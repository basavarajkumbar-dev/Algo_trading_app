"""Swing structure for price action.

Two complementary views of swings:

1. Fractal swings with HH/HL/LH/LL labels, Break of Structure and
   Structure Shift: reused unchanged from the Phase 4 structure engine
   (`app.market_intelligence.structure`), so both phases always agree.
2. Significant swings: a zig-zag that only confirms a turn once price
   has reversed by `reversal_atr` x ATR(14) from the extreme. This filters
   out minor wiggles and yields alternating high/low pivots and the
   legs between them, which the retracement and support/resistance
   engines use to find impulses and swing levels.

The last pivot is always provisional: it is the running extreme of the
current leg and moves until price reverses far enough to confirm it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, Trend
from app.market_intelligence.structure import StructureResult, analyze_structure
from app.market_intelligence.utils import mean
from app.market_intelligence.volatility import compute_atr_series, compute_true_range_series
from app.price_action.config import SwingConfig


class PivotType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class Pivot(BaseModel):
    type: PivotType
    index: int
    price: float
    timestamp: datetime
    confirmed: bool  # False only for the running extreme of the current leg


class SwingLeg(BaseModel):
    direction: Trend  # UP or DOWN
    start: Pivot
    end: Pivot
    size: float
    size_atr: float
    candles: int
    complete: bool  # False for the leg still in progress


def zigzag_pivots(candles: list[Candle], threshold: float) -> list[Pivot]:
    """Alternating significant highs and lows.

    A high is confirmed once a later low is `threshold` below it, and a
    low once a later high is `threshold` above it. The final pivot is
    the unconfirmed extreme of the leg in progress."""

    if not candles or threshold <= 0:
        return []

    def pivot(kind: PivotType, index: int, confirmed: bool = True) -> Pivot:
        candle = candles[index]
        price = candle.high if kind == PivotType.HIGH else candle.low
        return Pivot(
            type=kind,
            index=index,
            price=price,
            timestamp=candle.timestamp,
            confirmed=confirmed,
        )

    pivots: list[Pivot] = []
    hi_idx = lo_idx = 0
    direction: Trend | None = None
    extreme_idx = 0

    for i, candle in enumerate(candles):
        if direction is None:
            if candle.high > candles[hi_idx].high:
                hi_idx = i
            if candle.low < candles[lo_idx].low:
                lo_idx = i
            if candles[hi_idx].high - candles[lo_idx].low >= threshold:
                if hi_idx > lo_idx:
                    pivots.append(pivot(PivotType.LOW, lo_idx))
                    direction, extreme_idx = Trend.UP, hi_idx
                else:
                    pivots.append(pivot(PivotType.HIGH, hi_idx))
                    direction, extreme_idx = Trend.DOWN, lo_idx
        elif direction == Trend.UP:
            if candle.high > candles[extreme_idx].high:
                extreme_idx = i
            elif candles[extreme_idx].high - candle.low >= threshold:
                pivots.append(pivot(PivotType.HIGH, extreme_idx))
                direction, extreme_idx = Trend.DOWN, i
        else:
            if candle.low < candles[extreme_idx].low:
                extreme_idx = i
            elif candle.high - candles[extreme_idx].low >= threshold:
                pivots.append(pivot(PivotType.LOW, extreme_idx))
                direction, extreme_idx = Trend.UP, i

    if direction is not None:
        kind = PivotType.HIGH if direction == Trend.UP else PivotType.LOW
        pivots.append(pivot(kind, extreme_idx, confirmed=False))
    return pivots


def legs_from_pivots(pivots: list[Pivot], atr: float) -> list[SwingLeg]:
    legs = []
    for start, end in zip(pivots, pivots[1:], strict=False):
        size = abs(end.price - start.price)
        legs.append(
            SwingLeg(
                direction=Trend.UP if end.price > start.price else Trend.DOWN,
                start=start,
                end=end,
                size=round(size, 4),
                size_atr=round(size / atr, 2) if atr else 0.0,
                candles=end.index - start.index,
                complete=end.confirmed,
            )
        )
    return legs


def reference_atr(candles: list[Candle], period: int = 14) -> float:
    """The ATR every price action engine uses: ATR(period) at the latest
    candle, falling back to the mean true range for short series. One
    definition everywhere, so "2.5 ATR" means the same thing in the
    retracement, swing and support/resistance outputs."""

    if not candles:
        return 0.0
    series = compute_atr_series(candles, period)
    latest = series[-1] if series else None
    return latest if latest else mean(compute_true_range_series(candles))


class SwingStructureResult(EngineResult):
    atr: float
    reversal_threshold: float
    pivots: list[Pivot]
    legs: list[SwingLeg]
    latest_swing_high: Pivot | None
    latest_swing_low: Pivot | None
    current_leg: SwingLeg | None
    structure: StructureResult | None  # Phase 4 HH/HL/LH/LL view


def analyze_swing_structure(
    candles: list[Candle], config: SwingConfig | None = None
) -> SwingStructureResult:
    cfg = config or SwingConfig()
    minimum = cfg.atr_period + 2
    if len(candles) < minimum:
        raise InsufficientDataError(f"Swing structure needs at least {minimum} candles.")

    atr = reference_atr(candles, cfg.atr_period)
    threshold = cfg.reversal_atr * atr
    pivots = zigzag_pivots(candles, threshold)
    legs = legs_from_pivots(pivots, atr)
    confirmed = [p for p in pivots if p.confirmed]
    highs = [p for p in confirmed if p.type == PivotType.HIGH]
    lows = [p for p in confirmed if p.type == PivotType.LOW]

    try:
        structure: StructureResult | None = analyze_structure(
            candles, lookback=cfg.fractal_lookback
        )
    except InsufficientDataError:
        structure = None

    reasons = [
        f"{len(confirmed)} significant swings (reversal >= {cfg.reversal_atr:g} ATR)",
    ]
    current = legs[-1] if legs else None
    if current is not None:
        reasons.append(
            f"Current leg {current.direction.value.lower()} "
            f"{current.size_atr:.1f} ATR over {current.candles} candles"
        )
    if structure is not None:
        reasons.extend(structure.reasons)

    return SwingStructureResult(
        atr=round(atr, 4),
        reversal_threshold=round(threshold, 4),
        pivots=pivots[-cfg.max_pivots_returned :],
        legs=legs[-cfg.max_pivots_returned :],
        latest_swing_high=highs[-1] if highs else None,
        latest_swing_low=lows[-1] if lows else None,
        current_leg=current,
        structure=structure,
        reasons=reasons,
    )
