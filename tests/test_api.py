import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.auth.tokens import issue_session_token
from app.db.repository import InMemoryRepository
from app.main import create_app, start_services, stop_services
from app.services.sarah import SarahOutcome
from tests.conftest import WIDGET_KEY, FakeSarah, build_test_settings, success_outcome

import time


def auth_headers(token: str | None = None, origin: str | None = None) -> dict[str, str]:
    headers = {"X-Widget-Key": WIDGET_KEY}
    if token:
        headers["X-Session-Token"] = token
    if origin:
        headers["Origin"] = origin
    return headers


def page_body(**overrides) -> dict:
    body = {
        "user_id": "user-1",
        "page_url": "https://docs.example/helix",
        "page_title": "Helix",
        "asset_type": "pdf",
        "asset_name": "Helix_Embed_FAQ.pdf",
        "asset_version": "1.0",
    }
    body.update(overrides)
    return body


async def test_health(api) -> None:
    client, _app, _sarah = api
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert response.headers["cache-control"] == "no-store"


async def test_initialize_user_is_optional_and_page_identity_is_required(api) -> None:
    client, app, _sarah = api
    missing_page = await client.post(
        "/initialize",
        headers=auth_headers(),
        json={"user_id": "user-1", "page_title": "Helix", "session_id": "session-1"},
    )
    assert missing_page.status_code == 400
    assert missing_page.json()["status"] == "ERROR"
    assert missing_page.json()["errorMessage"] == "page_url, asset_id, or asset_name is required"

    anonymous = await client.post(
        "/initialize",
        headers=auth_headers(),
        json={"page_url": "https://docs.example/anon", "asset_type": "pdf"},
    )
    assert anonymous.status_code == 200
    session = anonymous.json()
    assert app.state.repository.users == {}
    assert app.state.repository.visits[0]["user_id"] is None

    answered = await client.post(
        "/get-answers",
        headers=auth_headers(session["session_token"]),
        json={"page_url": "https://docs.example/anon", "question": "What is Helix?"},
    )
    assert answered.status_code == 200
    assert answered.json()["status"] == "SUCCESS"
    assert app.state.repository.users == {}
    assert app.state.repository.messages[0]["user_id"] is None


