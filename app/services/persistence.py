"""Durable write queue.

Every event is appended to a local write-ahead log before it is acknowledged.
Workers retry Postgres with backoff. A failed event is appended to a dead-letter
file and logged as one JSON line so Cloud Logging still has the payload.

wait_for_completion keeps the HTTP request open until the write finishes, which
is required on Cloud Run when CPU is throttled after the response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.db.repository import VisitNotReady

logger = logging.getLogger(__name__)


class EventRepository(Protocol):
    async def record_initialize(self, payload: dict[str, Any]) -> None: ...

    async def record_answer(self, payload: dict[str, Any]) -> None: ...

    async def record_page_exit(self, payload: dict[str, Any], allow_placeholder: bool) -> None: ...


@dataclass
class _Waiter:
    ok: bool = False
    event: asyncio.Event = field(default_factory=asyncio.Event)


class PersistenceService:
    def __init__(self, settings: Settings, repository: EventRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._wal = Path(settings.persistence.wal_path)
        self._dead_letter_path = self._wal.with_suffix(".deadletter.jsonl")
        self._pending: dict[str, dict[str, Any]] = {}
        self._waiters: dict[str, _Waiter] = {}
        self._lock = asyncio.Lock()
        self._wakeup = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = False
        self._started = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def start(self) -> None:
        async with self._lock:
            self._load()
            self._started = True
        for _ in range(self._settings.persistence.worker_count):
            self._tasks.append(asyncio.create_task(self._worker()))
        self._wakeup.set()

    async def stop(self) -> None:
        self._stop = True
        self._wakeup.set()
        if not self._tasks:
            return
        done, pending = await asyncio.wait(
            self._tasks,
            timeout=self._settings.persistence.shutdown_drain_seconds,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
        if self._pending:
            logger.error("persistence_shutdown_incomplete remaining=%s", len(self._pending))
        else:
            logger.info("persistence_shutdown_drained")
        del done

    async def save(self, kind: str, payload: dict[str, Any]) -> bool:
        if not self._started:
            raise RuntimeError("Persistence service is not started.")
        job_id = str(payload["id"])
        waiter = _Waiter()
        job = {
            "id": job_id,
            "kind": kind,
            "payload": payload,
            "attempts": 0,
            "not_before": 0.0,
            "in_flight": False,
        }
        async with self._lock:
            self._pending[job_id] = job
            self._waiters[job_id] = waiter
            self._flush()
        self._wakeup.set()
        if not self._settings.persistence.wait_for_completion:
            return True
        try:
            await asyncio.wait_for(
                waiter.event.wait(),
                timeout=self._settings.persistence.request_wait_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("persistence_wait_timeout id=%s kind=%s", job_id, kind)
            return False
        return waiter.ok

    async def _worker(self) -> None:
        while True:
            job = await self._claim()
            if job is None:
                if self._stop and not self._pending:
                    return
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
                self._wakeup.clear()
                continue
            final = job["attempts"] + 1 >= self._settings.persistence.max_attempts
            try:
                await self._dispatch(job["kind"], job["payload"], final=final)
            except asyncio.CancelledError:
                async with self._lock:
                    job["in_flight"] = False
                raise
            except VisitNotReady:
                await self._fail(job, VisitNotReady("visit not stored yet"))
            except Exception as exc:
                logger.exception("persistence_attempt_failed id=%s kind=%s", job["id"], job["kind"])
                try:
                    await self._fail(job, exc)
                except Exception:
                    logger.exception("persistence_fail_handler_crashed id=%s", job["id"])
                    job["in_flight"] = False
            else:
                await self._complete(job)

    async def _dispatch(self, kind: str, payload: dict[str, Any], *, final: bool) -> None:
        if kind == "initialize":
            await self._repository.record_initialize(payload)
            return
        if kind == "answer":
            await self._repository.record_answer(payload)
            return
        if kind == "page_exit":
            await self._repository.record_page_exit(payload, allow_placeholder=final)
            return
        raise RuntimeError(f"Unknown persistence job: {kind}")

    async def _claim(self) -> dict[str, Any] | None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            for job in self._pending.values():
                if job["in_flight"]:
                    continue
                if not self._stop and job["not_before"] > now:
                    continue
                job["in_flight"] = True
                return job
        return None

    async def _complete(self, job: dict[str, Any]) -> None:
        async with self._lock:
            self._pending.pop(job["id"], None)
            waiter = self._waiters.pop(job["id"], None)
            self._flush()
        if waiter is not None:
            waiter.ok = True
            waiter.event.set()
        logger.info("persisted kind=%s id=%s", job["kind"], job["id"])

    async def _fail(self, job: dict[str, Any], exc: Exception) -> None:
        give_up = False
        waiter: _Waiter | None = None
        async with self._lock:
            job["attempts"] += 1
            job["in_flight"] = False
            if job["attempts"] >= self._settings.persistence.max_attempts:
                give_up = True
                waiter = self._waiters.get(job["id"])
            else:
                delay = min(
                    self._settings.persistence.max_backoff_seconds,
                    self._settings.persistence.initial_backoff_seconds * (2 ** (job["attempts"] - 1)),
                )
                job["not_before"] = asyncio.get_running_loop().time() + delay
                self._flush()
        if give_up:
            try:
                self._write_dead_letter(job, exc)
            except Exception:
                logger.exception("persistence_dead_letter_failed id=%s", job["id"])
            async with self._lock:
                self._pending.pop(job["id"], None)
                waiter = self._waiters.pop(job["id"], waiter)
                self._flush()
            if waiter is not None:
                waiter.ok = False
                waiter.event.set()
        self._wakeup.set()

    def _write_dead_letter(self, job: dict[str, Any], exc: Exception) -> None:
        record = {
            "id": job["id"],
            "kind": job["kind"],
            "payload": job["payload"],
            "attempts": job["attempts"],
            "error": str(exc),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        encoded = json.dumps(record, ensure_ascii=False)
        logger.error("persistence_dead_letter %s", encoded)
        self._dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._dead_letter_path, "a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _export(self) -> dict[str, dict[str, Any]]:
        return {
            job_id: {
                "id": job["id"],
                "kind": job["kind"],
                "payload": job["payload"],
                "attempts": job["attempts"],
            }
            for job_id, job in self._pending.items()
        }

    def _flush(self) -> None:
        self._wal.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._wal.with_suffix(".tmp")
        body = json.dumps({"jobs": self._export()}, ensure_ascii=False)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._wal)

    def _load(self) -> None:
        if not self._wal.is_file():
            return
        try:
            raw = json.loads(self._wal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("persistence_wal_unreadable path=%s", self._wal)
            broken = self._wal.with_suffix(".corrupt.json")
            os.replace(self._wal, broken)
            return
        jobs = raw.get("jobs", {}) if isinstance(raw, dict) else {}
        if not isinstance(jobs, dict):
            return
        max_attempts = self._settings.persistence.max_attempts
        for job in jobs.values():
            if not isinstance(job, dict) or "id" not in job or "payload" not in job:
                continue
            attempts = int(job.get("attempts") or 0)
            restored = {
                "id": str(job["id"]),
                "kind": job.get("kind"),
                "payload": job["payload"],
                "attempts": attempts,
                "not_before": 0.0,
                "in_flight": False,
            }
            if attempts >= max_attempts:
                self._write_dead_letter(restored, RuntimeError("retry limit already reached"))
                continue
            self._pending[restored["id"]] = restored
        if self._pending:
            logger.info("persistence_wal_replay count=%s", len(self._pending))
        self._flush()
