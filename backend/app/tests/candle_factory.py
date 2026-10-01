"""Deterministic synthetic candles for engine and regime tests.

Every series here is a closed-form shape (lines, sine waves, explicit
OHLC) so test expectations follow from the shape, not from random
numbers.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.market_data.schemas import Candle, Timeframe

BASE_TIME = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)  # 09:15 IST


def candles_from_ohlc(
    rows: list[tuple[float, float, float, float]],
    *,
    volumes: list[int] | None = None,
    symbol: str = "TEST",
    timeframe: Timeframe = Timeframe.M5,
    start: datetime = BASE_TIME,
) -> list[Candle]:
    volumes = volumes or [1000] * len(rows)
    step = timedelta(minutes=timeframe.minutes)
    return [
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=start + step * i,
            open=o,
            high=h,
            low=low,
            close=c,
            volume=v,
            is_closed=True,
        )
        for i, ((o, h, low, c), v) in enumerate(zip(rows, volumes, strict=True))
    ]


def candles_from_closes(
    closes: list[float],
    *,
    volumes: list[int] | None = None,
    wick_percent: float = 0.05,
    symbol: str = "TEST",
    timeframe: Timeframe = Timeframe.M5,
    start: datetime = BASE_TIME,
) -> list[Candle]:
    """Each candle opens at the previous close; high/low extend the body
    by `wick_percent` of price."""

    rows = []
    previous = closes[0]
    for close in closes:
        open_ = previous
        pad = max(open_, close) * wick_percent / 100.0
        rows.append((open_, max(open_, close) + pad, min(open_, close) - pad, close))
        previous = close
    return candles_from_ohlc(rows, volumes=volumes, symbol=symbol, timeframe=timeframe, start=start)


def uptrend_closes(n: int = 250, start: float = 100.0, step: float = 0.15) -> list[float]:
    """Rising line plus a 10-candle wave: clean higher highs and higher
    lows. With n=250 the series ends on a rising part of the wave."""

    return [start + step * i + 0.5 * math.sin(2 * math.pi * i / 10) for i in range(n)]


def downtrend_closes(n: int = 250, start: float = 200.0, step: float = 0.15) -> list[float]:
    return [start - step * i - 0.5 * math.sin(2 * math.pi * i / 10) for i in range(n)]


def flat_closes(n: int, level: float = 100.0) -> list[float]:
    return [level] * n


def zigzag_closes(n: int, level: float = 100.0, amplitude: float = 0.5) -> list[float]:
    """Alternates level+amplitude / level-amplitude every candle."""

    return [level + (amplitude if i % 2 == 0 else -amplitude) for i in range(n)]


def range_closes(n: int = 250, level: float = 100.0) -> list[float]:
    """Sideways chop: two incommensurate waves around a flat level."""

    return [
        level + 0.6 * math.sin(2 * math.pi * i / 7) + 0.4 * math.sin(2 * math.pi * i / 11.3)
        for i in range(n)
    ]


def breakout_candles() -> list[Candle]:
    """Ordinary two-way trade, a tight compression, then one wide
    candle closing well above the compression range on heavy volume."""

    closes = [100 + 0.4 * math.sin(2 * math.pi * i / 7) for i in range(200)]
    closes += [100 + 0.05 * math.sin(2 * math.pi * i / 5) for i in range(40)]
    rows: list[tuple[float, float, float, float]] = []
    previous = closes[0]
    for i, close in enumerate(closes):
        pad = 0.05 if i < 200 else 0.01
        rows.append((previous, max(previous, close) + pad, min(previous, close) - pad, close))
        previous = close
    rows.append((previous, previous + 1.3, previous - 0.02, previous + 1.25))
    volumes = [1000] * 240 + [6000]
    return candles_from_ohlc(rows, volumes=volumes)


def high_volatility_candles() -> list[Candle]:
    """Calm two-way trade, then an episode of large, alternating candles
    with long wicks on both sides."""

    closes = [100 + 0.3 * math.sin(2 * math.pi * i / 9) for i in range(200)]
    rows: list[tuple[float, float, float, float]] = []
    previous = closes[0]
    for close in closes:
        rows.append((previous, max(previous, close) + 0.05, min(previous, close) - 0.05, close))
        previous = close
    for i in range(20):
        direction = 1 if i % 2 == 0 else -1
        open_ = previous
        close = open_ + direction * 0.8
        rows.append((open_, max(open_, close) + 1.6, min(open_, close) - 1.6, close))
        previous = close
    return candles_from_ohlc(rows, volumes=[1000] * len(rows))
