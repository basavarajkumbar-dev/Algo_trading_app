"""Candle builder tests: 1m candle creation and higher-timeframe aggregation.

Uses explicit, hand-constructed timestamps rather than wall-clock time
so the tests are fully deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market_data.candle_builder import CandleBuilder, floor_to_timeframe
from app.market_data.schemas import Tick, Timeframe

BASE = datetime(2026, 1, 5, 9, 15, 0, tzinfo=UTC)  # 09:15:00 UTC, arbitrary Monday


def _tick(symbol: str, price: float, seconds_offset: int, volume: int = 100) -> Tick:
    return Tick(
        symbol=symbol,
        price=price,
        volume=volume,
        timestamp=BASE + timedelta(seconds=seconds_offset),
    )


def test_floor_to_timeframe_aligns_to_clock_boundaries() -> None:
    ts = datetime(2026, 1, 5, 9, 17, 42, tzinfo=UTC)
    assert floor_to_timeframe(ts, 1) == datetime(2026, 1, 5, 9, 17, 0, tzinfo=UTC)
    assert floor_to_timeframe(ts, 5) == datetime(2026, 1, 5, 9, 15, 0, tzinfo=UTC)
    assert floor_to_timeframe(ts, 15) == datetime(2026, 1, 5, 9, 15, 0, tzinfo=UTC)
    assert floor_to_timeframe(ts, 60) == datetime(2026, 1, 5, 9, 0, 0, tzinfo=UTC)


def test_first_tick_opens_an_in_progress_1m_candle() -> None:
    builder = CandleBuilder()
    builder.process_tick(_tick("RELIANCE", 1200.0, 0))

    current = builder.get_current_candle("RELIANCE", Timeframe.M1)
    assert current is not None
    assert current.is_closed is False
    assert current.open == current.high == current.low == current.close == 1200.0
    assert current.timestamp == BASE


def test_ticks_within_the_same_minute_update_ohlcv() -> None:
    builder = CandleBuilder()
    builder.process_tick(_tick("RELIANCE", 1200.0, 0, volume=100))
    builder.process_tick(_tick("RELIANCE", 1205.0, 10, volume=50))
    builder.process_tick(_tick("RELIANCE", 1195.0, 20, volume=25))
    builder.process_tick(_tick("RELIANCE", 1202.0, 30, volume=10))

    current = builder.get_current_candle("RELIANCE", Timeframe.M1)
    assert current is not None
    assert current.open == 1200.0
    assert current.high == 1205.0
    assert current.low == 1195.0
    assert current.close == 1202.0
    assert current.volume == 185
    assert current.is_closed is False


def test_new_minute_closes_the_previous_1m_candle() -> None:
    builder = CandleBuilder()
    builder.process_tick(_tick("RELIANCE", 1200.0, 0))
    builder.process_tick(_tick("RELIANCE", 1210.0, 45))
    closed = builder.process_tick(_tick("RELIANCE", 1220.0, 65))  # 65s -> next minute

    closed_1m = [c for c in closed if c.timeframe == Timeframe.M1]
    assert len(closed_1m) == 1
    assert closed_1m[0].close == 1210.0
    assert closed_1m[0].is_closed is True

    history = builder.get_candles("RELIANCE", Timeframe.M1, limit=10)
    assert len(history) == 1
    assert history[0].close == 1210.0

    new_current = builder.get_current_candle("RELIANCE", Timeframe.M1)
    assert new_current is not None
    assert new_current.open == 1220.0
    assert new_current.timestamp == BASE + timedelta(minutes=1)


def test_five_minute_aggregation_from_five_one_minute_candles() -> None:
    """Feed five 1-minute candles' worth of ticks for the [09:15, 09:20)
    bucket, then a tick in the *next* 5-minute bucket, and confirm the
    5m candle only closes once that next-bucket tick confirms it — a
    candle is never "closed" until time has moved past it, matching
    real exchange semantics."""

    builder = CandleBuilder()
    # Minutes 0-4 fall in bucket [09:15, 09:20); minute 6's tick is what
    # finally closes minute 5's 1m candle, which is what triggers the
    # 5m bucket to close (minute 5 itself belongs to the *next* bucket).
    prices = [1200.0, 1210.0, 1195.0, 1215.0, 1205.0, 1230.0, 1240.0]
    all_closed = []

    for minute_index, price in enumerate(prices):
        tick = _tick("RELIANCE", price, minute_index * 60)
        all_closed.extend(builder.process_tick(tick))

    closed_5m = [c for c in all_closed if c.timeframe == Timeframe.M5]
    assert len(closed_5m) == 1
    candle = closed_5m[0]
    assert candle.timestamp == BASE
    assert candle.open == 1200.0  # first minute's open
    assert candle.high == 1215.0  # max across minutes 0-4
    assert candle.low == 1195.0  # min across minutes 0-4
    assert candle.close == 1205.0  # close of minute 4 (the last minute IN this bucket)
    assert candle.volume == 100 * 5  # 100 volume per tick, 5 ticks aggregated
    assert candle.is_closed is True


def test_higher_timeframes_all_aggregate_from_the_same_1m_stream() -> None:
    """A full hour of 1-minute ticks (plus one confirming tick into the
    next hour) should close exactly one 60m candle and the expected
    number of 3m/5m/15m/30m candles.

    Uses a clean top-of-hour base (unlike the module-level BASE, which
    is 09:15 and would make the 60m bucket boundary fall mid-sequence,
    since floor_to_timeframe aligns 60m buckets to the top of the clock
    hour — see test_floor_to_timeframe_aligns_to_clock_boundaries).

    62 ticks = minutes 0-61: minute 60's tick confirms minute 59 is
    closed, and every bucket ending at or before minute 60 is therefore
    also confirmed closed (a bucket [k, k+n) closes once minute (k+n)
    itself closes, which needs a tick from minute (k+n+1))."""

    hour_base = datetime(2026, 1, 5, 9, 0, 0, tzinfo=UTC)

    def hour_tick(price: float, minute_index: int) -> Tick:
        return Tick(
            symbol="RELIANCE",
            price=price,
            volume=100,
            timestamp=hour_base + timedelta(minutes=minute_index),
        )

    builder = CandleBuilder()
    for minute_index in range(62):
        price = 1200.0 + minute_index
        builder.process_tick(hour_tick(price, minute_index))

    candles_3m = builder.get_candles("RELIANCE", Timeframe.M3, limit=100)
    candles_5m = builder.get_candles("RELIANCE", Timeframe.M5, limit=100)
    candles_15m = builder.get_candles("RELIANCE", Timeframe.M15, limit=100)
    candles_30m = builder.get_candles("RELIANCE", Timeframe.M30, limit=100)
    candles_60m = builder.get_candles("RELIANCE", Timeframe.M60, limit=100)

    assert len(candles_3m) == 20
    assert len(candles_5m) == 12
    assert len(candles_15m) == 4
    assert len(candles_30m) == 2
    assert len(candles_60m) == 1

    # The single closed 60m candle should span minutes 0-59.
    hour_candle = candles_60m[0]
    assert hour_candle.timestamp == hour_base
    assert hour_candle.open == 1200.0
    assert hour_candle.close == 1259.0  # close of minute 59, the last minute in this bucket
    assert hour_candle.high == 1259.0
    assert hour_candle.low == 1200.0


def test_history_is_capped_at_configured_length() -> None:
    builder = CandleBuilder(history_length=3)
    for minute_index in range(10):
        builder.process_tick(_tick("RELIANCE", 1200.0 + minute_index, minute_index * 60))

    history = builder.get_candles("RELIANCE", Timeframe.M1, limit=100)
    assert len(history) == 3


def test_candles_are_independent_per_symbol() -> None:
    builder = CandleBuilder()
    builder.process_tick(_tick("RELIANCE", 1200.0, 0))
    builder.process_tick(_tick("TCS", 2000.0, 0))

    reliance = builder.get_current_candle("RELIANCE", Timeframe.M1)
    tcs = builder.get_current_candle("TCS", Timeframe.M1)
    assert reliance is not None and reliance.open == 1200.0
    assert tcs is not None and tcs.open == 2000.0


def test_get_candles_including_current_appends_in_progress_candle() -> None:
    builder = CandleBuilder()
    builder.process_tick(_tick("RELIANCE", 1200.0, 0))
    builder.process_tick(_tick("RELIANCE", 1210.0, 65))  # closes minute 1, opens minute 2

    with_current = builder.get_candles_including_current("RELIANCE", Timeframe.M1, limit=10)
    assert len(with_current) == 2
    assert with_current[0].is_closed is True
    assert with_current[-1].is_closed is False


def test_unknown_symbol_returns_empty_history_and_no_current_candle() -> None:
    builder = CandleBuilder()
    assert builder.get_candles("UNKNOWN", Timeframe.M1) == []
    assert builder.get_current_candle("UNKNOWN", Timeframe.M1) is None
