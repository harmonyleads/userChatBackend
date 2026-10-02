from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app, start_services, stop_services
from app.services.sarah import SarahOutcome

WIDGET_KEY = "test-widget-key-123"
SIGNING_SECRET = "test-signing-secret-value"


def build_test_settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.auth.widget_keys = [WIDGET_KEY]
    settings.auth.signing_secret = SIGNING_SECRET
    settings.auth.allowed_origins = ["*"]
    settings.auth.allow_missing_origin = True
    settings.auth.rate_limit_requests = 1000
    settings.database.enabled = False
    settings.sarah.default_auth_code = "demo-code"
    settings.sarah.allowed_auth_codes = ["demo-code"]
    settings.persistence.wal_path = str(tmp_path / "wal.json")
    settings.persistence.initial_backoff_seconds = 0
    settings.persistence.max_backoff_seconds = 0
    settings.persistence.max_attempts = 3
    settings.persistence.request_wait_timeout_seconds = 5
    settings.persistence.shutdown_drain_seconds = 2
    settings.persistence.worker_count = 1
    settings.app.docs_enabled = False
    return settings


class FakeSarah:
    def __init__(self, outcome: SarahOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    async def ask(self, *, question: str, auth_code: str) -> SarahOutcome:
        self.calls.append({"question": question, "auth_code": auth_code})
        return self.outcome


def success_outcome() -> SarahOutcome:
    return SarahOutcome(
        status="SUCCESS",
        answer="Helix is a platform.",
        error_message=None,
        url="https://staging.cosellus.ai/api/v1/public/hclp/demo-code/sarah/ask",
        request_body={"question": "What is Helix?"},
        response_body={
            "question_id": "q_public_ask",
            "answer_text": "Helix is a platform.",
            "is_grounded": True,
        },
        latency_ms=12,
    )


@pytest.fixture
async def api(tmp_path: Path):
    sarah = FakeSarah(success_outcome())
    application = create_app(build_test_settings(tmp_path), sarah=sarah)
    await start_services(application)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, application, sarah
    await stop_services(application)
