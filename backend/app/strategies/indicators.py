"""Small indicator helpers for the strategy modules (closed candles only).

RSI and ADX use Wilder's smoothing; EMA/SMA reuse the market intelligence
implementations so every module computes them the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from zoneinfo import ZoneInfo

from app.market_data.schemas import Candle
from app.market_intelligence.moving_average import compute_ema_series, compute_sma_series
from app.market_intelligence.volatility import compute_true_range_series

IST = ZoneInfo("Asia/Kolkata")


def rsi_series(closes: Sequence[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if period < 1 or len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    def value(g: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / loss)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def adx_series(
    candles: Sequence[Candle], period: int = 14
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(ADX, +DI, -DI), Wilder's method. ADX needs about 2 x period candles."""

    n = len(candles)
    adx: list[float | None] = [None] * n
    pdi: list[float | None] = [None] * n
    mdi: list[float | None] = [None] * n
    if period < 1 or n <= 2 * period:
        return adx, pdi, mdi
    tr = compute_true_range_series(list(candles))
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    s_tr = sum(tr[1 : period + 1])
    s_p = sum(plus_dm[1 : period + 1])
    s_m = sum(minus_dm[1 : period + 1])
    dx: list[float] = []
    for i in range(period, n):
        if i > period:
            s_tr = s_tr - s_tr / period + tr[i]
            s_p = s_p - s_p / period + plus_dm[i]
            s_m = s_m - s_m / period + minus_dm[i]
        p = 100.0 * s_p / s_tr if s_tr else 0.0
        m = 100.0 * s_m / s_tr if s_tr else 0.0
        pdi[i], mdi[i] = p, m
        dx.append(100.0 * abs(p - m) / (p + m) if (p + m) else 0.0)
        if len(dx) == period:
            adx[i] = sum(dx) / period
        elif len(dx) > period:
            prev = adx[i - 1]
            assert prev is not None
            adx[i] = (prev * (period - 1) + dx[-1]) / period
    return adx, pdi, mdi


def moving_average(values: list[float], period: int, kind: str = "EMA") -> list[float | None]:
    return (
        compute_ema_series(values, period)
        if kind.upper() == "EMA"
        else compute_sma_series(values, period)
    )


def relative_volume(
    candles: Sequence[Candle], lookback: int = 20, *, mode: str = "time_of_day", sessions: int = 10
) -> tuple[float | None, str]:
    """Latest candle volume / its historical baseline, and how the baseline
    was built. `time_of_day`: the average volume of candles at the same
    time of day in the previous `sessions` sessions (intraday volume is
    U-shaped, so a plain rolling mean overstates the open and understates
    midday); falls back to `rolling` (the previous `lookback` candles)
    when fewer than 3 such candles exist."""

    if len(candles) < 2:
        return None, "not enough candles"
    latest = candles[-1]
    if mode == "time_of_day":
        when: time = latest.timestamp.astimezone(IST).time()
        day = latest.timestamp.astimezone(IST).date()
        same = [
            c.volume
            for c in candles[:-1]
            if c.timestamp.astimezone(IST).time() == when
            and c.timestamp.astimezone(IST).date() < day
        ][-sessions:]
        if len(same) >= 3 and sum(same) > 0:
            return (
                latest.volume / (sum(same) / len(same)),
                f"same time of day, {len(same)} sessions",
            )
    prior = [c.volume for c in candles[-lookback - 1 : -1]]
    if not prior or sum(prior) <= 0:
        return None, "no volume history"
    return latest.volume / (sum(prior) / len(prior)), f"previous {len(prior)} candles"


def body_fraction(c: Candle) -> float:
    rng = c.high - c.low
    return abs(c.close - c.open) / rng if rng > 0 else 0.0


def close_location(c: Candle) -> float:
    """0 = closed at the low, 1 = at the high."""

    rng = c.high - c.low
    return (c.close - c.low) / rng if rng > 0 else 0.5


__all__ = [
    "adx_series",
    "body_fraction",
    "close_location",
    "moving_average",
    "relative_volume",
    "rsi_series",
]
