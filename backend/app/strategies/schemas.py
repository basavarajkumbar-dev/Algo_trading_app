"""Strategy engine models.

A strategy evaluates one symbol and returns a `StrategyResult`: whether
its setup is present, the direction, and *candidate* entry, stop and
target prices. Every strategy is a testable hypothesis, not a proven
edge. Candidates are not orders: they are not sized, not risk-checked
and never sent to a broker. In the architecture from the brief they
feed STRATEGY -> SIGNAL -> RISK, all of which come in later phases.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.market_data.schemas import Timeframe
from app.regime.schemas import MarketRegime

CANDIDATE_NOTE = (
    "Strategy candidates are hypotheses, not orders. They are not sized, "
    "not risk-checked and never sent to a broker, and no strategy is "
    "assumed to be profitable."
)

FeatureValue = float | int | str | bool | None


class StrategyName(str, Enum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT_MOMENTUM = "BREAKOUT_MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    # The four families from "Best Intraday Trading Strategies" (2026-10).
    RANGE_TRADING = "RANGE_TRADING"
    BREAKOUT_TRADING = "BREAKOUT_TRADING"
    VOLUME_TRADING = "VOLUME_TRADING"
    MA_CROSSOVER = "MA_CROSSOVER"


#: Added strategies, off in the live router unless STRATEGY_EXTRA_ENABLED.
EXTRA_STRATEGIES: tuple[StrategyName, ...] = (
    StrategyName.RANGE_TRADING,
    StrategyName.BREAKOUT_TRADING,
    StrategyName.VOLUME_TRADING,
    StrategyName.MA_CROSSOVER,
)


STRATEGY_LABELS: dict[StrategyName, str] = {
    StrategyName.TREND_CONTINUATION: "Trend continuation",
    StrategyName.BREAKOUT_MOMENTUM: "Breakout momentum",
    StrategyName.MEAN_REVERSION: "Mean reversion",
    StrategyName.RANGE_TRADING: "Range trading",
    StrategyName.BREAKOUT_TRADING: "Breakout trading (confirmed trigger)",
    StrategyName.VOLUME_TRADING: "Volume-based trading",
    StrategyName.MA_CROSSOVER: "Moving-average crossover",
}


class EntryType(str, Enum):
    """How a candidate's entry is executed.

    MARKET: enter at the next price after the signal candle has closed
            (breakout momentum, mean reversion: the closed candle is the
            confirmation).
    STOP:   enter only if price trades through `entry_candidate` (the
            trigger) within `entry_valid_bars` candles; otherwise no trade.
            Used where the setup's own definition is "enter beyond a level"
            (trend continuation: beyond the confirmation candle).
    """

    MARKET = "MARKET"
    STOP = "STOP"


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class StrategyCheck(BaseModel):
    """One condition a strategy looked at.

    `required` checks gate eligibility: all must pass. Optional checks
    only add confidence, in proportion to their `weight`."""

    key: str
    label: str
    passed: bool
    required: bool
    weight: float = 0.0
    detail: str


class StrategyResult(BaseModel):
    strategy: StrategyName
    strategy_version: str = "unversioned"  # "<semver>+<config hash>", see BaseStrategy.version_id
    symbol: str
    timeframe: Timeframe
    eligible: bool
    direction: TradeDirection
    entry_candidate: float | None = None
    stop_candidate: float | None = None
    target_candidate: float | None = None
    risk_per_unit: float | None = None  # |entry - stop|
    reward_per_unit: float | None = None  # |target - entry|
    reward_risk: float | None = None
    entry_basis: str | None = None
    entry_type: EntryType = EntryType.MARKET
    entry_valid_bars: int | None = None  # STOP entries: trigger validity in candles
    stop_basis: str | None = None
    target_basis: str | None = None
    confidence: float = 0.0  # 0-100, only non-zero when eligible
    setup_score: float = 0.0  # 0-100, share of weighted checks met, eligible or not
    stage: str | None = None  # furthest stage of the strategy's concept reached
    checks: list[StrategyCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)  # what supports the setup
    blockers: list[str] = Field(default_factory=list)  # why it is not eligible
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    data_as_of: datetime | None = None
    timestamp: datetime
    note: str = CANDIDATE_NOTE


class RoutingEntry(BaseModel):
    strategy: StrategyName
    routed: bool
    reason: str
    allowed_directions: list[TradeDirection] = Field(default_factory=list)


class RouterDecision(BaseModel):
    """Which strategies may run for a regime. The order is the
    configured evaluation order, not a ranking: the router makes no
    claim that any strategy is better than another."""

    regime: MarketRegime
    regime_confidence: float
    eligible_strategies: list[StrategyName]
    entries: list[RoutingEntry]
    reasons: list[str] = Field(default_factory=list)
