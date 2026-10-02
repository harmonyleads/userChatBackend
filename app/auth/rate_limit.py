"""In-memory sliding window. Each Cloud Run instance limits its own traffic."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.errors import AppError


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = time.monotonic()
        events = self._events[key]
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.limit:
            raise AppError(429, "Rate limit exceeded")
        events.append(now)
