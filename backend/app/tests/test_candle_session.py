"""Candles on the NSE session (real data): 09:15-anchored buckets, the
clipped last 60m candle, time-based closing and late-tick rejection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market_data.candle_builder import (
    NSE_SESSION,
    CandleBuilder,
    aggregate_candles,
    floor_to_timeframe,
)
from app.market_data.schemas import Candle, Tick, Timeframe

IST_0915 = datetime(2026, 10, 1, 3, 45, tzinfo=UTC)  # 09:15 IST


def tick(ts: datetime, price: float, volume: int = 1, symbol: str = "RELIANCE") -> Tick:
    return Tick(symbol=symbol, price=price, volume=volume, timestamp=ts)


def test_session_anchored_buckets() -> None:
    anchor = NSE_SESSION.anchor_minute_utc()
    assert anchor == 225 and NSE_SESSION.minutes == 375
    ts = IST_0915 + timedelta(minutes=70)  # 10:25 IST
    assert floor_to_timeframe(ts, 60, anchor) == IST_0915 + timedelta(minutes=60)  # 10:15 IST
    assert floor_to_timeframe(ts, 30, anchor) == IST_0915 + timedelta(minutes=60)  # 10:15
    assert floor_to_timeframe(ts, 15, anchor) == IST_0915 + timedelta(minutes=60)
    assert floor_to_timeframe(ts, 5, anchor) == IST_0915 + timedelta(minutes=70)
    # without a session: the clock hour (simulated 24/7 feed)
    assert floor_to_timeframe(ts, 60) == datetime(2026, 10, 1, 4, 0, tzinfo=UTC)


def test_a_candle_closes_only_after_its_period_ends() -> None:
    b = CandleBuilder(session=NSE_SESSION)
    b.process_tick(tick(IST_0915 + timedelta(seconds=5), 100))
    b.process_tick(tick(IST_0915 + timedelta(seconds=50), 101))
    assert b.finalize_due(IST_0915 + timedelta(seconds=59)) == []  # still forming
    assert b.finalize_due(IST_0915 + timedelta(seconds=61)) == []  # inside the grace
    closed = b.finalize_due(IST_0915 + timedelta(seconds=63))
    assert [c.timeframe for c in closed] == [Timeframe.M1]
    assert closed[0].is_closed and (closed[0].open, closed[0].close) == (100, 101)
    assert b.get_current_candle("RELIANCE", Timeframe.M5) is not None  # 5m still forming


def test_quiet_instrument_closes_5m_15m_60m_on_time() -> None:
    b = CandleBuilder(session=NSE_SESSION)
    for m in range(60):  # one tick per minute for the first hour
        b.process_tick(tick(IST_0915 + timedelta(minutes=m, seconds=1), 100 + m, 10))
    # no tick after 10:14:01: finalization alone closes the 10:14 minute and the hour
    closed = b.finalize_due(IST_0915 + timedelta(minutes=60, seconds=3))
    by_tf = {c.timeframe: c for c in closed}
    assert {Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M60} <= set(by_tf)
    h = by_tf[Timeframe.M60]
    assert h.timestamp == IST_0915 and (h.open, h.close, h.high, h.low) == (100, 159, 159, 100)
    assert h.volume == 600
    assert len(b.get_candles("RELIANCE", Timeframe.M5)) == 12
    assert len(b.get_candles("RELIANCE", Timeframe.M15)) == 4


def test_late_and_after_close_ticks_never_reopen_candles() -> None:
    b = CandleBuilder(session=NSE_SESSION)
    b.process_tick(tick(IST_0915 + timedelta(seconds=1), 100))
    b.process_tick(tick(IST_0915 + timedelta(minutes=1, seconds=1), 101))  # closes 09:15
    assert b.process_tick(tick(IST_0915 + timedelta(seconds=30), 99)) == []
    assert b.late_ticks == 1
    assert b.get_candles("RELIANCE", Timeframe.M1)[-1].close == 100  # unchanged
    close = NSE_SESSION.close_on(IST_0915)  # 15:30 IST
    b.process_tick(tick(close + timedelta(seconds=5), 50))  # closing-session print
    assert b.late_ticks == 2


def test_last_hour_candle_is_clipped_at_the_session_close() -> None:
    b = CandleBuilder(session=NSE_SESSION)
    last = IST_0915 + timedelta(hours=6)  # 15:15 IST
    for m in range(15):
        b.process_tick(tick(last + timedelta(minutes=m, seconds=1), 200 + m))
    closed = b.finalize_due(NSE_SESSION.close_on(last) + timedelta(seconds=3))
    hour = [c for c in closed if c.timeframe == Timeframe.M60]
    assert len(hour) == 1 and hour[0].timestamp == last and hour[0].close == 214


def test_aggregation_with_the_session_matches_kite_buckets() -> None:
    bars = [
        Candle(
            symbol="X",
            timeframe=Timeframe.M5,
            timestamp=IST_0915 + timedelta(minutes=5 * i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=10,
            is_closed=True,
        )
        for i in range(75)  # a full session
    ]
    hours = aggregate_candles(bars, Timeframe.M60, NSE_SESSION)
    assert [h.timestamp for h in hours][:2] == [IST_0915, IST_0915 + timedelta(hours=1)]
    assert len(hours) == 7 and hours[-1].timestamp == IST_0915 + timedelta(hours=6)
    assert all(h.is_closed for h in hours) and hours[-1].volume == 30  # 15:15-15:30
