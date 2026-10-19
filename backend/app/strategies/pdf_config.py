"""Configuration of the four strategy families from "Best Intraday Trading
Strategies" (howtotrade.com, 2024): range, breakout, volume-based and
double moving-average crossover. The news strategy is deliberately not
implemented.

The PDF describes each family in words and gives no periods, thresholds,
stop or target rules. Every number below is therefore OUR documented,
testable default, not a value from the PDF, and none has been optimised.
They are configuration: change them here (or per backtest), and the
strategy version hash changes with them.

All four are disabled in the live router by default
(STRATEGY_EXTRA_ENABLED enables them); backtests can run each on its own.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.strategies.config import CandidateGeometry


class RangeTradingConfig(BaseModel):
    enabled: bool = False
    lookback: int = Field(default=40, ge=10, le=200)  # candles that define the range
    min_touches: int = Field(default=2, ge=1, le=10)  # visits to each boundary
    touch_tolerance_atr: float = Field(default=0.3, gt=0, le=2)
    min_width_atr: float = Field(default=2.5, gt=0)  # too narrow: no room for 1.5R
    max_width_atr: float = Field(default=10.0, gt=0)
    boundary_zone: float = Field(default=0.25, gt=0, le=0.5)  # outer share of the width
    use_rsi: bool = True
    rsi_period: int = Field(default=14, ge=2, le=50)
    rsi_oversold: float = Field(default=35.0, ge=0, le=50)
    rsi_overbought: float = Field(default=65.0, ge=50, le=100)
    adx_period: int = Field(default=14, ge=5, le=50)
    max_adx: float = Field(default=25.0, gt=0, le=100)  # above: trending, not a range
    target_fraction: float = Field(default=0.8, gt=0, le=1)  # of the way to the far boundary
    stop_buffer_atr: float = Field(default=0.25, ge=0, le=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"touches": 20.0, "rsi": 20.0, "vwap": 10.0, "volume": 10.0}
    )
    geometry: CandidateGeometry = Field(default_factory=CandidateGeometry)


class BreakoutTradingConfig(BaseModel):
    enabled: bool = False
    level_lookback: int = Field(default=20, ge=5, le=200)  # candles before the breakout
    max_level_width_atr: float = Field(default=6.0, gt=0)  # a range, not a trend leg
    use_day_extremes: bool = True  # the day's high/low also count as levels (PDF)
    max_bars_since_breakout: int = Field(default=3, ge=1, le=12)
    min_close_beyond_atr: float = Field(default=0.1, ge=0, le=2)  # a close, not a wick
    min_relative_volume: float = Field(default=1.5, gt=0)  # PDF: confirm with volume
    min_body_fraction: float = Field(default=0.5, ge=0, le=1)  # PDF: strong candle
    min_close_location: float = Field(default=0.7, ge=0.5, le=1)  # close near the extreme
    require_retest: bool = False  # PDF: "or wait for a retest" (optional)
    retest_tolerance_atr: float = Field(default=0.25, gt=0, le=2)
    max_extension_atr: float = Field(default=1.5, gt=0)
    trigger_valid_bars: int = Field(default=2, ge=1, le=12)
    stop_buffer_atr: float = Field(default=0.2, ge=0, le=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"context": 15.0, "vwap": 10.0, "momentum": 10.0, "retest": 10.0}
    )
    geometry: CandidateGeometry = Field(default_factory=CandidateGeometry)


class VolumeTradingConfig(BaseModel):
    enabled: bool = False
    baseline: str = Field(default="time_of_day", pattern="^(time_of_day|rolling)$")
    baseline_lookback: int = Field(default=20, ge=5, le=200)
    baseline_sessions: int = Field(default=10, ge=3, le=60)
    min_relative_volume: float = Field(default=2.0, gt=1)
    trend_fast: int = Field(default=20, ge=2, le=100)
    trend_slow: int = Field(default=50, ge=5, le=300)
    min_body_fraction: float = Field(default=0.5, ge=0, le=1)
    enable_continuation: bool = True
    enable_reversal: bool = True
    zone_tolerance_atr: float = Field(default=0.3, gt=0, le=2)  # near a support/resistance
    min_level_strength: float = Field(default=50.0, ge=0, le=100)
    min_wick_fraction: float = Field(default=0.5, ge=0, le=1)  # rejection wick
    trigger_valid_bars: int = Field(default=2, ge=1, le=12)
    stop_buffer_atr: float = Field(default=0.2, ge=0, le=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"market": 15.0, "vwap": 10.0, "momentum": 10.0, "level": 10.0}
    )
    geometry: CandidateGeometry = Field(default_factory=CandidateGeometry)


class MovingAverageCrossoverConfig(BaseModel):
    enabled: bool = False
    ma_type: str = Field(default="EMA", pattern="^(EMA|SMA)$")
    fast_period: int = Field(default=9, ge=2, le=100)
    slow_period: int = Field(default=21, ge=3, le=400)
    max_bars_since_cross: int = Field(default=3, ge=1, le=20)
    use_adx: bool = True  # PDF: confirm trend strength with ADX
    adx_period: int = Field(default=14, ge=5, le=50)
    min_adx: float = Field(default=20.0, ge=0, le=100)
    choppy_window: int = Field(default=30, ge=10, le=200)
    max_crosses_in_window: int = Field(default=2, ge=1, le=20)  # more = choppy, rejected
    swing_lookback: int = Field(default=5, ge=2, le=50)  # stop beyond this many candles
    stop_buffer_atr: float = Field(default=0.2, ge=0, le=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"vwap": 15.0, "market": 15.0, "volume": 10.0, "di": 10.0}
    )
    geometry: CandidateGeometry = Field(default_factory=CandidateGeometry)

    def model_post_init(self, __context: object) -> None:
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be shorter than slow_period")


__all__ = [
    "BreakoutTradingConfig",
    "MovingAverageCrossoverConfig",
    "RangeTradingConfig",
    "VolumeTradingConfig",
]
