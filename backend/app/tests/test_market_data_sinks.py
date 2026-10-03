"""Market-data sinks and the stale guard: Redis latest-state keys (with
TTLs, never every tick), the candle store, the signal stale guard and the
MarketDataMonitor's DATA_STALE event."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import redis.asyncio as redis_asyncio
from sqlalchemy import delete, select

from app.database.models.market_data import MarketCandle
from app.database.session import async_session_factory
from app.market_data.schemas import Candle, NormalizedTick, Timeframe
from app.market_data.sinks import CandleStore, MarketDataMonitor, MarketStateCache
from app.signals.service import SignalService

NOW = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0").rsplit("/", 1)[0] + "/15"


def ntick(price: float, token: int = 738561) -> NormalizedTick:
    return NormalizedTick(
        instrument_token=token,
        symbol="RELIANCE",
        exchange="NSE",
        timestamp=NOW,
        received_at=NOW,
        last_price=price,
    )


def candle(minute: int, tf: Timeframe = Timeframe.M5) -> Candle:
    return Candle(
        symbol="RELIANCE",
        timeframe=tf,
        timestamp=NOW + timedelta(minutes=minute),
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        volume=10,
        is_closed=True,
    )


async def _redis() -> Any:
    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("no Redis")
    await client.flushdb()
    return client


async def test_redis_keeps_only_the_latest_state_with_ttls() -> None:
    client = await _redis()
    cache = MarketStateCache(client=client, candle_keep=3)
    for p in (1400.0, 1400.5, 1401.0):  # a burst: coalesced into one write
        cache.on_tick(ntick(p))
    cache.on_candles([candle(i) for i in range(5)])
    cache.set_status({"source": "ZERODHA", "status": "LIVE"})
    cache.set_subscription({738561: "RELIANCE"})
    assert await cache.flush()
    tick = json.loads(await client.get("market:tick:738561"))
    assert tick["last_price"] == 1401.0
    assert float(await client.get("market:ltp:738561")) == 1401.0
    assert 0 < await client.ttl("market:tick:738561") <= 120
    assert await client.llen("market:candle:RELIANCE:5m") == 3  # trimmed, not forever
    assert json.loads(await client.get("market:status"))["status"] == "LIVE"
    assert json.loads(await client.get("market:subscription")) == {"738561": "RELIANCE"}
    assert await client.ttl("market:status") > 0
    await client.flushdb()
    await client.aclose()


async def test_redis_outage_is_counted_not_raised() -> None:
    cache = MarketStateCache("redis://127.0.0.1:1/0")
    cache.on_tick(ntick(1.0))
    assert await cache.flush() is False and cache.failures == 1


async def test_candle_store_is_idempotent(db_available: bool) -> None:
    if not db_available:
        pytest.skip("no Postgres")
    async with async_session_factory() as s:
        await s.execute(delete(MarketCandle).where(MarketCandle.source == "TEST"))
        await s.commit()
    store = CandleStore(async_session_factory, source="TEST")
    store.on_candles([candle(0), candle(5)])
    store.on_candles([candle(5)])  # the same candle twice
    assert await store.flush() == 3
    async with async_session_factory() as s:
        rows = (
            (await s.execute(select(MarketCandle).where(MarketCandle.source == "TEST")))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        await s.execute(delete(MarketCandle).where(MarketCandle.source == "TEST"))
        await s.commit()


class FakeMD:
    def __init__(self, stale: bool) -> None:
        self.stale = stale
        self.source = "ZERODHA"
        self.simulated = False
        self.stale_threshold = timedelta(seconds=10)
        self.last_tick_at = NOW
        self.provider = SimpleNamespace(status_details=lambda: {"websocket": "CONNECTED"})

    def is_stale(self, symbol: str | None = None, now: Any = None) -> bool:
        return self.stale

    def data_age_seconds(self, symbol: str | None = None, now: Any = None) -> float:
        return 42.0

    def data_status(self) -> str:
        return "STALE" if self.stale else "LIVE"


def test_stale_data_makes_signals_non_executable() -> None:
    signal = SimpleNamespace(symbol="RELIANCE", executable=True, blocked_reason=None, reasons=[])
    svc = SignalService.__new__(SignalService)
    svc._market_data = FakeMD(stale=True)  # type: ignore[assignment]
    svc._apply_stale_guard([signal], NOW)  # type: ignore[list-item]
    assert signal.executable is False and signal.blocked_reason == "DATA_STALE"
    assert "DATA_STALE" in signal.reasons[0] and "42s" in signal.reasons[0]
    fresh = SimpleNamespace(symbol="TCS", executable=True, blocked_reason=None, reasons=[])
    svc._market_data = FakeMD(stale=False)  # type: ignore[assignment]
    svc._apply_stale_guard([fresh], NOW)  # type: ignore[list-item]
    assert fresh.executable is True


async def test_monitor_logs_data_stale_once(caplog: pytest.LogCaptureFixture) -> None:
    md = FakeMD(stale=False)
    monitor = MarketDataMonitor(md)
    await monitor.check()
    md.stale = True
    await monitor.check()
    await monitor.check()
    assert [e for e, _ in monitor.events].count("DATA_STALE") == 1
    md.stale = False
    await monitor.check()
    assert monitor.events[-1][0] == "DATA_LIVE"
