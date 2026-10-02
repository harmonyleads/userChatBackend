"""Load YAML defaults and let environment variables override secrets."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AppSettings(BaseModel):
    name: str = "userChatBackend"
    log_level: str = "INFO"
    docs_enabled: bool = True
    api_prefix: str = ""


class AuthSettings(BaseModel):
    widget_keys: list[str] = Field(default_factory=list)
    signing_secret: str = ""
    session_ttl_seconds: int = 43200
    clock_skew_seconds: int = 15
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_missing_origin: bool = False
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60
    trust_proxy: bool = True


class DatabaseSettings(BaseModel):
    enabled: bool = True
    host: str = ""
    port: int = 5432
    user: str = ""
    password: str = ""
    database_name: str = "postgres"
    # Optional. Used only when host, user, and password are not set separately.
    dsn: str = ""
    ssl: bool = True
    min_pool_size: int = 1
    max_pool_size: int = 5
    command_timeout_seconds: float = 15
    startup_retries: int = 5
    startup_retry_seconds: float = 2
    auto_migrate: bool = False
    schema_path: str = "sql/001_schema.sql"
    max_inactive_connection_lifetime_seconds: float = 300


class SarahSettings(BaseModel):
    url_template: str = (
        "https://staging.cosellus.ai/api/v1/public/hclp/{sarah_auth_code}/sarah/ask"
    )
    default_auth_code: str = ""
    allowed_auth_codes: list[str] = Field(default_factory=list)
    allow_unlisted_auth_codes: bool = False
    timeout_seconds: float = 30
    connect_timeout_seconds: float = 5
    max_retries: int = 1


class PersistenceSettings(BaseModel):
    wait_for_completion: bool = True
    request_wait_timeout_seconds: float = 25
    wal_path: str = "data/wal.json"
    max_attempts: int = 5
    initial_backoff_seconds: float = 0.4
    max_backoff_seconds: float = 5
    shutdown_drain_seconds: float = 8
    worker_count: int = 2
    max_response_bytes: int = 200_000


class LimitSettings(BaseModel):
    max_body_bytes: int = 1_048_576
    max_question_chars: int = 8000
    max_field_chars: int = 4000


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    sarah: SarahSettings = Field(default_factory=SarahSettings)
    persistence: PersistenceSettings = Field(default_factory=PersistenceSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _csv(name: str) -> list[str] | None:
    if name not in os.environ:
        return None
    return [part.strip() for part in os.environ[name].split(",") if part.strip()]


def _flag(name: str) -> bool | None:
    if name not in os.environ:
        return None
    return os.environ[name].strip().lower() in {"1", "true", "yes", "on"}


def _text(name: str) -> str | None:
    if name not in os.environ:
        return None
    return os.environ[name]


def _int(name: str) -> int | None:
    if name not in os.environ or os.environ[name].strip() == "":
        return None
    return int(os.environ[name])


def apply_env(settings: Settings) -> Settings:
    data = settings.model_dump()

    widget_keys = _csv("WIDGET_KEYS")
    if widget_keys is not None:
        data["auth"]["widget_keys"] = widget_keys
    signing_secret = _text("AUTH_SIGNING_SECRET")
    if signing_secret is not None:
        data["auth"]["signing_secret"] = signing_secret
    origins = _csv("AUTH_ALLOWED_ORIGINS")
    if origins is not None:
        data["auth"]["allowed_origins"] = origins
    allow_missing = _flag("AUTH_ALLOW_MISSING_ORIGIN")
    if allow_missing is not None:
        data["auth"]["allow_missing_origin"] = allow_missing

    database_host = _text("DATABASE_HOST")
    if database_host is not None:
        data["database"]["host"] = database_host
    database_port = _int("DATABASE_PORT")
    if database_port is not None:
        data["database"]["port"] = database_port
    database_user = _text("DATABASE_USER")
    if database_user is not None:
        data["database"]["user"] = database_user
    database_password = _text("DATABASE_PASSWORD")
    if database_password is not None:
        data["database"]["password"] = database_password
    database_name = _text("DATABASE_NAME")
    if database_name is not None:
        data["database"]["database_name"] = database_name
    database_url = _text("DATABASE_URL")
    if database_url is not None:
        data["database"]["dsn"] = database_url
    database_enabled = _flag("DATABASE_ENABLED")
    if database_enabled is not None:
        data["database"]["enabled"] = database_enabled
    database_ssl = _flag("DATABASE_SSL")
    if database_ssl is not None:
        data["database"]["ssl"] = database_ssl
    auto_migrate = _flag("DATABASE_AUTO_MIGRATE")
    if auto_migrate is not None:
        data["database"]["auto_migrate"] = auto_migrate

    sarah_url = _text("SARAH_URL_TEMPLATE")
    if sarah_url is not None:
        data["sarah"]["url_template"] = sarah_url
    sarah_default = _text("SARAH_DEFAULT_AUTH_CODE")
    if sarah_default is not None:
        data["sarah"]["default_auth_code"] = sarah_default
    sarah_allowed = _csv("SARAH_ALLOWED_AUTH_CODES")
    if sarah_allowed is not None:
        data["sarah"]["allowed_auth_codes"] = sarah_allowed
    allow_unlisted = _flag("SARAH_ALLOW_UNLISTED_AUTH_CODES")
    if allow_unlisted is not None:
        data["sarah"]["allow_unlisted_auth_codes"] = allow_unlisted
    sarah_timeout = _text("SARAH_TIMEOUT_SECONDS")
    if sarah_timeout:
        data["sarah"]["timeout_seconds"] = float(sarah_timeout)

    log_level = _text("LOG_LEVEL")
    if log_level is not None:
        data["app"]["log_level"] = log_level
    docs_enabled = _flag("DOCS_ENABLED")
    if docs_enabled is not None:
        data["app"]["docs_enabled"] = docs_enabled

    wal_path = _text("PERSISTENCE_WAL_PATH")
    if wal_path is not None:
        data["persistence"]["wal_path"] = wal_path
    wait_for_completion = _flag("PERSISTENCE_WAIT_FOR_COMPLETION")
    if wait_for_completion is not None:
        data["persistence"]["wait_for_completion"] = wait_for_completion
    worker_count = _int("PERSISTENCE_WORKER_COUNT")
    if worker_count is not None:
        data["persistence"]["worker_count"] = worker_count

    return Settings.model_validate(data)


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv()
    path = config_path or Path(os.environ.get("CONFIG_PATH", PROJECT_ROOT / "config" / "settings.yaml"))
    raw: dict = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise RuntimeError(f"Configuration file must be a mapping: {path}")
        raw = loaded
    return apply_env(Settings.model_validate(raw))


def _database_is_configured(settings: Settings) -> bool:
    database = settings.database
    if database.host and database.user:
        return True
    return bool(database.dsn)


def validate_settings(settings: Settings) -> None:
    if not settings.auth.widget_keys:
        raise RuntimeError("Set WIDGET_KEYS to at least one publishable widget key.")
    if not settings.auth.signing_secret:
        raise RuntimeError("Set AUTH_SIGNING_SECRET to a long random secret.")
    if len(settings.auth.signing_secret) < 16:
        logger.warning("AUTH_SIGNING_SECRET should be at least 16 characters.")
    if any(len(key) < 16 for key in settings.auth.widget_keys):
        logger.warning("Each WIDGET_KEYS value should be at least 16 characters.")
    if not settings.sarah.url_template.startswith("https://"):
        raise RuntimeError("Sarah URL template must start with https://")
    if "{sarah_auth_code}" not in settings.sarah.url_template:
        raise RuntimeError("Sarah URL template must contain {sarah_auth_code}.")
    if settings.database.enabled and not _database_is_configured(settings):
        raise RuntimeError(
            "Set DATABASE_HOST, DATABASE_USER, and DATABASE_PASSWORD, "
            "or set DATABASE_URL. Set DATABASE_ENABLED=false for local tests."
        )
    if settings.persistence.max_attempts < 1:
        raise RuntimeError("persistence.max_attempts must be at least 1.")
    if settings.persistence.worker_count < 1:
        raise RuntimeError("persistence.worker_count must be at least 1.")
    if "*" in settings.auth.allowed_origins:
        logger.warning(
            "AUTH_ALLOWED_ORIGINS contains *. Restrict origins before production."
        )
