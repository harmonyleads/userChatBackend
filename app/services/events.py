"""Payloads written to Postgres. Identifiers are generated once and reused on retry."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cap_json(value: Any, max_bytes: int) -> Any:
    if value is None:
        return None
    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    preview = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return {"truncated": True, "preview": preview}


def build_initialize_event(
    *,
    user_id: str | None,
    page_id: str,
    session_id: str,
    page_url: str | None,
    page_title: str | None,
    asset_id: str | None,
    asset_name: str | None,
    asset_type: str | None,
    asset_version: str | None,
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "user_id": user_id,
        "page_id": page_id,
        "session_id": session_id,
        "page_url": page_url,
        "page_title": page_title,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "asset_version": asset_version,
        "visited_at": utc_now(),
    }


def build_answer_event(
    *,
    user_id: str | None,
    page_id: str,
    session_id: str,
    page_url: str | None,
    asset_id: str | None,
    asset_name: str | None,
    asset_type: str | None,
    asset_version: str | None,
    page_title: str | None,
    question: str,
    answer: str | None,
    status: str,
    sarah_request: dict[str, Any],
    sarah_response: Any,
    sarah_latency_ms: int,
    created_at: str,
    completed_at: str,
    max_response_bytes: int,
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "user_id": user_id,
        "page_id": page_id,
        "session_id": session_id,
        "page_url": page_url,
        "page_title": page_title,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "asset_version": asset_version,
        "question": question,
        "answer": answer,
        "status": status,
        "sarah_request": cap_json(sarah_request, max_response_bytes),
        "sarah_response": cap_json(sarah_response, max_response_bytes),
        "sarah_latency_ms": sarah_latency_ms,
        "created_at": created_at,
        "completed_at": completed_at,
    }


def build_page_exit_event(
    *,
    user_id: str | None,
    page_id: str,
    session_id: str,
    page_url: str | None,
    page_title: str | None,
    asset_id: str | None,
    asset_name: str | None,
    asset_type: str | None,
    asset_version: str | None,
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "user_id": user_id,
        "page_id": page_id,
        "session_id": session_id,
        "page_url": page_url,
        "page_title": page_title,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "asset_version": asset_version,
        "left_at": utc_now(),
    }
