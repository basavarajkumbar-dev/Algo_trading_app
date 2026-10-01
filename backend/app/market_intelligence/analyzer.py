"""Runs every market intelligence engine over one symbol's candles.

Each engine is independent: if one lacks data (InsufficientDataError)
its result is None and the reason is recorded in `unavailable`, while
the others still run. Nothing is fabricated to fill a gap.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, Field

from app.market_data.schemas import Candle, Timeframe
from app.market_intelligence.momentum import MomentumResult, analyze_momentum
from app.market_intelligence.moving_average import MovingAverageResult, analyze_moving_averages
from app.market_intelligence.range_analysis import RangeResult, analyze_range
from app.market_intelligence.relative_strength import (
    RelativeStrengthResult,
    analyze_relative_strength,
)
from app.market_intelligence.schemas import InsufficientDataError
from app.market_intelligence.structure import StructureResult, analyze_structure
from app.market_intelligence.trend_quality import (
    TrendQualityResult,
    TrendQualityWeights,
    compute_trend_quality,
)
from app.market_intelligence.volatility import VolatilityResult, analyze_volatility
from app.market_intelligence.volume import VolumeResult, analyze_volume
from app.market_intelligence.vwap import VWAPResult, analyze_vwap


class AnalysisConfig(BaseModel):
    """Engine parameters. Defaults follow the project brief (EMA 20/50,
    MA 200) and are tuned for intraday 5-minute candles."""

    vwap_anchor_candles: int = 75  # 75 x 5m = one NSE session (09:15-15:30)
    ema_fast_period: int = 20
    ema_slow_period: int = 50
    ma_period: int = 200
    slope_lookback: int = 5
    atr_period: int = 14
    volatility_baseline_period: int = 100
    swing_lookback: int = 3
    range_window: int = 12
    efficiency_window: int = 20
    relative_strength_lookback: int = 20
    momentum_lookbacks: tuple[tuple[int, float], ...] = ((3, 0.5), (10, 0.3), (20, 0.2))
    volume_average_window: int = 20
    trend_quality_weights: TrendQualityWeights = Field(default_factory=TrendQualityWeights)


class SymbolAnalysis(BaseModel):
    symbol: str
    timeframe: Timeframe
    candles_analyzed: int
    price: float | None
    data_as_of: datetime | None
    vwap: VWAPResult | None = None
    moving_averages: MovingAverageResult | None = None
    volatility: VolatilityResult | None = None
    momentum: MomentumResult | None = None
    volume: VolumeResult | None = None
    structure: StructureResult | None = None
    range: RangeResult | None = None
    relative_strength: RelativeStrengthResult | None = None
    trend_quality: TrendQualityResult | None = None
    unavailable: dict[str, str] = Field(default_factory=dict)


def analyze_candles(
    symbol: str,
    timeframe: Timeframe,
    candles: list[Candle],
    *,
    benchmark_candles: list[Candle] | None = None,
    config: AnalysisConfig | None = None,
) -> SymbolAnalysis:
    cfg = config or AnalysisConfig()
    result = SymbolAnalysis(
        symbol=symbol,
        timeframe=timeframe,
        candles_analyzed=len(candles),
        price=candles[-1].close if candles else None,
        data_as_of=candles[-1].timestamp if candles else None,
    )

    def run[T](name: str, fn: Callable[[], T]) -> T | None:
        try:
            return fn()
        except InsufficientDataError as exc:
            result.unavailable[name] = str(exc)
            return None

    result.vwap = run("vwap", lambda: analyze_vwap(candles[-cfg.vwap_anchor_candles :]))
    result.moving_averages = run(
        "moving_averages",
        lambda: analyze_moving_averages(
            candles,
            ema_fast_period=cfg.ema_fast_period,
            ema_slow_period=cfg.ema_slow_period,
            ma_period=cfg.ma_period,
            slope_lookback=cfg.slope_lookback,
        ),
    )
    result.volatility = run(
        "volatility",
        lambda: analyze_volatility(
            candles, period=cfg.atr_period, baseline_period=cfg.volatility_baseline_period
        ),
    )
    result.momentum = run(
        "momentum", lambda: analyze_momentum(candles, lookbacks=cfg.momentum_lookbacks)
    )
    result.volume = run(
        "volume", lambda: analyze_volume(candles, average_window=cfg.volume_average_window)
    )
    result.structure = run(
        "structure", lambda: analyze_structure(candles, lookback=cfg.swing_lookback)
    )
    result.range = run(
        "range",
        lambda: analyze_range(
            candles,
            window=cfg.range_window,
            baseline_period=cfg.volatility_baseline_period,
            efficiency_window=cfg.efficiency_window,
        ),
    )
    if benchmark_candles:
        result.relative_strength = run(
            "relative_strength",
            lambda: analyze_relative_strength(
                candles, benchmark_candles, lookback=cfg.relative_strength_lookback
            ),
        )
    if result.moving_averages is not None:
        ma = result.moving_averages
        result.trend_quality = compute_trend_quality(
            ma,
            result.structure,
            result.momentum,
            result.volume,
            result.volatility,
            weights=cfg.trend_quality_weights,
        )
    else:
        result.unavailable["trend_quality"] = "Needs moving-average analysis."
    return result
