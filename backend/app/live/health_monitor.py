"""BrokerHealthMonitor: the live layer's view of system health.

Every few seconds it takes the broker health report (database, Redis,
Kite auth, REST, instrument master, WebSocket, data freshness) and the
OrderManager's reconciliation state. `healthy` feeds the execution gate,
so a degradation blocks the next live entry immediately. While live
trading is armed, a transition away from HEALTHY is audited, and a lost
Kite session disarms.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class BrokerHealthMonitor:
    def __init__(
        self,
        probe: Callable[[], Awaitable[Any]],
        *,
        interval: float = 5.0,
        max_age: float = 20.0,
        on_change: Callable[[str | None, str], Awaitable[None]] | None = None,
    ) -> None:
        self._probe = probe
        self.interval = interval
        self.max_age = max_age
        self._on_change = on_change
        self.report: Any = None
        self._at: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def age_seconds(self) -> float | None:
        return None if self._at is None else time.monotonic() - self._at

    @property
    def status(self) -> str:
        if self.report is None:
            return "UNKNOWN"
        age = self.age_seconds
        if age is None or age > self.max_age:
            return "STALE_REPORT"
        return str(self.report.status)

    @property
    def healthy(self) -> bool:
        return self.status == "HEALTHY"

    async def check(self) -> Any:
        previous = self.status if self.report is not None else None
        try:
            self.report = await self._probe()
            self._at = time.monotonic()
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - reported as unhealthy
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Broker health probe failed: %s", self.last_error)
        current = self.status
        if current != previous and self._on_change is not None:
            try:
                await self._on_change(previous, current)
            except Exception:  # noqa: BLE001
                logger.exception("Health change handler failed")
        return self.report

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="live-health-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await self.check()
            await asyncio.sleep(self.interval)
