"""Moving average engine.

Configurable fast EMA, slow EMA and long simple MA (defaults 20 / 50 /
200, per the project brief). Computes, for each average, the price
relationship, percentage distance and slope, plus fast/slow separation
and whether the averages are converging or diverging, the overall
alignment, a trend direction, and an MA trend score (0-100).
"""

from __future__ import annotations

from enum import Enum

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import (
    EngineResult,
    InsufficientDataError,
    Relationship,
    Trend,
)
from app.market_intelligence.utils import clamp, percent_change, relationship, slope_percent

DEFAULT_EMA_FAST_PERIOD = 20
DEFAULT_EMA_SLOW_PERIOD = 50
DEFAULT_MA_PERIOD = 200
DEFAULT_SLOPE_LOOKBACK = 5
#: |slope| below this (percent per candle) counts as flat.
DEFAULT_FLAT_SLOPE_PERCENT = 0.02
DEFAULT_TOLERANCE_PERCENT = 0.02
#: Price further than this from the fast EMA is treated as extended.
DEFAULT_EXTENSION_PERCENT = 1.5


class SeparationState(str, Enum):
    CONVERGING = "CONVERGING"
    DIVERGING = "DIVERGING"
    STABLE = "STABLE"


class MAAlignment(str, Enum):
    BULLISH = "BULLISH"  # price > fast EMA > slow EMA
    BEARISH = "BEARISH"  # price < fast EMA < slow EMA
    MIXED = "MIXED"


def compute_sma_series(values: list[float], period: int) -> list[float | None]:
    """Simple moving average; None until `period` samples exist."""

    series: list[float | None] = []
    window_sum = 0.0
    for i, value in enumerate(values):
        window_sum += value
        if i >= period:
            window_sum -= values[i - period]
        series.append(window_sum / period if i >= period - 1 else None)
    return series


