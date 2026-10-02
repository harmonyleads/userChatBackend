"""Supabase connection pool. Statement cache is off for the transaction pooler."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote

import asyncpg

from app.config import DatabaseSettings

logger = logging.getLogger(__name__)


def parse_postgres_dsn(dsn: str) -> dict[str, str | int]:
    """Split a postgres URL without treating brackets in the password as an IPv6 host."""
    value = dsn.strip()
    if "://" not in value:
        raise ValueError("DATABASE_URL must start with postgresql://")
    _, remainder = value.split("://", 1)
    remainder = remainder.split("#", 1)[0]
    query = ""
    if "?" in remainder:
        remainder, query = remainder.split("?", 1)
    if "/" in remainder:
        authority, database = remainder.split("/", 1)
    else:
        authority, database = remainder, "postgres"
    if "@" not in authority:
        raise ValueError("DATABASE_URL must include a username and host.")
    userinfo, hostport = authority.rsplit("@", 1)
    if ":" in userinfo:
        user, password = userinfo.split(":", 1)
    else:
        user, password = userinfo, ""
    if hostport.startswith("[") and "]" in hostport:
        host, _, tail = hostport[1:].partition("]")
        port = int(tail[1:]) if tail.startswith(":") and tail[1:].isdigit() else 5432
    elif hostport.count(":") == 1:
        host, port_text = hostport.rsplit(":", 1)
        port = int(port_text)
    else:
        host, port = hostport, 5432
    params = dict(parse_qsl(query, keep_blank_values=True))
    return {
        "user": unquote(user),
        "password": unquote(password),
        "host": host,
        "port": port,
        "database": database or "postgres",
        "sslmode": params.get("sslmode", ""),
    }


def strip_sslmode(dsn: str) -> str:
    parsed = parse_postgres_dsn(dsn)
    user = quote(str(parsed["user"]), safe="")
    password = quote(str(parsed["password"]), safe="")
    auth = f"{user}:{password}" if password else user
    return (
        f"postgresql://{auth}@{parsed['host']}:{parsed['port']}/{parsed['database']}"
    )


def ssl_argument(settings: DatabaseSettings, sslmode: str) -> str | bool:
    """Encrypt like Supabase sslmode=require. Full verification rejects the pooler chain."""
    mode = sslmode.lower()
    if mode == "disable" or not settings.ssl:
        return False
    if mode in {"allow", "prefer", "require", "verify-ca", "verify-full"}:
        return mode
    return "require"


def connection_params(settings: DatabaseSettings) -> dict[str, str | int]:
    """Prefer DATABASE_HOST and the other separate fields over DATABASE_URL."""
    if settings.host:
        return {
            "user": settings.user,
            "password": settings.password,
            "host": settings.host,
            "port": settings.port,
            "database": settings.database_name or "postgres",
            "sslmode": "",
        }
    return parse_postgres_dsn(settings.dsn)


async def create_pool(settings: DatabaseSettings) -> asyncpg.Pool:
    parsed = connection_params(settings)
    kwargs: dict = {
        "host": parsed["host"],
        "port": parsed["port"],
        "user": parsed["user"],
        "password": parsed["password"],
        "database": parsed["database"],
        "min_size": settings.min_pool_size,
        "max_size": settings.max_pool_size,
        "command_timeout": settings.command_timeout_seconds,
        "statement_cache_size": 0,
        "max_inactive_connection_lifetime": settings.max_inactive_connection_lifetime_seconds,
        "server_settings": {"application_name": "userChatBackend"},
    }
    kwargs["ssl"] = ssl_argument(settings, str(parsed["sslmode"]))
    pool = await asyncpg.create_pool(**kwargs)
    if pool is None:
        raise RuntimeError("Database pool was not created.")
    return pool


async def create_pool_with_retry(settings: DatabaseSettings) -> asyncpg.Pool:
    attempts = max(1, settings.startup_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await create_pool(settings)
        except Exception as exc:
            last_error = exc
            logger.warning("database_connect_failed attempt=%s", attempt)
            if attempt == attempts:
                break
            await asyncio.sleep(settings.startup_retry_seconds)
    assert last_error is not None
    raise last_error


def split_sql(script: str) -> list[str]:
    lines = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    text = "\n".join(lines)
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    in_dollar = False
    while index < len(text):
        if text.startswith("$$", index):
            in_dollar = not in_dollar
            buffer.append("$$")
            index += 2
            continue
        character = text[index]
        if character == ";" and not in_dollar:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(character)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


async def apply_schema(pool: asyncpg.Pool, schema_path: Path) -> None:
    statements = split_sql(schema_path.read_text(encoding="utf-8"))
    async with pool.acquire() as conn:
        for statement in statements:
            await conn.execute(statement)
    logger.info("database_schema_applied statements=%s", len(statements))
