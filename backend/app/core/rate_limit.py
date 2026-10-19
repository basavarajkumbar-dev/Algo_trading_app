"""A small in-process sliding-window rate limiter (per key).

Used for sensitive endpoints (broker credential management). One backend
process serves the app (uvicorn --workers 1), so in-memory state is
sufficient; with several processes each would enforce its own window.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Hashable


class SlidingWindowLimiter:
    def __init__(
        self, limit: int, window_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[Hashable, deque[float]] = {}

    def check(self, key: Hashable) -> float | None:
        """Record a hit. None if allowed, else seconds until the next one is."""

        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(hits[0] + self.window - now, 0.0)
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()