async def test_initialize_stores_user_page_and_visit(api) -> None:
    client, app, _sarah = api
    response = await client.post(
        "/initialize",
        headers=auth_headers(),
        json=page_body(user_id=42, asset_version=1, session_id="session-7"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["session_id"] == "session-7"
    assert body["page_id"]
    assert body["session_token"]

    repository = app.state.repository
    assert "42" in repository.users
    assert repository.pages[body["page_id"]]["page_title"] == "Helix"
    assert repository.pages[body["page_id"]]["asset_name"] == "Helix_Embed_FAQ.pdf"
    assert repository.pages[body["page_id"]]["asset_type"] == "pdf"
    assert repository.pages[body["page_id"]]["asset_version"] == "1"
    assert repository.visits[0]["session_id"] == "session-7"
    assert repository.visits[0]["left_at"] is None


async def test_get_answers_returns_and_stores_sarah_exchange(api) -> None:
    client, app, sarah = api
    started = await client.post("/initialize", headers=auth_headers(), json=page_body())
    session = started.json()

    response = await client.post(
        "/get-answers",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "page_id": session["page_id"],
            "session_id": session["session_id"],
            "page_url": "https://docs.example/helix",
            "asset_name": "Helix_Embed_FAQ.pdf",
            "sarahAuthCode": "demo-code",
            "question": "What is Helix?",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"answer": "Helix is a platform.", "status": "SUCCESS"}
    assert sarah.calls == [{"question": "What is Helix?", "auth_code": "demo-code"}]

    message = app.state.repository.messages[0]
    assert message["question"] == "What is Helix?"
    assert message["answer"] == "Helix is a platform."
    assert message["status"] == "SUCCESS"
    assert message["page_id"] == session["page_id"]
    assert message["sarah_request"]["body"] == {"question": "What is Helix?"}
    assert message["sarah_response"]["answer_text"] == "Helix is a platform."
    assert message["sarah_latency_ms"] == 12


async def test_same_page_url_matches_the_initialized_session(api) -> None:
    client, _app, _sarah = api
    started = await client.post(
        "/initialize",
        headers=auth_headers(),
        json={"user_id": "user-1", "page_url": "https://docs.example/helix"},
    )
    session = started.json()
    response = await client.post(
        "/get-answers",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "session_id": session["session_id"],
            "page_url": "https://docs.example/helix/",
            "question": "What is Helix?",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"


async def test_rejects_missing_widget_key_and_wrong_page(api) -> None:
    client, _app, sarah = api
    unauthorized = await client.post("/initialize", json=page_body())
    assert unauthorized.status_code == 401

    started = await client.post("/initialize", headers=auth_headers(), json=page_body(page_id="page-1"))
    session = started.json()
    mismatch = await client.post(
        "/get-answers",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "page_id": "other-page",
            "session_id": session["session_id"],
            "page_url": "https://docs.example/other",
            "question": "What is Helix?",
        },
    )
    assert mismatch.status_code == 401
    assert mismatch.json()["errorMessage"] == "Session does not match this page"
    assert sarah.calls == []


async def test_page_exit_records_duration_and_is_idempotent(api) -> None:
    client, app, _sarah = api
    started = await client.post(
        "/initialize",
        headers=auth_headers(),
        json=page_body(session_id="session-9"),
    )
    session = started.json()
    first = await client.post(
        "/page_exit",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "page_id": session["page_id"],
            "session_id": "session-9",
            "page_url": "https://docs.example/helix",
        },
    )
    second = await client.post(
        "/page_exit",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "page_id": session["page_id"],
            "session_id": "session-9",
            "asset_name": "Helix_Embed_FAQ.pdf",
        },
    )
    assert first.status_code == 200
    assert second.status_code == 200
    visits = app.state.repository.visits
    assert len(visits) == 1
    assert visits[0]["left_at"] is not None
    assert visits[0]["duration_seconds"] >= 0


