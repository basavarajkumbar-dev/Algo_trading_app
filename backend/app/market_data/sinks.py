"""Where market data goes besides the engines (Phase 13).

MarketStateCache   Redis, latest state only (never every tick forever):
                     market:tick:{token}          latest normalized tick, TTL
                     market:ltp:{token}           latest price, TTL
                     market:candle:{symbol}:{tf}  last N closed candles (list)
                     market:status                feed status snapshot, TTL
                     market:subscription          subscribed tokens -> symbol
                   Writes are coalesced (one pipeline every flush interval
                   with the latest tick per instrument), so a burst of ticks
                   costs one round trip.
CandleStore        PostgreSQL market_candles (closed candles; batched,
                   idempotent on source/symbol/timeframe/timestamp) and,
                   only when PERSIST_RAW_TICKS=true, market_ticks.
MarketDataMonitor  watches feed state and freshness; logs DATA_STALE /
                   WEBSOCKET_* transitions and records them as system_events.

All sinks are best effort: a Redis or database outage is logged and
counted, and never stops the feed or the engines.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database.models.market_data import MarketCandle, MarketTick
from app.database.models.system_event import SystemEvent
from app.market_data.base import utcnow
from app.market_data.schemas import Candle, NormalizedTick

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


def _json(value: Any) -> str:
    return json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v))


class MarketStateCache:
    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: Any = None,
        tick_ttl_seconds: int = 120,
        status_ttl_seconds: int = 30,
        candle_keep: int = 300,
        candle_ttl_seconds: int = 3 * 86400,
        flush_interval: float = 0.5,
    ) -> None:
        if client is None and redis_url:
            import redis.asyncio as redis_asyncio

            client = redis_asyncio.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        self._redis = client
        self.tick_ttl = tick_ttl_seconds
        self.status_ttl = status_ttl_seconds
        self.candle_keep = candle_keep
        self.candle_ttl = candle_ttl_seconds
        self._interval = flush_interval
        self._ticks: dict[int, NormalizedTick] = {}
        self._candles: list[Candle] = []
        self._status: dict[str, Any] | None = None
        self._subscription: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self.flushes = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last_flush_at: datetime | None = None

    # sinks (sync, cheap)
    def on_tick(self, tick: NormalizedTick) -> None:
        self._ticks[tick.instrument_token] = tick

    def on_candles(self, candles: list[Candle]) -> None:
        self._candles.extend(c for c in candles if c.is_closed)

    def set_status(self, status: dict[str, Any]) -> None:
        self._status = status

    def set_subscription(self, tokens: dict[int, str]) -> None:
        self._subscription = {str(t): s for t, s in tokens.items()}

    async def start(self) -> None:
        if self._redis is not None and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="market-state-cache")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._redis is not None:
            try:
                await self.flush()
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.flush()

    async def flush(self) -> bool:
        if self._redis is None:
            return False
        ticks, self._ticks = self._ticks, {}
        candles, self._candles = self._candles, []
        status, self._status = self._status, None
        subscription, self._subscription = self._subscription, None
        if not (ticks or candles or status or subscription):
            return True
        try:
            pipe = self._redis.pipeline(transaction=False)
            for token, tick in ticks.items():
                pipe.set(f"market:tick:{token}", tick.model_dump_json(), ex=self.tick_ttl)
                pipe.set(f"market:ltp:{token}", str(tick.last_price), ex=self.tick_ttl)
            for c in candles:
                key = f"market:candle:{c.symbol}:{c.timeframe.value}"
                pipe.lpush(key, c.model_dump_json())
                pipe.ltrim(key, 0, self.candle_keep - 1)
                pipe.expire(key, self.candle_ttl)
            if status is not None:
                pipe.set("market:status", _json(status), ex=self.status_ttl)
            if subscription is not None:
                pipe.set("market:subscription", _json(subscription))
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - the cache is optional
            self.failures += 1
            self.last_error = type(exc).__name__
            if self.failures in (1, 10) or self.failures % 100 == 0:
                logger.warning(
                    "Redis market cache write failed (%d): %s", self.failures, self.last_error
                )
            return False
        self.flushes += 1
        self.last_flush_at = utcnow()
        return True


class CandleStore:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        source: str,
        persist_raw_ticks: bool = False,
        flush_interval: float = 5.0,
        max_buffer: int = 50_000,
    ) -> None:
        self._session_factory = session_factory
        self.source = source
        self.persist_raw_ticks = persist_raw_ticks
        self._interval = flush_interval
        self._max = max_buffer
        self._candles: list[Candle] = []
        self._ticks: list[NormalizedTick] = []
        self._task: asyncio.Task[None] | None = None
        self.stored_candles = 0
        self.stored_ticks = 0
        self.failures = 0
        self.dropped = 0

    def on_candles(self, candles: list[Candle]) -> None:
        self._candles.extend(c for c in candles if c.is_closed)
        if len(self._candles) > self._max:
            self.dropped += len(self._candles) - self._max
            self._candles = self._candles[-self._max :]

    def on_tick(self, tick: NormalizedTick) -> None:
        if self.persist_raw_ticks:
            self._ticks.append(tick)
            if len(self._ticks) > self._max:
                self.dropped += len(self._ticks) - self._max
                self._ticks = self._ticks[-self._max :]

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="candle-store")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.flush()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.flush()

    async def flush(self) -> int:
        candles, self._candles = self._candles, []
        ticks, self._ticks = self._ticks, []
        if not candles and not ticks:
            return 0
        try:
            async with self._session_factory() as session:
                if candles:
                    rows = [
                        {
                            "source": self.source,
                            "symbol": c.symbol,
                            "timeframe": c.timeframe.value,
                            "timestamp": c.timestamp,
                            "open": c.open,
                            "high": c.high,
                            "low": c.low,
                            "close": c.close,
                            "volume": c.volume,
                            "origin": "LIVE",
                        }
                        for c in candles
                    ]
                    for i in range(0, len(rows), 2000):
                        stmt = insert(MarketCandle).values(rows[i : i + 2000])
                        await session.execute(
                            stmt.on_conflict_do_nothing(constraint="uq_market_candle")
                        )
                if ticks:
                    session.add_all(
                        MarketTick(
                            instrument_token=t.instrument_token,
                            symbol=t.symbol,
                            timestamp=t.timestamp,
                            exchange_timestamp=t.exchange_timestamp,
                            last_price=t.last_price,
                            last_quantity=t.last_quantity,
                            volume=t.volume,
                            payload=json.loads(t.model_dump_json()),
                        )
                        for t in ticks
                    )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - storage is best effort
            self.failures += 1
            logger.warning("Candle store write failed: %s", type(exc).__name__)
            return 0
        self.stored_candles += len(candles)
        self.stored_ticks += len(ticks)
        return len(candles)


class MarketDataMonitor:
    """Turns feed state changes into logs, system events and the Redis
    market:status key. Polls the market data service (no hooks needed)."""

    def __init__(
        self,
        service: Any,
        *,
        session_factory: SessionFactory | None = None,
        cache: MarketStateCache | None = None,
        interval: float = 2.0,
    ) -> None:
        self.service = service
        self._session_factory = session_factory
        self.cache = cache
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self.last_data_status: str | None = None
        self.last_websocket: str | None = None
        self.events: list[tuple[str, str]] = []  # recent (event_type, message), newest last

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="market-data-monitor")

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
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Market data monitor check failed")
            await asyncio.sleep(self._interval)

    async def check(self) -> None:
        svc = self.service
        status = svc.data_status()
        details = svc.provider.status_details()
        websocket = str(details.get("websocket") or ("SIMULATED" if svc.simulated else "UNKNOWN"))
        if status != self.last_data_status:
            if status == "STALE":
                age = svc.data_age_seconds()
                await self._event(
                    "DATA_STALE",
                    "WARNING",
                    f"No market data for {age:.0f}s (threshold "
                    f"{svc.stale_threshold.total_seconds():.0f}s): executable signals and "
                    "new orders are blocked",
                )
            elif status == "LIVE" and self.last_data_status is not None:
                await self._event("DATA_LIVE", "INFO", "Market data is fresh again")
            self.last_data_status = status
        if not svc.simulated and websocket != self.last_websocket:
            if websocket == "CONNECTED":
                await self._event("WEBSOCKET_CONNECTED", "INFO", "Kite WebSocket connected")
            elif self.last_websocket == "CONNECTED":
                await self._event(
                    "WEBSOCKET_DISCONNECTED", "WARNING", f"Kite WebSocket is {websocket}"
                )
            self.last_websocket = websocket
        if self.cache is not None:
            self.cache.set_status(
                {
                    "source": svc.source,
                    "simulated": svc.simulated,
                    "status": status,
                    "websocket": websocket,
                    "last_tick_at": svc.last_tick_at,
                    "subscribed_instruments": details.get("subscribed_instruments"),
                    "checked_at": utcnow(),
                }
            )
            subs = getattr(svc.provider, "subscriptions", None)
            tokens = getattr(svc.provider, "_token_symbol", None)
            if subs is not None and tokens:
                self.cache.set_subscription({t: s for t, (s, _e) in tokens.items()})

    async def _event(self, event_type: str, severity: str, message: str) -> None:
        log = logger.warning if severity != "INFO" else logger.info
        log("%s %s", event_type, message)
        self.events = [*self.events[-49:], (event_type, message)]
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                session.add(
                    SystemEvent(
                        event_type=(
                            f"MARKET_{event_type}"
                            if not event_type.startswith("WEBSOCKET")
                            else event_type
                        ),
                        severity=severity,
                        message=message[:1000],
                        event_metadata={"source": self.service.source},
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.warning("Could not record market data event %s", event_type)


__all__ = ["CandleStore", "MarketDataMonitor", "MarketStateCache"]
