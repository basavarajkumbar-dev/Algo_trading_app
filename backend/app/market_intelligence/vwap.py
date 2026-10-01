"""VWAP (Volume-Weighted Average Price) engine.

VWAP is cumulative over the candles passed in, so the caller decides
the anchor: pass only the current session's candles for a classic
session VWAP. The intelligence service anchors VWAP on the most recent
N candles (75 x 5m candles is one NSE session) because the simulated
feed has no real session boundaries.
"""

from __future__ import annotations

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, Relationship
from app.market_intelligence.utils import relationship, slope_percent

DEFAULT_SLOPE_LOOKBACK = 5
DEFAULT_RECENT_WINDOW = 20
DEFAULT_TOLERANCE_PERCENT = 0.02


class VWAPResult(EngineResult):
    vwap: float
    vwap_slope_percent: float | None
    price_vs_vwap: Relationship
    distance_from_vwap: float
    distance_from_vwap_percent: float
    crossings: int
    recent_crossings: int
    candles_since_last_cross: int | None
    anchor_candles: int


def _typical_price(candle: Candle) -> float:
    return (candle.high + candle.low + candle.close) / 3.0


def compute_vwap_series(candles: list[Candle]) -> list[float]:
    """Cumulative VWAP after each candle (same length/order as `candles`).
    Candles with zero volume contribute nothing; if no volume has traded
    yet, the close stands in for VWAP."""

    series: list[float] = []
    cumulative_pv = 0.0
    cumulative_volume = 0
    for candle in candles:
        cumulative_pv += _typical_price(candle) * candle.volume
        cumulative_volume += candle.volume
        series.append(cumulative_pv / cumulative_volume if cumulative_volume > 0 else candle.close)
    return series


def _crossing_indices(closes: list[float], vwap_series: list[float]) -> list[int]:
    """Indices where the close moved from one side of VWAP to the other
    (candles sitting exactly on VWAP don't count as a side)."""

    indices: list[int] = []
    previous_side = 0
    for i, (close, vwap) in enumerate(zip(closes, vwap_series, strict=True)):
        side = 1 if close > vwap else -1 if close < vwap else 0
        if side == 0:
            continue
        if previous_side != 0 and side != previous_side:
            indices.append(i)
        previous_side = side
    return indices


def analyze_vwap(
    candles: list[Candle],
    *,
    slope_lookback: int = DEFAULT_SLOPE_LOOKBACK,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    tolerance_percent: float = DEFAULT_TOLERANCE_PERCENT,
) -> VWAPResult:
    if len(candles) < 2:
        raise InsufficientDataError("VWAP analysis needs at least 2 candles.")

    vwap_series = compute_vwap_series(candles)
    closes = [c.close for c in candles]
    price = closes[-1]
    vwap = vwap_series[-1]

    vwap_slope = slope_percent(vwap_series, min(slope_lookback, len(vwap_series) - 1))
    distance = price - vwap
    distance_percent = (distance / vwap * 100.0) if vwap else 0.0
    price_vs_vwap = relationship(price, vwap, tolerance_percent)

    crosses = _crossing_indices(closes, vwap_series)
    recent_start = len(candles) - recent_window
    recent_crossings = sum(1 for i in crosses if i >= recent_start)
    candles_since_last_cross = (len(candles) - 1 - crosses[-1]) if crosses else None

    reasons: list[str] = []
    if price_vs_vwap == Relationship.ABOVE:
        reasons.append(f"Price {distance_percent:+.2f}% above VWAP")
    elif price_vs_vwap == Relationship.BELOW:
        reasons.append(f"Price {distance_percent:+.2f}% below VWAP")
    else:
        reasons.append("Price at VWAP")
    if vwap_slope is not None and vwap_slope > 0:
        reasons.append("VWAP sloping up")
    elif vwap_slope is not None and vwap_slope < 0:
        reasons.append("VWAP sloping down")
    if recent_crossings >= 3:
        reasons.append(f"{recent_crossings} VWAP crossings in the last {recent_window} candles")

    return VWAPResult(
        vwap=round(vwap, 4),
        vwap_slope_percent=round(vwap_slope, 6) if vwap_slope is not None else None,
        price_vs_vwap=price_vs_vwap,
        distance_from_vwap=round(distance, 4),
        distance_from_vwap_percent=round(distance_percent, 4),
        crossings=len(crosses),
        recent_crossings=recent_crossings,
        candles_since_last_cross=candles_since_last_cross,
        anchor_candles=len(candles),
        reasons=reasons,
    )
