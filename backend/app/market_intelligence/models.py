"""Response-level models produced by MarketIntelligenceService."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.market_data.schemas import Timeframe
from app.market_intelligence.analyzer import SymbolAnalysis
from app.market_intelligence.schemas import Relationship, Trend, VolatilityLevel
from app.regime.schemas import REGIME_DISCLAIMER, MarketRegime, RegimeResult


class FactorScore(BaseModel):
    """One 0-100 factor in the "market brain" panel. Some factors are
    directional (50 = neutral) and some measure intensity; `scale`
    says which, so a score is never read out of context."""

    key: str
    label: str
    score: float | None
    scale: str  # "directional" (50 = neutral) or "intensity" (0 = none, 100 = maximum)
    interpretation: str
    detail: str | None = None


class SymbolRegimeSummary(BaseModel):
    symbol: str
    name: str | None
    price: float | None
    change_percent: float | None
    regime: MarketRegime
    confidence: float
    top_reason: str | None
    trend_quality_score: float | None
    trend_direction: Trend | None
    momentum_score: float | None
    relative_strength_score: float | None
    relative_strength_rank: int | None
    price_vs_vwap: Relationship | None
    volatility_level: VolatilityLevel | None
    relative_volume: float | None
    data_as_of: datetime | None


class SymbolIntelligence(BaseModel):
    symbol: str
    name: str | None
    price: float | None
    change_percent: float | None
    regime: RegimeResult
    factors: list[FactorScore]
    relative_strength_rank: int | None
    analysis: SymbolAnalysis


class MarketBreadth(BaseModel):
    symbols_counted: int
    advancing: int
    declining: int
    unchanged: int
    above_vwap: int
    below_vwap: int
    percent_above_vwap: float | None
    advance_decline_ratio: float | None
    bullish_regimes: int
    bearish_regimes: int


class MarketSnapshot(BaseModel):
    """Market-level picture, built from the benchmark (NIFTY50) and the
    breadth of its constituents."""

    benchmark_symbol: str
    timeframe: Timeframe
    market_direction: Trend
    market_regime: MarketRegime
    confidence: float
    volatility: VolatilityLevel | None
    trend_strength: float | None
    momentum: float | None
    breadth: MarketBreadth
    regime_distribution: dict[MarketRegime, int]
    factors: list[FactorScore]
    regime: RegimeResult
    timestamp: datetime
    note: str = REGIME_DISCLAIMER


class IntelligenceServiceStatus(BaseModel):
    is_running: bool
    timeframe: Timeframe
    refresh_seconds: float
    refresh_count: int
    last_refresh_at: datetime | None
    last_compute_ms: float | None
    last_error: str | None
    symbols_analyzed: int


class MarketIntelligenceResponse(BaseModel):
    snapshot: MarketSnapshot
    symbols: list[SymbolRegimeSummary] = Field(default_factory=list)
    status: IntelligenceServiceStatus
