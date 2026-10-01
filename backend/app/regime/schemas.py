"""Market regime schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.market_data.schemas import Timeframe

REGIME_DISCLAIMER = (
    "A regime describes recent price behaviour. It is not a forecast and "
    "does not guarantee future price movement."
)


class MarketRegime(str, Enum):
    STRONG_BULLISH = "STRONG_BULLISH"
    STRONG_BEARISH = "STRONG_BEARISH"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class RegimeCondition(BaseModel):
    """One weighted piece of evidence for a regime."""

    key: str
    weight: float
    met: bool
    description: str


class RegimeScore(BaseModel):
    regime: MarketRegime
    score: float  # 0-100: sum of weights of met conditions / total weight
    conditions: list[RegimeCondition]


FeatureValue = float | int | str | bool | None


class RegimeResult(BaseModel):
    symbol: str
    timeframe: Timeframe
    regime: MarketRegime
    confidence: float  # 0-100
    reasons: list[str] = Field(default_factory=list)
    counter_reasons: list[str] = Field(default_factory=list)
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    scores: dict[MarketRegime, float] = Field(default_factory=dict)
    candles_analyzed: int
    data_as_of: datetime | None
    timestamp: datetime
    note: str = REGIME_DISCLAIMER
