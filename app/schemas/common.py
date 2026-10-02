"""Shared request parsing."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import LimitSettings
from app.errors import AppError

T = TypeVar("T", bound=BaseModel)

_NUMERIC_AS_STRING = {"user_id", "page_id", "session_id", "asset_id", "asset_version"}


def normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if key in _NUMERIC_AS_STRING and isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str):
            value = value.strip()
            if value == "" and key != "question":
                value = None
        normalized[key] = value
    return normalized


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")
        return normalize_payload(data)


def reject_long_fields(data: dict[str, Any], limits: LimitSettings) -> None:
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        limit = limits.max_question_chars if key == "question" else limits.max_field_chars
        if len(value) > limit:
            raise AppError(400, f"{key} exceeds the maximum length of {limit} characters")


def humanize_validation(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        message = error["msg"]
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        messages.append(message)
    return "; ".join(messages) or "Invalid request"


def parse_model(model_type: type[T], data: dict[str, Any]) -> T:
    try:
        return model_type.model_validate(model_type._normalize(data))
    except ValidationError as exc:
        raise AppError(400, humanize_validation(exc)) from None
    except ValueError as exc:
        raise AppError(400, str(exc)) from None
