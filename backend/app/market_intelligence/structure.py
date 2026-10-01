"""Market structure engine.

1. Swing detection: a candle is a swing high if its high is the single
   highest high within `lookback` candles on both sides (fractal rule);
   swing lows mirror this. `lookback` is configurable — a small value
   finds many minor swings, a larger one only major turns.
2. Each swing is labelled against the previous swing of the same type:
   Higher High / Lower High, Higher Low / Lower Low.
3. Trend: up when the latest high is a HH and the latest low a HL,
   down for LH + LL, otherwise decided by a clear majority of recent
   labels, else sideways.
4. Events, comparing the latest close with the latest swing levels:
   Break of Structure (price breaks the swing in the trend's direction)
   and Structure Shift (price breaks the swing against the trend).
5. State: TREND_CONTINUATION (break of structure), TRENDING, TREND_WEAKNESS
   (the most recent swing contradicts the trend), STRUCTURE_SHIFT, RANGING.

Note the latest `lookback` candles can never be confirmed swings yet,
by construction: confirmation needs candles on the right-hand side.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, Trend
from app.market_intelligence.utils import clamp

DEFAULT_SWING_LOOKBACK = 3
DEFAULT_LABEL_WINDOW = 4
DEFAULT_MAX_SWINGS_RETURNED = 12


class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingLabel(str, Enum):
    HIGHER_HIGH = "HIGHER_HIGH"
    LOWER_HIGH = "LOWER_HIGH"
    HIGHER_LOW = "HIGHER_LOW"
    LOWER_LOW = "LOWER_LOW"


class StructureEvent(str, Enum):
    BREAK_OF_STRUCTURE = "BREAK_OF_STRUCTURE"
    STRUCTURE_SHIFT = "STRUCTURE_SHIFT"
    NONE = "NONE"


class StructureState(str, Enum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    TRENDING = "TRENDING"
    TREND_WEAKNESS = "TREND_WEAKNESS"
    STRUCTURE_SHIFT = "STRUCTURE_SHIFT"
    RANGING = "RANGING"


_BULLISH_LABELS = {SwingLabel.HIGHER_HIGH, SwingLabel.HIGHER_LOW}
_BEARISH_LABELS = {SwingLabel.LOWER_HIGH, SwingLabel.LOWER_LOW}


class SwingPoint(BaseModel):
    type: SwingType
    index: int
    price: float
    timestamp: datetime
    label: SwingLabel | None = None


def find_swing_points(
    candles: list[Candle], *, lookback: int = DEFAULT_SWING_LOOKBACK
) -> list[SwingPoint]:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")

    swings: list[SwingPoint] = []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback : i + lookback + 1]
        highs = [c.high for c in window]
        lows = [c.low for c in window]
        candle = candles[i]
        if candle.high == max(highs) and highs.count(candle.high) == 1:
            swings.append(
                SwingPoint(type=SwingType.HIGH, index=i, price=candle.high, timestamp=candle.timestamp)
            )
        if candle.low == min(lows) and lows.count(candle.low) == 1:
            swings.append(
                SwingPoint(type=SwingType.LOW, index=i, price=candle.low, timestamp=candle.timestamp)
            )
    return swings  # already in index order


def classify_swings(swings: list[SwingPoint]) -> list[SwingPoint]:
    """Label each swing against the previous swing of the same type.
    The first high and first low have no reference and stay unlabelled.
    Equal levels: an equal high counts as a LOWER_HIGH (no new high was
    made) and an equal low as a HIGHER_LOW (the prior low held)."""

    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    labelled: list[SwingPoint] = []
    for swing in swings:
        updated = swing.model_copy()
        if swing.type == SwingType.HIGH:
            if last_high is not None:
                updated.label = (
                    SwingLabel.HIGHER_HIGH if swing.price > last_high.price else SwingLabel.LOWER_HIGH
                )
            last_high = swing
        else:
            if last_low is not None:
                updated.label = (
                    SwingLabel.LOWER_LOW if swing.price < last_low.price else SwingLabel.HIGHER_LOW
                )
            last_low = swing
        labelled.append(updated)
    return labelled


class StructureResult(EngineResult):
    trend: Trend
    structure_state: StructureState
    last_event: StructureEvent
    confidence: float
    latest_swing_high: float | None
    latest_swing_low: float | None
    higher_highs: int
    higher_lows: int
    lower_highs: int
    lower_lows: int
    swings: list[SwingPoint]


def analyze_structure(
    candles: list[Candle],
    *,
    lookback: int = DEFAULT_SWING_LOOKBACK,
    label_window: int = DEFAULT_LABEL_WINDOW,
    max_swings_returned: int = DEFAULT_MAX_SWINGS_RETURNED,
) -> StructureResult:
    minimum = lookback * 2 + 3
    if len(candles) < minimum:
        raise InsufficientDataError(
            f"Structure analysis needs at least {minimum} candles for lookback={lookback}."
        )

    swings = classify_swings(find_swing_points(candles, lookback=lookback))
    highs = [s for s in swings if s.type == SwingType.HIGH]
    lows = [s for s in swings if s.type == SwingType.LOW]
    labelled = [s for s in swings if s.label is not None]
    recent = [s.label for s in labelled[-label_window:] if s.label is not None]

    latest_high_label = highs[-1].label if highs else None
    latest_low_label = lows[-1].label if lows else None
    bullish = sum(1 for lbl in recent if lbl in _BULLISH_LABELS)
    bearish = sum(1 for lbl in recent if lbl in _BEARISH_LABELS)

    reasons: list[str] = []
    if latest_high_label == SwingLabel.HIGHER_HIGH and latest_low_label == SwingLabel.HIGHER_LOW:
        trend = Trend.UP
        reasons.append("Higher-high, higher-low structure")
    elif latest_high_label == SwingLabel.LOWER_HIGH and latest_low_label == SwingLabel.LOWER_LOW:
        trend = Trend.DOWN
        reasons.append("Lower-high, lower-low structure")
    elif recent and bullish - bearish >= 2:
        trend = Trend.UP
        reasons.append(f"Mostly bullish swings ({bullish} of last {len(recent)})")
    elif recent and bearish - bullish >= 2:
        trend = Trend.DOWN
        reasons.append(f"Mostly bearish swings ({bearish} of last {len(recent)})")
    else:
        trend = Trend.SIDEWAYS
        reasons.append("No clear swing sequence (mixed highs and lows)")

    last_event = StructureEvent.NONE
    close = candles[-1].close
    if highs and lows:
        if trend == Trend.UP and close > highs[-1].price:
            last_event = StructureEvent.BREAK_OF_STRUCTURE
        elif trend == Trend.DOWN and close < lows[-1].price:
            last_event = StructureEvent.BREAK_OF_STRUCTURE
        elif trend == Trend.UP and close < lows[-1].price:
            last_event = StructureEvent.STRUCTURE_SHIFT
        elif trend == Trend.DOWN and close > highs[-1].price:
            last_event = StructureEvent.STRUCTURE_SHIFT

    latest_label = labelled[-1].label if labelled else None
    contradicts = (trend == Trend.UP and latest_label in _BEARISH_LABELS) or (
        trend == Trend.DOWN and latest_label in _BULLISH_LABELS
    )

    if last_event == StructureEvent.STRUCTURE_SHIFT:
        state = StructureState.STRUCTURE_SHIFT
        reasons.append("Structure shift: price broke the swing level against the trend")
    elif last_event == StructureEvent.BREAK_OF_STRUCTURE:
        state = StructureState.TREND_CONTINUATION
        reasons.append("Break of structure in the trend's direction")
    elif trend != Trend.SIDEWAYS and contradicts:
        state = StructureState.TREND_WEAKNESS
        reasons.append(f"Trend weakness: latest swing is a {latest_label.value.replace('_', ' ').lower()}")
    elif trend != Trend.SIDEWAYS:
        state = StructureState.TRENDING
    else:
        state = StructureState.RANGING

    if trend == Trend.SIDEWAYS:
        confidence = 30.0
    else:
        dominant = max(bullish, bearish) / len(recent) if recent else 0.5
        confidence = 50.0 + 30.0 * dominant
        confidence += {
            StructureState.TREND_CONTINUATION: 15.0,
            StructureState.TREND_WEAKNESS: -15.0,
            StructureState.STRUCTURE_SHIFT: -25.0,
        }.get(state, 0.0)

    def count(label: SwingLabel) -> int:
        return sum(1 for s in swings if s.label == label)

    return StructureResult(
        trend=trend,
        structure_state=state,
        last_event=last_event,
        confidence=round(clamp(confidence), 2),
        latest_swing_high=highs[-1].price if highs else None,
        latest_swing_low=lows[-1].price if lows else None,
        higher_highs=count(SwingLabel.HIGHER_HIGH),
        higher_lows=count(SwingLabel.HIGHER_LOW),
        lower_highs=count(SwingLabel.LOWER_HIGH),
        lower_lows=count(SwingLabel.LOWER_LOW),
        swings=swings[-max_swings_returned:],
        reasons=reasons,
    )
