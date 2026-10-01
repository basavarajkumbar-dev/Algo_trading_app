"""Every threshold and weight used by the price action engine.

Defaults are tuned for intraday 5-minute candles on liquid NSE stocks.
They are starting points to be validated by backtesting (a later
phase), not proven optimal values. All of them are exposed read-only
at GET /api/v1/market/price-action/config.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.market_data.schemas import Timeframe
from app.price_action.schemas import LevelType


class ContextConfig(BaseModel):
    """How a pattern's surroundings are measured."""

    atr_period: int = 14
    trend_lookback: int = 10  # candles before the pattern used for "prior trend"
    trend_threshold_atr: float = 1.0  # net move needed to call the prior trend up/down
    average_window: int = 20  # candles for average range / body / volume
    level_proximity_atr: float = 0.5  # a level this close to the pattern counts as "at" it


class NarrowRangeConfig(BaseModel):
    lookback: int = 7  # NR7: narrowest range of the last 7 candles
    short_lookback: int = 4  # also reported: NR4
    strong_ratio: float = 0.5  # range <= 50% of average counts as strongly narrow


class NarrowBodyConfig(BaseModel):
    max_body_ratio: float = 0.3  # body <= 30% of the candle's own range
    max_body_vs_average: float = 0.5  # and <= 50% of the average body


class WideRangeConfig(BaseModel):
    min_body_vs_average: float = 2.0  # body >= 2x the average body
    min_body_ratio: float = 0.6  # body is most of the candle
    full_strength_multiple: float = 3.5  # body multiple that scores 100 strength
    strong_close_position: float = 0.8  # closes in the outer 20% of its range
    breakout_lookback: int = 10  # closes beyond the high/low of this many candles


class TailConfig(BaseModel):
    min_tail_ratio: float = 0.5  # tail >= 50% of the candle range
    min_tail_vs_body: float = 2.0  # tail >= 2x the body
    min_close_position: float = 0.5  # BT closes in the upper half (TT: lower half)
    min_range_atr: float = 0.5  # ignore tiny candles: range >= 0.5 ATR


class ChangingGuardConfig(BaseModel):
    min_run: int = 3  # consecutive candles of the old colour before the change
    require_progressive_run: bool = True  # bearish run makes lower highs (bullish: higher lows)
    strong_close_position: float = 0.7


class PatternConfig(BaseModel):
    context: ContextConfig = Field(default_factory=ContextConfig)
    narrow_range: NarrowRangeConfig = Field(default_factory=NarrowRangeConfig)
    narrow_body: NarrowBodyConfig = Field(default_factory=NarrowBodyConfig)
    wide_range: WideRangeConfig = Field(default_factory=WideRangeConfig)
    tails: TailConfig = Field(default_factory=TailConfig)
    changing_guard: ChangingGuardConfig = Field(default_factory=ChangingGuardConfig)
    doji_body_ratio: float = 0.1  # body <= 10% of range: no colour
    scan_candles: int = 60  # recent candles scanned for chart markers
    # Confidence adjustments from context (points, added to a detector's base).
    support_bonus: float = 15.0  # pattern direction agrees with the level it sits on
    against_level_penalty: float = 10.0  # bullish pattern at resistance / bearish at support
    reversal_context_bonus: float = 10.0  # tail or COG after a move in the opposite direction
    volume_bonus: float = 10.0
    volume_threshold: float = 1.5  # relative volume that earns the bonus


class SwingConfig(BaseModel):
    fractal_lookback: int = 3  # Phase 4 HH/HL/LH/LL structure
    reversal_atr: float = 1.5  # a significant swing needs a reversal of 1.5 ATR
    atr_period: int = 14
    max_pivots_returned: int = 12


class RetracementConfig(BaseModel):
    levels: tuple[float, ...] = (40.0, 50.0, 60.0, 80.0, 100.0)
    # Zone upper bounds in percent: shallow < 40 <= healthy < 50 <= deep < 60
    # <= weakening < 80 <= major weakness < 100 <= invalidation context.
    zone_bounds: tuple[float, float, float, float, float] = (40.0, 50.0, 60.0, 80.0, 100.0)
    impulse_min_atr: float = 2.5  # an impulse leg must span at least 2.5 ATR
    search_legs: int = 4  # recent legs considered; the largest qualifying one is used
    full_strength_atr: float = 6.0  # impulse size that scores full size-strength
    stale_after_candles: int = 60  # impulses older than this lose confidence
    volume_window: int = 20

    @model_validator(mode="after")
    def _ascending(self) -> RetracementConfig:
        if list(self.zone_bounds) != sorted(self.zone_bounds):
            raise ValueError("zone_bounds must be ascending")
        return self


