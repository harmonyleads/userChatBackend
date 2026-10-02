"""Widget key, origin allow-list, and session checks for the public chat page."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.auth.rate_limit import SlidingWindowRateLimiter
from app.auth.tokens import decode_session_token, secure_equal
from app.config import Settings
from app.errors import AppError


def _presented_widget_key(request: Request, body: dict) -> str:
    header = request.headers.get("x-widget-key")
    if isinstance(header, str) and header.strip():
        return header.strip()
    body_key = body.get("widget_key")
    if isinstance(body_key, str) and body_key.strip():
        return body_key.strip()
    return ""


def widget_key_accepted(presented: str, keys: list[str]) -> bool:
    if not presented:
        return False
    matched = False
    for key in keys:
        matched = secure_equal(presented, key) or matched
    return matched


def enforce_origin(settings: Settings, request: Request) -> None:
    allowed = [origin.rstrip("/") for origin in settings.auth.allowed_origins if origin.strip()]
    if "*" in allowed:
        return
    candidate = request.headers.get("origin")
    if candidate:
        candidate = candidate.strip()
    if not candidate:
        referer = request.headers.get("referer")
        if referer:
            parts = urlsplit(referer.strip())
            if parts.scheme and parts.netloc:
                candidate = f"{parts.scheme}://{parts.netloc}"
    if not candidate:
        if settings.auth.allow_missing_origin:
            return
        raise AppError(403, "Origin is required")
    if candidate.rstrip("/") not in allowed:
        raise AppError(403, "Origin is not allowed")


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_widget(request: Request, body: dict) -> None:
    settings: Settings = request.app.state.settings
    enforce_origin(settings, request)
    limiter: SlidingWindowRateLimiter = request.app.state.rate_limiter
    limiter.hit(client_ip(request, settings.auth.trust_proxy))
    presented = _presented_widget_key(request, body)
    if not widget_key_accepted(presented, settings.auth.widget_keys):
        raise AppError(401, "Unauthorized")


def verify_session(
    request: Request,
    body: dict,
    *,
    user_id: str | None,
    session_id: str | None,
    page_id: str,
    allow_expired: bool,
) -> dict:
    settings: Settings = request.app.state.settings
    header = request.headers.get("x-session-token")
    token = header.strip() if isinstance(header, str) and header.strip() else ""
    if not token:
        raw_token = body.get("session_token")
        if isinstance(raw_token, str):
            token = raw_token.strip()
    if not token:
        raise AppError(401, "Session token is required")
    claims = decode_session_token(
        token,
        settings.auth.signing_secret,
        settings.auth.clock_skew_seconds,
        allow_expired=allow_expired,
    )
    if not secure_equal(str(claims["user_id"]), user_id or ""):
        raise AppError(401, "Session does not match user_id")
    if session_id and not secure_equal(str(claims["session_id"]), session_id):
        raise AppError(401, "Session does not match session_id")
    if not secure_equal(str(claims["page_id"]), page_id):
        raise AppError(401, "Session does not match this page")
    return claims
