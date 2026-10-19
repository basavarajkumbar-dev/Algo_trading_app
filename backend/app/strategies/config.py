"""Strategy engine configuration.

Every threshold below is a starting hypothesis for intraday 5-minute
candles, to be validated by backtesting in a later phase, not a proven
value. All of it is exposed read-only at GET /api/v1/strategies/config.
"""

from __future__ import annotations

from datetime import time

from pydantic import BaseModel, Field, field_validator, model_validator

from app.regime.schemas import MarketRegime
from app.strategies.schemas import StrategyName, TradeDirection


class CandidateGeometry(BaseModel):
    """Shared rules for turning a setup into entry/stop/target candidates."""

    stop_buffer_atr: float = 0.2  # stop placed this far beyond the protective level
    min_stop_atr: float = 0.5  # stops tighter than this sit inside normal noise
    max_stop_atr: float = 3.0  # wider stops make the setup ineligible
    min_reward_risk: float = 1.5  # structure targets must offer at least this
    default_reward_risk: float = 2.0  # used when no structure target qualifies


# Imported here, after CandidateGeometry: pdf_config imports it from this module.
from app.strategies.pdf_config import (  # noqa: E402
    BreakoutTradingConfig,
    MovingAverageCrossoverConfig,
    RangeTradingConfig,
    VolumeTradingConfig,
)


class TrendContinuationConfig(BaseModel):
    enabled: bool = True
    min_pullback_percent: float = 30.0  # the impulse must have been retraced at least this much
    max_pullback_percent: float = 65.0  # deeper pullbacks are treated as weakening, not healthy
    max_candles_since_extreme: int = 40  # older impulses are stale
    support_proximity_atr: float = 0.75  # a support level this close to the pullback low counts
    min_trend_quality: float = 40.0
    min_relative_strength: float = 55.0  # RS score on the trend side (longs > 55, shorts < 45)
    min_alignment: float = 50.0  # multi-timeframe alignment score
    confirmation_patterns: tuple[str, ...] = ("COG", "BT", "TT", "WRB")
    # The entry is a stop trigger beyond the confirmation candle, valid for
    # this many candles after it; untriggered by then = no trade.
    trigger_valid_bars: int = Field(default=2, ge=1, le=12)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "regime": 10.0,
            "higher_timeframe": 15.0,
            "structure": 10.0,
            "vwap": 10.0,
            "trend_quality": 10.0,
            "support": 10.0,
            "volume": 5.0,
            "momentum": 10.0,
            "relative_strength": 10.0,
            "pattern": 10.0,
        }
    )
    geometry: CandidateGeometry = Field(default_factory=CandidateGeometry)


class BreakoutMomentumConfig(BaseModel):
    enabled: bool = True
    opening_range_minutes: int = 15  # 5, 15 or 30 in the brief; any multiple of 5 works
    session_start: time = time(9, 15)  # NSE cash market open, IST
    session_timezone: str = "Asia/Kolkata"
    consolidation_window: int = 20  # candles before the breakout candle
    max_consolidation_width_atr: float = 4.0  # wider "ranges" are not consolidations
    max_candles_since_breakout: int = 2
    max_extension_atr: float = 1.5  # beyond this past the level it is chasing, not breaking out
    min_relative_volume: float = 1.3
    expansion_range_multiple: float = 1.3  # breakout candle range vs average range
    compression_lookback: int = 12  # candles searched for NR / NRB before the breakout
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "consolidation": 15.0,
            "compression": 10.0,
            "volume": 15.0,
            "expansion": 15.0,
            "momentum": 15.0,
            "vwap": 10.0,
            "opening_range": 10.0,
            "higher_timeframe": 10.0,
        }
    )
    geometry: CandidateGeometry = Field(default_factory=CandidateGeometry)

    @field_validator("opening_range_minutes")
    @classmethod
    def _multiple_of_five(cls, value: int) -> int:
        if value <= 0 or value % 5 != 0:
            raise ValueError("opening_range_minutes must be a positive multiple of 5")
        return value


class MeanReversionConfig(BaseModel):
    enabled: bool = True
    # "Do not mean-revert strong trends": any one of these makes it ineligible.
    max_trend_quality: float = 50.0
    max_efficiency_ratio: float = 0.4  # directional efficiency of recent movement
    max_higher_timeframe_bias: float = 50.0  # |context bias| above this is a strong trend
    boundary_proximity_atr: float = 0.5  # how close to the range edge counts as "at" it
    # Price must be stretched this far from VWAP. Kept modest: range edges in a tight
    # range sit only about 1 ATR from the mean, and the room to VWAP is enforced
    # separately by the reward/risk check on the VWAP target.
    min_vwap_deviation_atr: float = 0.5
    min_extension_atr: float = 0.75  # distance from the fast EMA
    reversal_patterns: tuple[str, ...] = ("BT", "TT", "COG", "WRB")
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "range_regime": 15.0,
            "vwap_deviation": 15.0,
            "extension": 10.0,
            "slowdown": 15.0,
            "reversal": 20.0,
            "level": 15.0,
            "volume": 10.0,
        }
    )
    geometry: CandidateGeometry = Field(
        default_factory=lambda: CandidateGeometry(min_reward_risk=1.0, default_reward_risk=1.0)
    )


