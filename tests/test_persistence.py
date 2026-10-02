import asyncio
import json
from pathlib import Path

from app.db.repository import InMemoryRepository
from app.services.events import build_initialize_event
from app.services.persistence import PersistenceService
from tests.conftest import build_test_settings


class FlakyRepository(InMemoryRepository):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures_remaining = failures
        self.attempts = 0

    async def record_initialize(self, payload: dict) -> None:
        self.attempts += 1
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("db down")
        await super().record_initialize(payload)


def _event() -> dict:
    return build_initialize_event(
        user_id="user-1",
        page_id="page-1",
        session_id="session-1",
        page_url="https://example.com/helix",
        page_title="Helix",
        asset_id="asset-1",
        asset_name="Helix.pdf",
        asset_type="pdf",
        asset_version="1",
    )


async def test_retries_then_stores(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    repository = FlakyRepository(failures=1)
    service = PersistenceService(settings, repository)
    await service.start()
    try:
        assert await service.save("initialize", _event()) is True
    finally:
        await service.stop()
    assert repository.attempts == 2
    assert len(repository.visits) == 1
    assert repository.users["user-1"]["user_id"] == "user-1"


async def test_dead_letter_keeps_the_payload(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    repository = FlakyRepository(failures=10)
    service = PersistenceService(settings, repository)
    event = _event()
    await service.start()
    try:
        assert await service.save("initialize", event) is False
    finally:
        await service.stop()
    dead_letter = Path(settings.persistence.wal_path).with_suffix(".deadletter.jsonl")
    text = dead_letter.read_text(encoding="utf-8")
    assert event["id"] in text
    assert "user-1" in text
    assert repository.visits == []


async def test_replays_write_ahead_log_on_startup(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    event = _event()
    wal = {
        "jobs": {
            event["id"]: {
                "id": event["id"],
                "kind": "initialize",
                "payload": event,
                "attempts": 0,
            }
        }
    }
    path = Path(settings.persistence.wal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wal), encoding="utf-8")
    repository = InMemoryRepository()
    service = PersistenceService(settings, repository)
    await service.start()
    try:
        for _ in range(50):
            if repository.visits:
                break
            await asyncio.sleep(0.02)
        assert len(repository.visits) == 1
    finally:
        await service.stop()


async def test_background_mode_returns_before_the_database_write(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.persistence.wait_for_completion = False
    repository = InMemoryRepository()
    service = PersistenceService(settings, repository)
    await service.start()
    try:
        assert await service.save("initialize", _event()) is True
        for _ in range(50):
            if repository.visits:
                break
            await asyncio.sleep(0.02)
        assert len(repository.visits) == 1
    finally:
        await service.stop()
