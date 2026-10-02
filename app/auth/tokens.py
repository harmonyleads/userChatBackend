"""Signed session tokens for a public page that cannot store a password.

The page receives a token from /initialize and sends it back on later calls.
The signing secret stays on the server. A stolen page script can mint nothing
without first calling /initialize, and each token is bound to one user, session,
and page.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.errors import AppError


def secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def issue_session_token(claims: dict[str, Any], secret: str) -> str:
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_session_token(
    token: str,
    secret: str,
    clock_skew_seconds: int,
    *,
    allow_expired: bool = False,
) -> dict[str, Any]:
    if not isinstance(token, str) or "." not in token:
        raise AppError(401, "Invalid session token")
    body, signature = token.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not secure_equal(signature, expected):
        raise AppError(401, "Invalid session token")
    padding = "=" * (-len(body) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(body + padding))
    except (ValueError, json.JSONDecodeError):
        raise AppError(401, "Invalid session token") from None
    if not isinstance(data, dict):
        raise AppError(401, "Invalid session token")
    if data.get("v") != 1:
        raise AppError(401, "Invalid session token")
    for field in ("user_id", "session_id", "page_id", "exp"):
        if field not in data:
            raise AppError(401, "Invalid session token")
    try:
        expires_at = float(data["exp"])
    except (TypeError, ValueError):
        raise AppError(401, "Invalid session token") from None
    if expires_at + clock_skew_seconds < time.time() and not allow_expired:
        raise AppError(401, "Session expired")
    return data
