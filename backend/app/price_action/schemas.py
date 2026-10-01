"""Shared models for the price action engine.

Price action detectors describe what a candle (or a short run of
candles) looks like *in context*. They are features for later scoring
layers, never trade signals: nothing in this package produces an
order, an entry price, a stop or a position size, and a detected
pattern does not imply that price will move in its direction.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.market_intelligence.schemas import Trend

PATTERN_DISCLAIMER = (
    "Price action patterns are contextual features, not trade signals. "
    "They describe recent candles and do not predict future movement."
)

LEVEL_DISCLAIMER = (
    "Support, resistance and retracement levels are reference zones, "
    "not guaranteed reversal points."
)


class PatternType(str, Enum):
    COG = "COG"  # Changing of the Guard
    NR = "NR"  # Narrow Range
    NRB = "NRB"  # Narrow Range Body
    WRB = "WRB"  # Wide Range Body
    BT = "BT"  # Bottoming Tail
    TT = "TT"  # Topping Tail


PATTERN_NAMES: dict[PatternType, str] = {
    PatternType.COG: "Changing of the Guard",
    PatternType.NR: "Narrow Range",
    PatternType.NRB: "Narrow Range Body",
    PatternType.WRB: "Wide Range Body",
    PatternType.BT: "Bottoming Tail",
    PatternType.TT: "Topping Tail",
}


class PatternDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class CandleColor(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    DOJI = "DOJI"


class LevelLocation(str, Enum):
    AT_SUPPORT = "AT_SUPPORT"
    AT_RESISTANCE = "AT_RESISTANCE"
    THROUGH_LEVEL = "THROUGH_LEVEL"  # the candle's body crossed the level (e.g. a breakout candle)
    BETWEEN_LEVELS = "BETWEEN_LEVELS"
    NO_LEVELS = "NO_LEVELS"


class ReferenceLevel(BaseModel):
    """A price level used as context for a pattern (e.g. a support
    level from the S/R engine). Kept minimal so detectors do not depend
    on the S/R engine itself."""

    label: str
    price: float


class NearbyLevel(BaseModel):
    label: str
    price: float
    distance_atr: float


class PatternContext(BaseModel):
    """Where the pattern happened. Context changes how much a pattern is
    worth: a bottoming tail after a decline into support says more than
    one in the middle of nowhere."""

    prior_trend: Trend
    prior_move_atr: float | None  # net move over the context window, in ATR units
    atr: float | None
    range_vs_average: float | None  # this candle's range / average range
    relative_volume: float | None
    location: LevelLocation
    nearest_level: NearbyLevel | None = None
    levels_considered: bool = False  # False for historical scans (no look-ahead levels)


class PatternResult(BaseModel):
    pattern: PatternType
    name: str
    detected: bool
    direction: PatternDirection
    confidence: float  # 0-100: how clear the reading is, including context
    strength: float  # 0-100: intrinsic magnitude of the pattern itself
    context: PatternContext
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    candle_index: int
    timestamp: datetime


class PatternMarker(BaseModel):
    """A detected pattern on a past candle, for charting."""

    pattern: PatternType
    direction: PatternDirection
    confidence: float
    strength: float
    timestamp: datetime
    price: float  # where to draw it: the low for bullish, the high otherwise


class LevelType(str, Enum):
    PREVIOUS_DAY_HIGH = "PREVIOUS_DAY_HIGH"
    PREVIOUS_DAY_LOW = "PREVIOUS_DAY_LOW"
    PREVIOUS_CLOSE = "PREVIOUS_CLOSE"
    INTRADAY_HIGH = "INTRADAY_HIGH"
    INTRADAY_LOW = "INTRADAY_LOW"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    RANGE_HIGH = "RANGE_HIGH"
    RANGE_LOW = "RANGE_LOW"
    BREAKOUT_LEVEL = "BREAKOUT_LEVEL"  # a range high that price has closed above
    BREAKDOWN_LEVEL = "BREAKDOWN_LEVEL"  # a range low that price has closed below
    VWAP = "VWAP"
    EMA = "EMA"
    MA = "MA"
    RETRACEMENT = "RETRACEMENT"


class RetracementZone(str, Enum):
    SHALLOW = "SHALLOW"  # < 40%
    HEALTHY_PULLBACK = "HEALTHY_PULLBACK"  # 40-50%
    DEEP_PULLBACK = "DEEP_PULLBACK"  # 50-60%
    WEAKENING = "WEAKENING"  # 60-80%
    MAJOR_WEAKNESS = "MAJOR_WEAKNESS"  # 80-100%
    INVALIDATION_CONTEXT = "INVALIDATION_CONTEXT"  # >= 100%: the impulse is fully retraced
