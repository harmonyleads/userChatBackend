"""Application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import SecurityHeadersMiddleware
from app.api.routes import build_router
from app.auth.rate_limit import SlidingWindowRateLimiter
from app.config import Settings, load_settings, validate_settings
from app.db.pool import apply_schema, create_pool_with_retry
from app.db.repository import InMemoryRepository, PostgresRepository
from app.logging_config import configure_logging
from app.services.persistence import PersistenceService
from app.services.sarah import SarahClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_services(app)
    try:
        yield
    finally:
        await stop_services(app)


async def start_services(app: FastAPI) -> None:
    if getattr(app.state, "services_started", False):
        return
    app.state.services_started = True
    settings: Settings = app.state.settings
    app.state.http = None
    if app.state.sarah_override is not None:
        app.state.sarah = app.state.sarah_override
    else:
        timeout = httpx.Timeout(
            settings.sarah.timeout_seconds,
            connect=settings.sarah.connect_timeout_seconds,
        )
        app.state.http = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        app.state.sarah = SarahClient(settings, app.state.http)

    app.state.pool = None
    if app.state.repository_override is not None:
        app.state.repository = app.state.repository_override
    elif settings.database.enabled:
        app.state.pool = await create_pool_with_retry(settings.database)
        if settings.database.auto_migrate:
            schema_path = Path(settings.database.schema_path)
            if not schema_path.is_file():
                schema_path = Path(__file__).resolve().parent.parent / settings.database.schema_path
            for script in sorted(schema_path.parent.glob("*.sql")):
                await apply_schema(app.state.pool, script)
        app.state.repository = PostgresRepository(app.state.pool)
    else:
        logger.warning("DATABASE_ENABLED is false. Events stay in memory for this process only.")
        app.state.repository = InMemoryRepository()

    app.state.rate_limiter = SlidingWindowRateLimiter(
        settings.auth.rate_limit_requests,
        settings.auth.rate_limit_window_seconds,
    )
    app.state.persistence = PersistenceService(settings, app.state.repository)
    await app.state.persistence.start()
    logger.info(
        "userChatBackend started wait_for_completion=%s",
        settings.persistence.wait_for_completion,
    )


async def stop_services(app: FastAPI) -> None:
    if getattr(app.state, "services_stopped", False):
        return
    app.state.services_stopped = True
    persistence = getattr(app.state, "persistence", None)
    if persistence is not None:
        await persistence.stop()
    http = getattr(app.state, "http", None)
    if http is not None:
        await http.aclose()
    pool = getattr(app.state, "pool", None)
    if pool is not None:
        await pool.close()


def create_app(
    settings: Settings | None = None,
    *,
    sarah=None,
    repository=None,
) -> FastAPI:
    resolved = settings or load_settings()
    validate_settings(resolved)
    configure_logging(resolved.app.log_level)
    app = FastAPI(
        title="userChatBackend",
        version=__version__,
        description="Backup API for the public chat page. Stores visits and proxies Sarah.",
        docs_url="/docs" if resolved.app.docs_enabled else None,
        redoc_url="/redoc" if resolved.app.docs_enabled else None,
        openapi_url="/openapi.json" if resolved.app.docs_enabled else None,
        lifespan=lifespan,
        redirect_slashes=False,
    )
    app.state.settings = resolved
    app.state.sarah_override = sarah
    app.state.repository_override = repository
    register_exception_handlers(app)
    app.include_router(build_router(), prefix=resolved.app.api_prefix)
    app.add_middleware(
        SecurityHeadersMiddleware,
        max_body_bytes=resolved.limits.max_body_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.auth.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )
    return app


def __getattr__(name: str):
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
