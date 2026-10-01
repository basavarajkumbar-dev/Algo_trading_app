"""MarketDataService tests.

Uses a fast tick interval and a small watchlist so the background
streaming loop produces enough activity within a short, bounded wait —
no reliance on wall-clock realism.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.paper_provider import PaperMarketDataProvider
from app.market_data.schemas import MarketDataProviderStatus, Tick, Timeframe
from app.market_data.service import MarketDataService


async def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


async def test_service_start_and_stop_toggle_running_and_provider_status() -> None:
    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider(tick_interval_seconds=0.02))
    service = MarketDataService(provider, watchlist=["RELIANCE", "TCS"])

    await service.start()
    try:
        assert service.is_running
        assert await service.get_provider_status() == MarketDataProviderStatus.CONNECTED
    finally:
        await service.stop()

    assert not service.is_running
    assert await service.get_provider_status() == MarketDataProviderStatus.DISCONNECTED


async def test_service_produces_snapshots_and_candles_for_watchlist_symbols() -> None:
    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider(tick_interval_seconds=0.02))
    service = MarketDataService(provider, watchlist=["RELIANCE", "TCS"])

    await service.start()
    try:
        got_snapshot = await _wait_until(lambda: service.get_snapshot("RELIANCE") is not None)
        assert got_snapshot, "expected a RELIANCE snapshot to appear once ticks start flowing"

        snapshot = service.get_snapshot("RELIANCE")
        assert snapshot is not None
        assert snapshot.price > 0
        assert isinstance(snapshot.change, float)
        assert isinstance(snapshot.change_percent, float)

        current_1m = service.get_candles("RELIANCE", Timeframe.M1, limit=5)
        assert len(current_1m) >= 1
    finally:
        await service.stop()


async def test_service_snapshot_is_none_for_symbol_not_on_watchlist() -> None:
    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider(tick_interval_seconds=10.0))
    service = MarketDataService(provider, watchlist=["RELIANCE"])

    await service.start()
    try:
        assert service.get_snapshot("TCS") is None
    finally:
        await service.stop()


async def test_service_tracks_rejected_ticks_separately_from_accepted() -> None:
    """Feed an invalid tick directly through the service's processing
    path and confirm it's counted as rejected, not silently dropped or
    counted as accepted."""

    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider(tick_interval_seconds=10.0))
    service = MarketDataService(provider, watchlist=["RELIANCE"])
    await service.start()
    try:
        stale_tick = Tick(
            symbol="RELIANCE",
            price=1200.0,
            volume=10,
            timestamp=datetime.now(UTC) - timedelta(minutes=5),
        )
        await service._process_tick(stale_tick)

        assert service.stats.ticks_rejected == 1
        assert service.stats.ticks_accepted == 0
        assert "STALE_TICK" in service.stats.rejected_by_issue
    finally:
        await service.stop()


async def test_service_get_instruments_delegates_to_provider() -> None:
    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider())
    service = MarketDataService(provider, watchlist=["RELIANCE"])
    instruments = await service.get_instruments()
    assert len(instruments) > 0


async def test_stop_is_safe_to_call_before_start() -> None:
    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider())
    service = MarketDataService(provider, watchlist=["RELIANCE"])
    await service.stop()  # must not raise
    assert not service.is_running


async def test_start_is_idempotent() -> None:
    provider = PaperMarketDataProvider(delegate=MockMarketDataProvider(tick_interval_seconds=0.05))
    service = MarketDataService(provider, watchlist=["RELIANCE"])
    await service.start()
    try:
        first_task = service._task
        await service.start()  # second call should be a no-op
        assert service._task is first_task
    finally:
        await service.stop()