class RouteRule(BaseModel):
    strategy: StrategyName
    directions: list[TradeDirection] = Field(
        default_factory=lambda: [TradeDirection.LONG, TradeDirection.SHORT]
    )


def _default_routes() -> dict[MarketRegime, list[RouteRule]]:
    """Based on the brief's example mapping (see the RANGE note).
    HIGH_VOLATILITY and UNKNOWN route to nothing (defensive / no trade)."""

    return {
        MarketRegime.STRONG_BULLISH: [
            RouteRule(strategy=StrategyName.TREND_CONTINUATION, directions=[TradeDirection.LONG])
        ],
        MarketRegime.STRONG_BEARISH: [
            RouteRule(strategy=StrategyName.TREND_CONTINUATION, directions=[TradeDirection.SHORT])
        ],
        MarketRegime.BREAKOUT: [RouteRule(strategy=StrategyName.BREAKOUT_MOMENTUM)],
        # The brief's example maps RANGE to mean reversion only. Breakout
        # momentum is added because its own concept starts from a
        # consolidation (a range): the regime detector only says BREAKOUT
        # once a large breakout candle has printed, by which time entry is
        # usually extended. The two cannot fire together: one needs a
        # reversal inside the range, the other a close outside it.
        MarketRegime.RANGE: [
            RouteRule(strategy=StrategyName.MEAN_REVERSION),
            RouteRule(strategy=StrategyName.BREAKOUT_MOMENTUM),
        ],
        MarketRegime.HIGH_VOLATILITY: [],
        MarketRegime.UNKNOWN: [],
    }


def _extra_routes() -> dict[MarketRegime, list[RouteRule]]:
    """Where the four added strategies may run. They are disabled by
    default, so these rules change nothing until a strategy is enabled."""

    long_only, short_only = [TradeDirection.LONG], [TradeDirection.SHORT]
    return {
        MarketRegime.RANGE: [
            RouteRule(strategy=StrategyName.RANGE_TRADING),
            RouteRule(strategy=StrategyName.BREAKOUT_TRADING),
        ],
        MarketRegime.BREAKOUT: [
            RouteRule(strategy=StrategyName.BREAKOUT_TRADING),
            RouteRule(strategy=StrategyName.VOLUME_TRADING),
            RouteRule(strategy=StrategyName.MA_CROSSOVER),
        ],
        MarketRegime.STRONG_BULLISH: [
            RouteRule(strategy=StrategyName.VOLUME_TRADING, directions=long_only),
            RouteRule(strategy=StrategyName.MA_CROSSOVER, directions=long_only),
        ],
        MarketRegime.STRONG_BEARISH: [
            RouteRule(strategy=StrategyName.VOLUME_TRADING, directions=short_only),
            RouteRule(strategy=StrategyName.MA_CROSSOVER, directions=short_only),
        ],
    }


def _all_routes() -> dict[MarketRegime, list[RouteRule]]:
    routes = _default_routes()
    for regime, rules in _extra_routes().items():
        routes[regime] = [*routes.get(regime, []), *rules]
    return routes


class RouterConfig(BaseModel):
    routes: dict[MarketRegime, list[RouteRule]] = Field(default_factory=_all_routes)
    min_regime_confidence: float = 50.0  # below this nothing is routed

    @model_validator(mode="after")
    def _every_regime(self) -> RouterConfig:
        for regime in MarketRegime:
            self.routes.setdefault(regime, [])
        return self


class StrategyEngineConfig(BaseModel):
    router: RouterConfig = Field(default_factory=RouterConfig)
    trend_continuation: TrendContinuationConfig = Field(default_factory=TrendContinuationConfig)
    breakout_momentum: BreakoutMomentumConfig = Field(default_factory=BreakoutMomentumConfig)
    mean_reversion: MeanReversionConfig = Field(default_factory=MeanReversionConfig)
    # The four PDF strategy families (disabled by default; see pdf_config.py).
    range_trading: RangeTradingConfig = Field(default_factory=RangeTradingConfig)
    breakout_trading: BreakoutTradingConfig = Field(default_factory=BreakoutTradingConfig)
    volume_trading: VolumeTradingConfig = Field(default_factory=VolumeTradingConfig)
    ma_crossover: MovingAverageCrossoverConfig = Field(default_factory=MovingAverageCrossoverConfig)
