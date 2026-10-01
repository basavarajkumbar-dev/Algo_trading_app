"""MockMarketDataProvider tests.

No credentials, network, or external services required — this provider
is meant to work completely standalone. Streaming tests use a short
tick interval so the suite stays fast.
"""

from __future__ import annotations

import pytest

from app.market_data.base import MarketDataProviderError
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.schemas import MarketDataProviderStatus, SimulatedRegime, Timeframe
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_SYMBOL


async def test_connect_and_disconnect_change_health_status() -> None:
    provider = MockMarketDataProvider()
    assert await provider.health_check() == MarketDataProviderStatus.DISCONNECTED

    await provider.connect()
    assert await provider.health_check() == MarketDataProviderStatus.CONNECTED

    await provider.disconnect()
    assert await provider.health_check() == MarketDataProviderStatus.DISCONNECTED


async def test_get_instruments_returns_nifty_50_plus_index() -> None:
    provider = MockMarketDataProvider()
    instruments = await provider.get_instruments()
    symbols = {i.symbol for i in instruments}

    assert len(instruments) == len(NIFTY_50_UNIVERSE) + 1
    assert NIFTY_INDEX_SYMBOL in symbols
    assert "RELIANCE" in symbols


async def test_get_ltp_returns_reference_price_before_any_tick() -> None:
    provider = MockMarketDataProvider()
    ltp = await provider.get_ltp("RELIANCE")
    assert ltp > 0


async def test_get_ltp_unknown_symbol_raises() -> None:
    provider = MockMarketDataProvider()
    with pytest.raises(MarketDataProviderError):
        await provider.get_ltp("NOT_A_REAL_SYMBOL")


async def test_subscribe_unknown_symbol_raises() -> None:
    provider = MockMarketDataProvider()
    with pytest.raises(MarketDataProviderError):
        await provider.subscribe(["NOT_A_REAL_SYMBOL"])


async def test_subscribe_and_unsubscribe_track_watchlist() -> None:
    provider = MockMarketDataProvider(rng_seed=42)
    await provider.subscribe(["RELIANCE", "TCS"])
    assert provider._subscribed == {"RELIANCE", "TCS"}

    await provider.unsubscribe(["RELIANCE"])
    assert provider._subscribed == {"TCS"}


async def test_get_historical_data_returns_requested_length_and_is_ohlc_consistent() -> None:
    provider = MockMarketDataProvider()
    candles = await provider.get_historical_data("RELIANCE", Timeframe.M5, limit=20)

    assert len(candles) == 20
    for candle in candles:
        assert candle.high >= candle.open
        assert candle.high >= candle.close
        assert candle.low <= candle.open
        assert candle.low <= candle.close
        assert candle.is_closed is True
        assert candle.volume >= 0

    # Candles should be in ascending chronological order.
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


async def test_get_historical_data_unknown_symbol_raises() -> None:
    provider = MockMarketDataProvider()
    with pytest.raises(MarketDataProviderError):
        await provider.get_historical_data("NOT_A_REAL_SYMBOL", Timeframe.M1)


async def test_stream_ticks_yields_only_subscribed_symbols() -> None:
    provider = MockMarketDataProvider(tick_interval_seconds=0.01, rng_seed=7)
    await provider.connect()
    await provider.subscribe(["RELIANCE", "TCS"])

    seen_symbols = set()
    count = 0
    async for tick in provider.stream_ticks():
        seen_symbols.add(tick.symbol)
        assert tick.price > 0
        assert tick.volume >= 0
        count += 1
        if count >= 20:
            break

    assert seen_symbols.issubset({"RELIANCE", "TCS"})
    await provider.disconnect()


async def test_stream_ticks_stops_when_disconnected() -> None:
    """The generator's while-loop condition checks connection status, so
    once disconnected it should stop yielding rather than run forever."""

    provider = MockMarketDataProvider(tick_interval_seconds=0.01, rng_seed=1)
    await provider.connect()
    await provider.subscribe(["RELIANCE"])

    stream = provider.stream_ticks()
    tick = await stream.__anext__()
    assert tick.symbol == "RELIANCE"

    await provider.disconnect()

    # After disconnecting, the generator should exhaust rather than
    # continue yielding indefinitely.
    remaining = [t async for t in stream]
    assert remaining == []


async def test_set_and_get_regime() -> None:
    provider = MockMarketDataProvider()
    provider.set_regime("RELIANCE", SimulatedRegime.BREAKOUT)
    assert provider.get_regime("RELIANCE") == SimulatedRegime.BREAKOUT


async def test_set_regime_unknown_symbol_raises() -> None:
    provider = MockMarketDataProvider()
    with pytest.raises(MarketDataProviderError):
        provider.set_regime("NOT_A_REAL_SYMBOL", SimulatedRegime.RANGE)


async def test_generated_ticks_move_price_and_update_ltp() -> None:
    provider = MockMarketDataProvider(tick_interval_seconds=0.01, rng_seed=99)
    provider.set_regime("RELIANCE", SimulatedRegime.TRENDING_UP)
    await provider.connect()
    await provider.subscribe(["RELIANCE"])

    initial_ltp = await provider.get_ltp("RELIANCE")

    count = 0
    async for _tick in provider.stream_ticks():
        count += 1
        if count >= 30:
            break

    final_ltp = await provider.get_ltp("RELIANCE")
    # A strong trending-up regime over 30 ticks should very likely move
    # the price meaningfully away from the initial value. This is a
    # probabilistic check on a seeded RNG (deterministic for this seed),
    # not a flaky real-time assertion.
    assert final_ltp != initial_ltp