def _default_type_weights() -> dict[LevelType, float]:
    return {
        LevelType.PREVIOUS_DAY_HIGH: 0.9,
        LevelType.PREVIOUS_DAY_LOW: 0.9,
        LevelType.PREVIOUS_CLOSE: 0.7,
        LevelType.INTRADAY_HIGH: 0.8,
        LevelType.INTRADAY_LOW: 0.8,
        LevelType.SWING_HIGH: 0.7,
        LevelType.SWING_LOW: 0.7,
        LevelType.RANGE_HIGH: 0.65,
        LevelType.RANGE_LOW: 0.65,
        LevelType.BREAKOUT_LEVEL: 0.75,
        LevelType.BREAKDOWN_LEVEL: 0.75,
        LevelType.VWAP: 0.75,
        LevelType.EMA: 0.6,
        LevelType.MA: 0.8,
        LevelType.RETRACEMENT: 0.5,
    }


def _default_timeframe_weights() -> dict[str, float]:
    return {"1d": 1.0, "60m": 0.9, "30m": 0.8, "15m": 0.7, "5m": 0.55, "3m": 0.45, "1m": 0.35}


class LevelStrengthWeights(BaseModel):
    """Points each component contributes to the 0-100 Level Strength
    Score. They are normalised by their sum, so any positive values work."""

    type: float = 30.0
    touches: float = 25.0
    confluence: float = 20.0
    recency: float = 15.0
    timeframe: float = 10.0


class LevelConfig(BaseModel):
    weights: LevelStrengthWeights = Field(default_factory=LevelStrengthWeights)
    type_weights: dict[LevelType, float] = Field(default_factory=_default_type_weights)
    timeframe_weights: dict[str, float] = Field(default_factory=_default_timeframe_weights)
    session_timezone: str = "Asia/Kolkata"  # days for previous-day/intraday levels
    touch_lookback: int = 150  # candles searched for touches
    touch_tolerance_atr: float = 0.1  # a candle within 0.1 ATR of a level touches it
    touch_cap: int = 4  # touches beyond this add nothing
    recency_half_life: float = 30.0  # candles; recency score halves every 30 candles
    confluence_tolerance_atr: float = 0.25
    confluence_cap: int = 3
    range_window: int = 20  # range high/low from this many candles before the latest
    swing_levels: int = 3  # most recent significant swing highs and lows used
    ema_periods: tuple[int, ...] = (20, 50)
    ma_periods: tuple[int, ...] = (200,)
    vwap_anchor_candles: int = 75  # same anchor as the Phase 4 VWAP engine
    max_distance_atr: float = 15.0  # levels further away than this are not returned


class MultiTimeframeConfig(BaseModel):
    context_timeframe: Timeframe = Timeframe.M60
    setup_timeframe: Timeframe = Timeframe.M15
    entry_timeframe: Timeframe = Timeframe.M5
    context_weight: float = 0.5
    setup_weight: float = 0.3
    entry_weight: float = 0.2
    bias_threshold: float = 20.0  # |bias| below this (of 100) reads as sideways
    full_agreement_bias: float = 40.0  # a timeframe this far on the overall side agrees fully
    min_candles: int = 60  # per timeframe
    key_level_max_distance_atr: float = 6.0  # context key levels must be this close to price
    momentum_return_threshold: float = 55.0  # momentum score on the bias side
    volume_confirmation: float = 1.2  # relative volume on the latest entry candle

    @model_validator(mode="after")
    def _ordered(self) -> MultiTimeframeConfig:
        minutes = [
            self.context_timeframe.minutes,
            self.setup_timeframe.minutes,
            self.entry_timeframe.minutes,
        ]
        if not minutes[0] > minutes[1] > minutes[2]:
            raise ValueError("context > setup > entry timeframe is required")
        return self


class PriceActionConfig(BaseModel):
    patterns: PatternConfig = Field(default_factory=PatternConfig)
    swings: SwingConfig = Field(default_factory=SwingConfig)
    retracement: RetracementConfig = Field(default_factory=RetracementConfig)
    levels: LevelConfig = Field(default_factory=LevelConfig)
    multi_timeframe: MultiTimeframeConfig = Field(default_factory=MultiTimeframeConfig)
