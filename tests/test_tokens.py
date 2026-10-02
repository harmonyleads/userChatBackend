import time

import pytest

from app.auth.tokens import decode_session_token, issue_session_token
from app.errors import AppError

SECRET = "test-signing-secret-value"


def _claims(exp_offset: int = 60) -> dict:
    now = int(time.time())
    return {
        "v": 1,
        "user_id": "user-1",
        "session_id": "session-1",
        "page_id": "page-1",
        "iat": now,
        "exp": now + exp_offset,
    }


def test_round_trip() -> None:
    token = issue_session_token(_claims(), SECRET)
    decoded = decode_session_token(token, SECRET, clock_skew_seconds=0)
    assert decoded["user_id"] == "user-1"
    assert decoded["page_id"] == "page-1"


def test_tampered_signature_is_rejected() -> None:
    token = issue_session_token(_claims(), SECRET)
    body, _signature = token.split(".", 1)
    with pytest.raises(AppError) as caught:
        decode_session_token(f"{body}.{'0' * 64}", SECRET, clock_skew_seconds=0)
    assert caught.value.status_code == 401
    assert caught.value.message == "Invalid session token"


def test_expired_token_can_be_allowed_for_page_exit() -> None:
    token = issue_session_token(_claims(exp_offset=-120), SECRET)
    with pytest.raises(AppError) as caught:
        decode_session_token(token, SECRET, clock_skew_seconds=0)
    assert caught.value.message == "Session expired"
    decoded = decode_session_token(token, SECRET, clock_skew_seconds=0, allow_expired=True)
    assert decoded["session_id"] == "session-1"