async def test_page_exit_accepts_beacon_body_and_expired_token(api) -> None:
    client, app, _sarah = api
    started = await client.post("/initialize", headers=auth_headers(), json=page_body())
    session = started.json()
    expired = issue_session_token(
        {
            "v": 1,
            "user_id": "user-1",
            "session_id": session["session_id"],
            "page_id": session["page_id"],
            "iat": int(time.time()) - 100,
            "exp": int(time.time()) - 30,
        },
        app.state.settings.auth.signing_secret,
    )
    response = await client.post(
        "/page_exit",
        content=json.dumps(
            {
                "widget_key": WIDGET_KEY,
                "session_token": expired,
                "user_id": "user-1",
                "page_id": session["page_id"],
                "session_id": session["session_id"],
                "asset_name": "Helix_Embed_FAQ.pdf",
            }
        ),
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 200
    assert app.state.repository.visits[0]["left_at"] is not None


async def test_answer_is_returned_when_storage_fails(tmp_path: Path) -> None:
    class AnswerFailRepository(InMemoryRepository):
        async def record_answer(self, payload: dict) -> None:
            raise RuntimeError("db down")

    settings = build_test_settings(tmp_path)
    sarah_outcome = success_outcome()

    class Sarah:
        async def ask(self, *, question: str, auth_code: str) -> SarahOutcome:
            return sarah_outcome

    application = create_app(settings, sarah=Sarah(), repository=AnswerFailRepository())
    await start_services(application)
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post("/initialize", headers=auth_headers(), json=page_body())
            session = started.json()
            response = await client.post(
                "/get-answers",
                headers=auth_headers(session["session_token"]),
                json={
                    "user_id": "user-1",
                    "page_id": session["page_id"],
                    "session_id": session["session_id"],
                    "page_url": "https://docs.example/helix",
                    "question": "What is Helix?",
                },
            )
    finally:
        await stop_services(application)

    assert response.status_code == 500
    assert response.json()["answer"] == "Helix is a platform."
    assert response.json()["status"] == "ERROR"
    assert response.json()["errorMessage"] == "The chat exchange could not be stored"
    dead_letter = Path(settings.persistence.wal_path).with_suffix(".deadletter.jsonl")
    assert "What is Helix?" in dead_letter.read_text(encoding="utf-8")


async def test_timeout_status_is_stored(api) -> None:
    client, app, sarah = api
    sarah.outcome = SarahOutcome(
        status="TIMEOUT",
        answer=None,
        error_message="The answer service timed out",
        url="https://staging.cosellus.ai/api/v1/public/hclp/demo-code/sarah/ask",
        request_body={"question": "slow"},
        response_body={"error": "timeout"},
        latency_ms=30000,
    )
    started = await client.post("/initialize", headers=auth_headers(), json=page_body())
    session = started.json()
    response = await client.post(
        "/get-answers",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "page_id": session["page_id"],
            "session_id": session["session_id"],
            "asset_name": "Helix_Embed_FAQ.pdf",
            "question": "slow",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "TIMEOUT"
    assert response.json()["errorMessage"] == "The answer service timed out"
    assert app.state.repository.messages[0]["status"] == "TIMEOUT"


async def test_origin_allow_list(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.auth.allowed_origins = ["https://chat.example"]
    settings.auth.allow_missing_origin = False
    application = create_app(settings, sarah=type("S", (), {"ask": static_ask})())
    await start_services(application)
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            blocked = await client.post(
                "/initialize",
                headers=auth_headers(origin="https://evil.example"),
                json=page_body(),
            )
            missing = await client.post("/initialize", headers=auth_headers(), json=page_body())
            allowed = await client.post(
                "/initialize",
                headers=auth_headers(origin="https://chat.example"),
                json=page_body(),
            )
    finally:
        await stop_services(application)
    assert blocked.status_code == 403
    assert missing.status_code == 403
    assert allowed.status_code == 200


async def static_ask(*, question: str, auth_code: str) -> SarahOutcome:
    return success_outcome()


async def test_rate_limit(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.auth.rate_limit_requests = 2
    application = create_app(settings, sarah=type("S", (), {"ask": static_ask})())
    await start_services(application)
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            statuses = []
            for _ in range(3):
                response = await client.post("/initialize", headers=auth_headers(), json=page_body())
                statuses.append(response.status_code)
    finally:
        await stop_services(application)
    assert statuses == [200, 200, 429]


async def test_analytics_is_not_implemented(api) -> None:
    client, _app, _sarah = api
    response = await client.post("/analytics", headers=auth_headers(), json={})
    assert response.status_code == 501
    assert response.json()["status"] == "ERROR"


async def test_disallowed_sarah_code_does_not_call_sarah(api) -> None:
    client, _app, sarah = api
    started = await client.post("/initialize", headers=auth_headers(), json=page_body())
    session = started.json()
    response = await client.post(
        "/get-answers",
        headers=auth_headers(session["session_token"]),
        json={
            "user_id": "user-1",
            "page_id": session["page_id"],
            "session_id": session["session_id"],
            "page_url": "https://docs.example/helix",
            "sarahAuthCode": "not-allowed",
            "question": "What is Helix?",
        },
    )
    assert response.status_code == 400
    assert response.json()["status"] == "ERROR"
    assert response.json()["errorMessage"] == "sarahAuthCode is not allowed"
    assert sarah.calls == []


async def test_failed_store_returns_error_status(tmp_path: Path) -> None:
    class BrokenRepository(InMemoryRepository):
        async def record_initialize(self, payload: dict) -> None:
            raise RuntimeError("db down")

    application = create_app(
        build_test_settings(tmp_path),
        sarah=FakeSarah(success_outcome()),
        repository=BrokenRepository(),
    )
    await start_services(application)
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/initialize", headers=auth_headers(), json=page_body())
    finally:
        await stop_services(application)
    assert response.status_code == 500
    assert response.json() == {
        "status": "ERROR",
        "errorMessage": "The page visit could not be stored",
    }


async def test_unknown_route_is_not_an_internal_error(api) -> None:
    client, _app, _sarah = api
    response = await client.get("/missing")
    assert response.status_code == 404
