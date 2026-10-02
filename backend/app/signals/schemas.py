"""Signal engine models.

A TradingSignal is a scored, explained strategy candidate. It is not an
order: nothing here sizes for a real account, checks risk limits or
talks to a broker. In the brief's flow it sits at
SIGNAL -> CONFIDENCE SCORING -> CANDIDATE RANKING, before the Risk
Engine of a later phase.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.market_data.schemas import Timeframe
from app.regime.schemas import MarketRegime
from app.strategies.schemas import FeatureValue, StrategyCheck, StrategyName

SIGNAL_NOTE = (
    "Signals are scored strategy candidates, not orders. Scores and ranks "
    "describe how much of each strategy's evidence is present; they are "
    "not proof of profitability."
)


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class SignalCategory(str, Enum):
    ELITE = "ELITE"
    STRONG = "STRONG"
    WATCH = "WATCH"
    IGNORE = "IGNORE"


CATEGORY_ORDER = [
    SignalCategory.IGNORE,
    SignalCategory.WATCH,
    SignalCategory.STRONG,
    SignalCategory.ELITE,
]


class SignalStatus(str, Enum):
    ACTIVE = "ACTIVE"  # the setup is still present in the latest scan
    EXPIRED = "EXPIRED"  # the setup has disappeared since


class ScoreComponent(BaseModel):
    key: str
    label: str
    weight: float  # configured points
    applicable: bool
    effective_weight: float  # after scaling applicable weights to 100
    score: float | None  # 0-1, None when not applicable
    points: float  # contribution to the 0-100 total
    passed: bool
    detail: str


class ScoreBreakdown(BaseModel):
    total: float  # 0-100
    category: SignalCategory
    executable: bool
    executable_threshold: float
    components: list[ScoreComponent]


class TradingSignal(BaseModel):
    """One scored candidate. `id` is set once the signal is stored."""

    id: uuid.UUID | None = None
    symbol: str
    direction: SignalDirection
    strategy: StrategyName
    strategy_version: str
    timeframe: Timeframe
    entry: float
    stop_loss: float
    target: float
    risk_reward: float
    quantity_candidate: int
    quantity_note: str
    score: float
    category: SignalCategory
    executable: bool
    confidence: float  # the strategy's own confidence, kept separately from the score
    # Ranking metrics, taken from the analysis context for every strategy.
    relative_strength: float | None = None  # 0-100, 50 = in line with NIFTY 50
    trend_quality: float | None = None
    relative_volume: float | None = None
    liquidity: float | None = None  # average traded value per candle
    market_regime: MarketRegime
    regime_confidence: float
    breakdown: ScoreBreakdown
    reasons: list[str] = Field(default_factory=list)  # "why this signal", one line per component
    strategy_reasons: list[str] = Field(default_factory=list)
    strategy_checks: list[StrategyCheck] = Field(default_factory=list)
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    candle_time: datetime  # the closed candle the signal was computed on
    timestamp: datetime  # when it was generated
    status: SignalStatus = SignalStatus.ACTIVE
    note: str = SIGNAL_NOTE

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of the setup: one ACTIVE signal per symbol, strategy
        and direction at a time."""

        return (self.symbol, self.strategy.value, self.direction.value)


class TradeCandidate(BaseModel):
    """A ranked signal (the brief's TradeCandidate)."""

    rank: int
    signal_id: uuid.UUID | None
    symbol: str
    direction: SignalDirection
    strategy: StrategyName
    score: float
    category: SignalCategory
    status: str  # READY (executable) / WATCH / IGNORE
    entry: float
    stop_loss: float
    target: float
    risk_reward: float
    quantity_candidate: int
    relative_strength: float | None
    liquidity: float | None  # average turnover per candle (price x volume)
    trend_quality: float | None
    relative_volume: float | None
    strategy_confidence: float
    market_regime: MarketRegime


class MarketContext(BaseModel):
    """The broad market (NIFTY 50 index) at scan time."""

    benchmark_symbol: str
    available: bool
    regime: MarketRegime | None = None
    regime_confidence: float | None = None
    bias_score: float | None = None  # multi-timeframe bias, -100..+100
    direction: str | None = None  # UP / DOWN / SIDEWAYS
    price: float | None = None
    change_percent: float | None = None
