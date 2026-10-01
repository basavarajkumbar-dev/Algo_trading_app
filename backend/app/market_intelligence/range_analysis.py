"""Range / compression / breakout analysis.

Looks at the window of candles *before* the latest one:

- range high/low and width (in % and in ATR units),
- compression: the window's average true range relative to a longer
  baseline (a quiet, narrowing market),
- breakout: whether the latest close is beyond the window's high/low,
  and by how many ATRs,
- directional efficiency (Kaufman efficiency ratio): net move divided
  by the total distance travelled. Near 1 = moving in a straight line,
  near 0 = chopping back and forth.
"""

from __future__ import annotations

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, Trend
from app.market_intelligence.utils import mean
from app.market_intelligence.volatility import compute_true_range_series

DEFAULT_RANGE_WINDOW = 12
DEFAULT_BASELINE_PERIOD = 100
DEFAULT_EFFICIENCY_WINDOW = 20
DEFAULT_COMPRESSION_RATIO = 0.8


class RangeResult(EngineResult):
    window: int
    range_high: float
    range_low: float
    range_width_percent: float
    range_width_atr: float
    compression_ratio: float  # window avg true range / baseline avg true range
    is_compressed: bool
    breakout_direction: Trend | None  # UP / DOWN when the latest close left the range
    breakout_distance_atr: float
    efficiency_ratio: float
    net_move_atr: float


def analyze_range(
    candles: list[Candle],
    *,
    window: int = DEFAULT_RANGE_WINDOW,
    baseline_period: int = DEFAULT_BASELINE_PERIOD,
    efficiency_window: int = DEFAULT_EFFICIENCY_WINDOW,
    compression_ratio_max: float = DEFAULT_COMPRESSION_RATIO,
) -> RangeResult:
    if len(candles) < window + 2:
        raise InsufficientDataError(f"Range analysis needs at least {window + 2} candles.")

    true_ranges = compute_true_range_series(candles)
    prior = candles[-(window + 1) : -1]
    prior_tr = true_ranges[-(window + 1) : -1]
    baseline_tr = true_ranges[-(baseline_period + window + 1) : -(window + 1)] or prior_tr
    baseline = mean(baseline_tr)
    window_atr = mean(prior_tr)

    range_high = max(c.high for c in prior)
    range_low = min(c.low for c in prior)
    width = range_high - range_low
    reference = baseline if baseline > 0 else window_atr
    compression = window_atr / baseline if baseline > 0 else 1.0
    is_compressed = compression <= compression_ratio_max

    close = candles[-1].close
    breakout: Trend | None = None
    distance = 0.0
    if close > range_high:
        breakout = Trend.UP
        distance = (close - range_high) / reference if reference else 0.0
    elif close < range_low:
        breakout = Trend.DOWN
        distance = (range_low - close) / reference if reference else 0.0

    closes = [c.close for c in candles[-(efficiency_window + 1) :]]
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
    net = closes[-1] - closes[0]
    efficiency = abs(net) / path if path > 0 else 0.0

    reasons: list[str] = []
    if is_compressed:
        reasons.append(f"Compression: recent candle ranges {compression:.2f}x baseline")
    if breakout == Trend.UP:
        reasons.append(f"Close broke above the {window}-candle range high")
    elif breakout == Trend.DOWN:
        reasons.append(f"Close broke below the {window}-candle range low")
    if efficiency < 0.3:
        reasons.append(f"Choppy, low directional efficiency ({efficiency:.2f})")
    elif efficiency > 0.6:
        reasons.append(f"Directional movement (efficiency {efficiency:.2f})")

    return RangeResult(
        window=window,
        range_high=round(range_high, 4),
        range_low=round(range_low, 4),
        range_width_percent=round(width / range_low * 100.0, 4) if range_low else 0.0,
        range_width_atr=round(width / reference, 4) if reference else 0.0,
        compression_ratio=round(compression, 4),
        is_compressed=is_compressed,
        breakout_direction=breakout,
        breakout_distance_atr=round(distance, 4),
        efficiency_ratio=round(efficiency, 4),
        net_move_atr=round(net / reference, 4) if reference else 0.0,
        reasons=reasons,
    )
