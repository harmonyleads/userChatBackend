import json

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.services.sarah import SarahClient, resolve_sarah_auth_code


def _settings() -> Settings:
    settings = Settings()
    settings.sarah.default_auth_code = "demo-code"
    settings.sarah.allowed_auth_codes = ["demo-code", "other-code"]
    settings.sarah.max_retries = 1
    settings.sarah.timeout_seconds = 5
    settings.sarah.connect_timeout_seconds = 1
    return settings


def test_rejects_path_injection() -> None:
    with pytest.raises(AppError) as caught:
        resolve_sarah_auth_code(_settings(), "../admin")
    assert caught.value.message == "sarahAuthCode is invalid"


def test_rejects_code_outside_allow_list() -> None:
    with pytest.raises(AppError) as caught:
        resolve_sarah_auth_code(_settings(), "not-allowed")
    assert caught.value.message == "sarahAuthCode is not allowed"


def test_uses_configured_default_when_omitted() -> None:
    assert resolve_sarah_auth_code(_settings(), None) == "demo-code"


@pytest.mark.asyncio
async def test_reads_answer_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/public/hclp/demo-code/sarah/ask"
        assert json.loads(request.content) == {"question": "What is Helix?"}
        return httpx.Response(200, json={"answer_text": "Helix is a platform."})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        outcome = await SarahClient(_settings(), http).ask(
            question="What is Helix?",
            auth_code="demo-code",
        )
    assert outcome.status == "SUCCESS"
    assert outcome.answer == "Helix is a platform."
    assert outcome.response_body["answer_text"] == "Helix is a platform."


@pytest.mark.asyncio
async def test_timeout_is_not_retried() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("slow")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        outcome = await SarahClient(_settings(), http).ask(question="Hi", auth_code="demo-code")
    assert outcome.status == "TIMEOUT"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_retries_bad_gateway_once() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(502, json={"error": "bad gateway"})
        return httpx.Response(200, json={"answer_text": "Recovered"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        outcome = await SarahClient(_settings(), http).ask(question="Hi", auth_code="demo-code")
    assert outcome.status == "SUCCESS"
    assert outcome.answer == "Recovered"
    assert calls["count"] == 2
