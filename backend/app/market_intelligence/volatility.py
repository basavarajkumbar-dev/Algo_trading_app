"""ATR / volatility engine.

Volatility is classified *relative to the instrument's own recent
history* (ATR divided by a longer baseline of true range) rather than
by fixed ATR-percent thresholds, because "high volatility" for a 1m
chart, a 60m chart, a utility stock and a metals stock are very
different absolute numbers. ATR as a percent of price is still
reported for display.

Also measures candle character used by the regime detector: average
wick-to-range ratio, how often candle direction flips, and how large
the latest candle is relative to ATR.
"""

from __future__ import annotations

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, VolatilityLevel
from app.market_intelligence.utils import mean

DEFAULT_ATR_PERIOD = 14
DEFAULT_BASELINE_PERIOD = 100
DEFAULT_CHARACTER_WINDOW = 10
#: ATR / baseline-TR ratio thresholds.
DEFAULT_LOW_RATIO = 0.75
DEFAULT_HIGH_RATIO = 1.4
DEFAULT_EXTREME_RATIO = 2.0
#: ATR rising by at least this fraction over `expansion_lookback` candles = expanding.
DEFAULT_EXPANSION_FRACTION = 0.10
DEFAULT_EXPANSION_LOOKBACK = 5


def compute_true_range_series(candles: list[Candle]) -> list[float]:
    """True range per candle; the first candle uses its high-low range."""

    series: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            series.append(candle.high - candle.low)
            continue
        prev_close = candles[i - 1].close
        series.append(
            max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close),
            )
        )
    return series


def compute_atr_series(
    candles: list[Candle], period: int = DEFAULT_ATR_PERIOD
) -> list[float | None]:
    """Wilder-smoothed ATR; None until `period` true ranges exist."""

    tr = compute_true_range_series(candles)
    atr: list[float | None] = [None] * len(tr)
    if period < 1 or len(tr) < period:
        return atr
    previous = sum(tr[:period]) / period
    atr[period - 1] = previous
    for i in range(period, len(tr)):
        previous = (previous * (period - 1) + tr[i]) / period
        atr[i] = previous
    return atr


class VolatilityResult(EngineResult):
    atr: float
    atr_percent: float
    atr_baseline: float
    atr_ratio: float
    volatility_level: VolatilityLevel
    is_expanding: bool
    average_wick_ratio: float  # 0-1, share of candle range that is wick
    direction_changes: int  # body-direction flips within the character window
    character_window: int
    latest_range_atr: float  # latest candle's range in ATR units


def analyze_volatility(
    candles: list[Candle],
    *,
    period: int = DEFAULT_ATR_PERIOD,
    baseline_period: int = DEFAULT_BASELINE_PERIOD,
    character_window: int = DEFAULT_CHARACTER_WINDOW,
    low_ratio: float = DEFAULT_LOW_RATIO,
    high_ratio: float = DEFAULT_HIGH_RATIO,
    extreme_ratio: float = DEFAULT_EXTREME_RATIO,
    expansion_fraction: float = DEFAULT_EXPANSION_FRACTION,
    expansion_lookback: int = DEFAULT_EXPANSION_LOOKBACK,
) -> VolatilityResult:
    if len(candles) < period + 1:
        raise InsufficientDataError(
            f"Volatility analysis needs at least {period + 1} candles (got {len(candles)})."
        )

    tr = compute_true_range_series(candles)
    atr_series = compute_atr_series(candles, period)
    atr = atr_series[-1] or 0.0
    price = candles[-1].close

    baseline_window = tr[-baseline_period:]
    atr_baseline = mean(baseline_window) or atr
    atr_ratio = atr / atr_baseline if atr_baseline > 0 else 1.0

    if atr_ratio >= extreme_ratio:
        level = VolatilityLevel.EXTREME
    elif atr_ratio >= high_ratio:
        level = VolatilityLevel.HIGH
    elif atr_ratio <= low_ratio:
        level = VolatilityLevel.LOW
    else:
        level = VolatilityLevel.NORMAL

    past_atr = atr_series[-1 - expansion_lookback] if len(atr_series) > expansion_lookback else None
    is_expanding = bool(past_atr and atr > past_atr * (1 + expansion_fraction))

    window = candles[-character_window:]
    wick_ratios = []
    for c in window:
        candle_range = c.high - c.low
        if candle_range > 0:
            wick_ratios.append((candle_range - abs(c.close - c.open)) / candle_range)
    average_wick_ratio = mean(wick_ratios)

    directions = [1 if c.close > c.open else -1 for c in window if c.close != c.open]
    direction_changes = sum(1 for a, b in zip(directions, directions[1:]) if a != b)

    previous_atr = atr_series[-2] or atr
    latest = candles[-1]
    latest_range_atr = (latest.high - latest.low) / previous_atr if previous_atr > 0 else 0.0

    reasons: list[str] = []
    if level in (VolatilityLevel.HIGH, VolatilityLevel.EXTREME):
        reasons.append(f"ATR {atr_ratio:.2f}x its {len(baseline_window)}-candle baseline")
    elif level == VolatilityLevel.LOW:
        reasons.append(f"Volatility compressed (ATR {atr_ratio:.2f}x baseline)")
    if is_expanding:
        reasons.append("ATR expanding")
    if average_wick_ratio >= 0.55:
        reasons.append(f"Large wicks ({average_wick_ratio:.0%} of candle range)")
    if len(directions) >= 4 and direction_changes >= 0.6 * (len(directions) - 1):
        reasons.append(f"{direction_changes} candle-direction reversals in {len(window)} candles")

    return VolatilityResult(
        atr=round(atr, 4),
        atr_percent=round(atr / price * 100.0, 4) if price else 0.0,
        atr_baseline=round(atr_baseline, 4),
        atr_ratio=round(atr_ratio, 4),
        volatility_level=level,
        is_expanding=is_expanding,
        average_wick_ratio=round(average_wick_ratio, 4),
        direction_changes=direction_changes,
        character_window=len(window),
        latest_range_atr=round(latest_range_atr, 4),
        reasons=reasons,
    )