def compute_ema_series(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average; None until `period` samples exist,
    seeded with the SMA of the first `period` values."""

    series: list[float | None] = [None] * len(values)
    if period < 1 or len(values) < period:
        return series

    multiplier = 2 / (period + 1)
    previous = sum(values[:period]) / period
    series[period - 1] = previous
    for i in range(period, len(values)):
        previous = (values[i] - previous) * multiplier + previous
        series[i] = previous
    return series


class MovingAverageResult(EngineResult):
    ema_fast_period: int
    ema_slow_period: int
    ma_period: int

    ema_fast: float | None
    ema_slow: float | None
    ma: float | None

    price_vs_ema_fast: Relationship | None
    price_vs_ema_slow: Relationship | None
    price_vs_ma: Relationship | None

    distance_from_ema_fast_percent: float | None
    distance_from_ema_slow_percent: float | None
    distance_from_ma_percent: float | None

    ema_fast_slope_percent: float | None
    ema_slow_slope_percent: float | None
    ma_slope_percent: float | None

    separation_percent: float | None  # (fast - slow) / slow
    separation_state: SeparationState | None
    alignment: MAAlignment
    trend: Trend
    ma_trend_score: float  # 0-100, strength of the MA picture (direction-agnostic)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def analyze_moving_averages(
    candles: list[Candle],
    *,
    ema_fast_period: int = DEFAULT_EMA_FAST_PERIOD,
    ema_slow_period: int = DEFAULT_EMA_SLOW_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    slope_lookback: int = DEFAULT_SLOPE_LOOKBACK,
    flat_slope_percent: float = DEFAULT_FLAT_SLOPE_PERCENT,
    tolerance_percent: float = DEFAULT_TOLERANCE_PERCENT,
    extension_percent: float = DEFAULT_EXTENSION_PERCENT,
) -> MovingAverageResult:
    if len(candles) < ema_fast_period:
        raise InsufficientDataError(
            f"Moving average analysis needs at least {ema_fast_period} candles "
            f"(got {len(candles)})."
        )

    closes = [c.close for c in candles]
    price = closes[-1]

    fast_series = compute_ema_series(closes, ema_fast_period)
    slow_series = compute_ema_series(closes, ema_slow_period)
    ma_series = compute_sma_series(closes, ma_period)
    ema_fast, ema_slow, ma = fast_series[-1], slow_series[-1], ma_series[-1]

    def rel(level: float | None) -> Relationship | None:
        return relationship(price, level, tolerance_percent) if level is not None else None

    def dist(level: float | None) -> float | None:
        return percent_change(price, level) if level else None

    fast_slope = slope_percent(fast_series, slope_lookback)
    slow_slope = slope_percent(slow_series, slope_lookback)
    ma_slope = slope_percent(ma_series, slope_lookback)

    separation = None
    separation_state = None
    if ema_fast is not None and ema_slow is not None and ema_slow != 0:
        separation = (ema_fast - ema_slow) / ema_slow * 100.0
        past_fast = fast_series[-1 - slope_lookback] if len(fast_series) > slope_lookback else None
        past_slow = slow_series[-1 - slope_lookback] if len(slow_series) > slope_lookback else None
        if past_fast is not None and past_slow:
            past_separation = (past_fast - past_slow) / past_slow * 100.0
            change = abs(separation) - abs(past_separation)
            if abs(change) < 0.01:
                separation_state = SeparationState.STABLE
            elif change > 0:
                separation_state = SeparationState.DIVERGING
            else:
                separation_state = SeparationState.CONVERGING

    price_vs_fast = rel(ema_fast)
    if ema_fast is not None and ema_slow is not None and price > ema_fast > ema_slow:
        alignment = MAAlignment.BULLISH
    elif ema_fast is not None and ema_slow is not None and price < ema_fast < ema_slow:
        alignment = MAAlignment.BEARISH
    else:
        alignment = MAAlignment.MIXED

    slope = fast_slope or 0.0
    if price_vs_fast == Relationship.ABOVE and slope > flat_slope_percent and (separation or 0) >= 0:
        trend = Trend.UP
    elif (
        price_vs_fast == Relationship.BELOW and slope < -flat_slope_percent and (separation or 0) <= 0
    ):
        trend = Trend.DOWN
    else:
        trend = Trend.SIDEWAYS

    distance_fast = dist(ema_fast)
    reasons = _reasons(
        price_vs_fast, fast_slope, flat_slope_percent, alignment, separation_state, rel(ma),
        distance_fast, extension_percent,
    )

    score = _ma_trend_score(
        price_vs_fast=price_vs_fast,
        separation=separation,
        fast_slope=fast_slope,
        distance_fast=distance_fast,
        extension_percent=extension_percent,
    )

    return MovingAverageResult(
        ema_fast_period=ema_fast_period,
        ema_slow_period=ema_slow_period,
        ma_period=ma_period,
        ema_fast=_round(ema_fast),
        ema_slow=_round(ema_slow),
        ma=_round(ma),
        price_vs_ema_fast=price_vs_fast,
        price_vs_ema_slow=rel(ema_slow),
        price_vs_ma=rel(ma),
        distance_from_ema_fast_percent=_round(distance_fast),
        distance_from_ema_slow_percent=_round(dist(ema_slow)),
        distance_from_ma_percent=_round(dist(ma)),
        ema_fast_slope_percent=_round(fast_slope, 6),
        ema_slow_slope_percent=_round(slow_slope, 6),
        ma_slope_percent=_round(ma_slope, 6),
        separation_percent=_round(separation),
        separation_state=separation_state,
        alignment=alignment,
        trend=trend,
        ma_trend_score=score,
        reasons=reasons,
    )


def _reasons(
    price_vs_fast: Relationship | None,
    fast_slope: float | None,
    flat_slope_percent: float,
    alignment: MAAlignment,
    separation_state: SeparationState | None,
    price_vs_ma: Relationship | None,
    distance_fast: float | None,
    extension_percent: float,
) -> list[str]:
    reasons: list[str] = []
    if price_vs_fast is not None:
        reasons.append(f"Price {price_vs_fast.value.lower()} fast EMA")
    if fast_slope is not None:
        if fast_slope > flat_slope_percent:
            reasons.append("Positive EMA slope")
        elif fast_slope < -flat_slope_percent:
            reasons.append("Negative EMA slope")
        else:
            reasons.append("Flat EMA slope")
    if alignment == MAAlignment.BULLISH:
        reasons.append("Bullish EMA alignment (price > fast > slow)")
    elif alignment == MAAlignment.BEARISH:
        reasons.append("Bearish EMA alignment (price < fast < slow)")
    if separation_state == SeparationState.DIVERGING:
        reasons.append("Fast and slow EMAs diverging")
    elif separation_state == SeparationState.CONVERGING:
        reasons.append("Fast and slow EMAs converging")
    if price_vs_ma is not None:
        reasons.append(f"Price {price_vs_ma.value.lower()} long MA")
    if distance_fast is not None and abs(distance_fast) > extension_percent:
        reasons.append(f"Price extended {distance_fast:+.2f}% from fast EMA")
    return reasons


def _ma_trend_score(
    *,
    price_vs_fast: Relationship | None,
    separation: float | None,
    fast_slope: float | None,
    distance_fast: float | None,
    extension_percent: float,
    slope_full_scale: float = 0.05,
    separation_full_scale: float = 0.5,
) -> float:
    """0-100 strength of the moving-average picture, independent of
    direction: agreement of price/EMA/slope (40), slope magnitude (30),
    fast/slow separation (20), and not being overextended (10)."""

    signs: list[int] = []
    if price_vs_fast == Relationship.ABOVE:
        signs.append(1)
    elif price_vs_fast == Relationship.BELOW:
        signs.append(-1)
    if separation is not None and separation != 0:
        signs.append(1 if separation > 0 else -1)
    if fast_slope is not None and fast_slope != 0:
        signs.append(1 if fast_slope > 0 else -1)

    agreement = abs(sum(signs)) / 3.0 if signs else 0.0
    slope_component = min(abs(fast_slope or 0.0) / slope_full_scale, 1.0)
    separation_component = min(abs(separation or 0.0) / separation_full_scale, 1.0)
    distance = abs(distance_fast or 0.0)
    extension_component = clamp(1.0 - max(0.0, distance - extension_percent) / extension_percent, 0, 1)

    score = 40 * agreement + 30 * slope_component + 20 * separation_component + 10 * extension_component
    return round(clamp(score), 2)
