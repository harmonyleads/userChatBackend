"""Postgres and in-memory storage. Inserts are idempotent on the event id."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg


class VisitNotReady(Exception):
    """Page exit arrived before the matching visit was stored."""


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


class InMemoryRepository:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.pages: dict[str, dict[str, Any]] = {}
        self.visits: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def _touch_user(self, user_id: str | None) -> None:
        if not user_id:
            return
        now = datetime.now(timezone.utc)
        existing = self.users.get(user_id)
        if existing is None:
            self.users[user_id] = {
                "user_id": user_id,
                "created_at": now,
                "last_seen_at": now,
            }
            return
        existing["last_seen_at"] = now

    def _upsert_page(self, payload: dict[str, Any]) -> None:
        page_id = payload["page_id"]
        existing = self.pages.get(page_id)
        if existing is None:
            existing = {"page_id": page_id, "created_at": datetime.now(timezone.utc)}
        for field in ("page_url", "page_title", "asset_id", "asset_name", "asset_type", "asset_version"):
            if payload.get(field):
                existing[field] = payload[field]
        self.pages[page_id] = existing

    async def record_initialize(self, payload: dict[str, Any]) -> None:
        if any(visit["id"] == payload["id"] for visit in self.visits):
            return
        self._touch_user(payload["user_id"])
        self._upsert_page(payload)
        self.visits.append(
            {
                "id": payload["id"],
                "user_id": payload["user_id"],
                "page_id": payload["page_id"],
                "session_id": payload["session_id"],
                "visited_at": _parse_dt(payload["visited_at"]),
                "left_at": None,
                "duration_seconds": None,
            }
        )

    async def record_answer(self, payload: dict[str, Any]) -> None:
        if any(message["id"] == payload["id"] for message in self.messages):
            return
        self._touch_user(payload["user_id"])
        self._upsert_page(payload)
        self.messages.append(dict(payload))

    async def record_page_exit(self, payload: dict[str, Any], allow_placeholder: bool) -> None:
        self._touch_user(payload["user_id"])
        self._upsert_page(payload)
        if any(visit["id"] == payload["id"] for visit in self.visits):
            return
        left_at = _parse_dt(payload["left_at"])
        open_visits = [
            visit
            for visit in self.visits
            if visit["user_id"] == payload["user_id"]
            and visit["session_id"] == payload["session_id"]
            and visit["page_id"] == payload["page_id"]
            and visit["left_at"] is None
        ]
        if open_visits:
            for visit in open_visits:
                visit["left_at"] = left_at
                visit["duration_seconds"] = max(
                    0, int((left_at - visit["visited_at"]).total_seconds())
                )
            return
        already = any(
            visit["user_id"] == payload["user_id"]
            and visit["session_id"] == payload["session_id"]
            and visit["page_id"] == payload["page_id"]
            for visit in self.visits
        )
        if already:
            return
        if not allow_placeholder:
            raise VisitNotReady()
        self.visits.append(
            {
                "id": payload["id"],
                "user_id": payload["user_id"],
                "page_id": payload["page_id"],
                "session_id": payload["session_id"],
                "visited_at": left_at,
                "left_at": left_at,
                "duration_seconds": 0,
            }
        )


class PostgresRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_initialize(self, payload: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await _remember_user(conn, payload.get("user_id"))
                await _upsert_page(conn, payload)
                await conn.execute(
                    """
                    INSERT INTO page_visits (
                        id, user_id, page_id, session_id, visited_at
                    ) VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    _uuid(payload["id"]),
                    payload.get("user_id"),
                    payload["page_id"],
                    payload["session_id"],
                    _parse_dt(payload["visited_at"]),
                )

    async def record_answer(self, payload: dict[str, Any]) -> None:
        response = payload.get("sarah_response")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await _remember_user(conn, payload.get("user_id"))
                await _upsert_page(conn, payload)
                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, user_id, session_id, page_id, page_url, asset_id, asset_name,
                        question, answer, sarah_request, sarah_response,
                        sarah_latency_ms, created_at, completed_at, status
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10::jsonb, $11::jsonb,
                        $12, $13, $14, $15
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    _uuid(payload["id"]),
                    payload.get("user_id"),
                    payload.get("session_id"),
                    payload.get("page_id"),
                    payload.get("page_url"),
                    payload.get("asset_id"),
                    payload.get("asset_name"),
                    payload["question"],
                    payload.get("answer"),
                    json.dumps(payload.get("sarah_request")),
                    None if response is None else json.dumps(response),
                    payload.get("sarah_latency_ms"),
                    _parse_dt(payload["created_at"]),
                    _parse_dt(payload["completed_at"]) if payload.get("completed_at") else None,
                    payload["status"],
                )

    async def record_page_exit(self, payload: dict[str, Any], allow_placeholder: bool) -> None:
        left_at = _parse_dt(payload["left_at"])
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await _remember_user(conn, payload.get("user_id"))
                await _upsert_page(conn, payload)
                existing_exit = await conn.fetchval(
                    "SELECT 1 FROM page_visits WHERE id = $1",
                    _uuid(payload["id"]),
                )
                if existing_exit:
                    return
                result = await conn.execute(
                    """
                    UPDATE page_visits
                    SET left_at = $4,
                        duration_seconds = GREATEST(
                            0,
                            FLOOR(EXTRACT(EPOCH FROM ($4 - visited_at)))::int
                        )
                    WHERE user_id IS NOT DISTINCT FROM $1
                      AND session_id = $2
                      AND page_id = $3
                      AND left_at IS NULL
                    """,
                    payload.get("user_id"),
                    payload["session_id"],
                    payload["page_id"],
                    left_at,
                )
                if int(result.split()[-1]) > 0:
                    return
                existing = await conn.fetchval(
                    """
                    SELECT 1 FROM page_visits
                    WHERE user_id IS NOT DISTINCT FROM $1
                      AND session_id = $2
                      AND page_id = $3
                    LIMIT 1
                    """,
                    payload.get("user_id"),
                    payload["session_id"],
                    payload["page_id"],
                )
                if existing:
                    return
                if not allow_placeholder:
                    raise VisitNotReady()
                await conn.execute(
                    """
                    INSERT INTO page_visits (
                        id, user_id, page_id, session_id,
                        visited_at, left_at, duration_seconds
                    ) VALUES ($1, $2, $3, $4, $5, $5, 0)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    _uuid(payload["id"]),
                    payload.get("user_id"),
                    payload["page_id"],
                    payload["session_id"],
                    left_at,
                )


async def _remember_user(conn: asyncpg.Connection, user_id: str | None) -> None:
    if user_id:
        await _upsert_user(conn, user_id)


async def _upsert_user(conn: asyncpg.Connection, user_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO users (user_id)
        VALUES ($1)
        ON CONFLICT (user_id)
        DO UPDATE SET last_seen_at = now()
        """,
        user_id,
    )


async def _upsert_page(conn: asyncpg.Connection, payload: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO pages (
            page_id, page_url, page_title, asset_id, asset_name, asset_type, asset_version
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (page_id) DO UPDATE SET
            page_url = COALESCE(EXCLUDED.page_url, pages.page_url),
            page_title = COALESCE(EXCLUDED.page_title, pages.page_title),
            asset_id = COALESCE(EXCLUDED.asset_id, pages.asset_id),
            asset_name = COALESCE(EXCLUDED.asset_name, pages.asset_name),
            asset_type = COALESCE(EXCLUDED.asset_type, pages.asset_type),
            asset_version = COALESCE(EXCLUDED.asset_version, pages.asset_version)
        """,
        payload["page_id"],
        payload.get("page_url"),
        payload.get("page_title"),
        payload.get("asset_id"),
        payload.get("asset_name"),
        payload.get("asset_type"),
        payload.get("asset_version"),
    )
