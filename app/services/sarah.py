"""Synchronous client for the Sarah answer API."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.errors import AppError

logger = logging.getLogger(__name__)

_AUTH_CODE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RETRYABLE_STATUS = {502, 503}


@dataclass(frozen=True)
class SarahOutcome:
    status: str
    answer: str | None
    error_message: str | None
    url: str
    request_body: dict[str, Any]
    response_body: dict[str, Any] | None
    latency_ms: int


def _code_allowed(code: str, allowed: list[str]) -> bool:
    from app.auth.tokens import secure_equal

    matched = False
    for item in allowed:
        matched = secure_equal(code, item) or matched
    return matched


def resolve_sarah_auth_code(settings: Settings, provided: str | None) -> str:
    provided_code = (provided or "").strip()
    default_code = settings.sarah.default_auth_code.strip()
    code = provided_code or default_code
    if not code:
        raise AppError(400, "sarahAuthCode is required")
    if not _AUTH_CODE.fullmatch(code):
        raise AppError(400, "sarahAuthCode is invalid")
    allowed = [item.strip() for item in settings.sarah.allowed_auth_codes if item.strip()]
    if allowed:
        if not _code_allowed(code, allowed):
            raise AppError(400, "sarahAuthCode is not allowed")
        return code
    if default_code:
        if not _code_allowed(code, [default_code]):
            raise AppError(400, "sarahAuthCode is not allowed")
        return code
    if settings.sarah.allow_unlisted_auth_codes:
        return code
    raise AppError(400, "sarahAuthCode is not allowed")


def build_sarah_url(template: str, auth_code: str) -> str:
    return template.replace("{sarah_auth_code}", auth_code)


class SarahClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def ask(self, *, question: str, auth_code: str) -> SarahOutcome:
        url = build_sarah_url(self._settings.sarah.url_template, auth_code)
        request_body = {"question": question}
        started = time.perf_counter()
        attempt = 0
        response: httpx.Response | None = None
        while True:
            attempt += 1
            try:
                response = await self._http.post(
                    url,
                    json=request_body,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "userChatBackend/1.0",
                    },
                )
            except httpx.TimeoutException:
                return self._finish(
                    status="TIMEOUT",
                    answer=None,
                    error_message="The answer service timed out",
                    url=url,
                    request_body=request_body,
                    response_body={"error": "timeout"},
                    started=started,
                )
            except httpx.RequestError as exc:
                if attempt <= self._settings.sarah.max_retries:
                    logger.warning("sarah_connect_retry attempt=%s", attempt)
                    continue
                return self._finish(
                    status="ERROR",
                    answer=None,
                    error_message="The answer service is unavailable",
                    url=url,
                    request_body=request_body,
                    response_body={"error": exc.__class__.__name__},
                    started=started,
                )
            if response.status_code in _RETRYABLE_STATUS and attempt <= self._settings.sarah.max_retries:
                logger.warning(
                    "sarah_status_retry status_code=%s attempt=%s",
                    response.status_code,
                    attempt,
                )
                continue
            break

        assert response is not None
        latency_started = started
        if response.status_code == 504:
            return self._finish(
                status="TIMEOUT",
                answer=None,
                error_message="The answer service timed out",
                url=url,
                request_body=request_body,
                response_body={"http_status": 504, "body": _preview(response)},
                started=latency_started,
            )
        payload = _json_object(response)
        if response.status_code >= 400:
            message = "The answer service returned an error"
            if payload and payload.get("message"):
                message = str(payload["message"])[:500]
            elif payload and payload.get("error"):
                message = str(payload["error"])[:500]
            return self._finish(
                status="ERROR",
                answer=None,
                error_message=message,
                url=url,
                request_body=request_body,
                response_body=payload or {"http_status": response.status_code, "body": _preview(response)},
                started=latency_started,
            )
        answer = payload.get("answer_text") if payload else None
        if not isinstance(answer, str) or not answer.strip():
            return self._finish(
                status="ERROR",
                answer=None,
                error_message="The answer service did not return an answer",
                url=url,
                request_body=request_body,
                response_body=payload or {"http_status": response.status_code, "body": _preview(response)},
                started=latency_started,
            )
        return self._finish(
            status="SUCCESS",
            answer=answer,
            error_message=None,
            url=url,
            request_body=request_body,
            response_body=payload,
            started=latency_started,
        )

    def _finish(
        self,
        *,
        status: str,
        answer: str | None,
        error_message: str | None,
        url: str,
        request_body: dict[str, Any],
        response_body: dict[str, Any] | None,
        started: float,
    ) -> SarahOutcome:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info("sarah_ask status=%s latency_ms=%s", status, latency_ms)
        return SarahOutcome(
            status=status,
            answer=answer,
            error_message=error_message,
            url=url,
            request_body=request_body,
            response_body=response_body,
            latency_ms=latency_ms,
        )


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _preview(response: httpx.Response) -> str:
    return response.text[:4000]
