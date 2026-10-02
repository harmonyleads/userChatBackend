"""JSON body and widget-key dependencies."""

from __future__ import annotations

import json

from fastapi import Depends, Request

from app.auth.guard import enforce_widget
from app.errors import AppError
from app.schemas.common import reject_long_fields


async def read_json_body(request: Request) -> dict:
    settings = request.app.state.settings
    raw = await request.body()
    if len(raw) > settings.limits.max_body_bytes:
        raise AppError(413, "Request body is too large")
    if not raw or not raw.strip():
        data: object = {}
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise AppError(400, "Request body must be JSON") from None
    if not isinstance(data, dict):
        raise AppError(400, "Request body must be a JSON object")
    reject_long_fields(data, settings.limits)
    return data


async def require_widget(request: Request, body: dict = Depends(read_json_body)) -> dict:
    enforce_widget(request, body)
    return body
