"""Hand-built StrategyContexts for strategy tests.

Each scenario sets the engine outputs a strategy reads explicitly, so
a test can flip one input and know exactly which check should change.
Baseline scenarios are clean, eligible setups; tests then break them.
ATR is fixed at 1.0 so prices read directly as ATR multiples.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.market_data.schemas import Timeframe
from app.market_intelligence.analyzer import SymbolAnalysis
from app.market_intelligence.momentum import MomentumResult
from app.market_intelligence.moving_average import MovingAverageResult
from app.market_intelligence.range_analysis import RangeResult
from app.market_intelligence.relative_strength import RelativeStrengthResult
from app.market_intelligence.schemas import Relationship, Trend
from app.market_intelligence.structure import StructureResult
from app.market_intelligence.trend_quality import TrendQualityResult
from app.market_intelligence.volatility import VolatilityResult
from app.market_intelligence.volume import VolumeResult
from app.market_intelligence.vwap import VWAPResult
from app.price_action.analyzer import PriceActionAnalysis
from app.price_action.multi_timeframe import (
    EntryConfirmation,
    MultiTimeframeResult,
    TimeframeAnalysis,
    TimeframeRole,
)
from app.price_action.retracement import RetracementResult
from app.price_action.schemas import (
    LevelLocation,
    LevelType,
    PatternContext,
    PatternDirection,
    PatternMarker,
    PatternResult,
    PatternType,
    RetracementZone,
)
from app.price_action.support_resistance import LevelRole, PriceLevel, SupportResistanceResult
from app.regime.schemas import MarketRegime, RegimeResult
from app.strategies.context import StrategyContext
from app.tests.candle_factory import candles_from_ohlc

NOW = datetime(2026, 1, 6, 9, 0, tzinfo=UTC)  # 14:30 IST
Row = tuple[float, float, float, float]


# --- building blocks -------------------------------------------------------------------


def level(
    label: str, level_type: LevelType, price: float, close: float, strength: float = 60.0
) -> PriceLevel:
    return PriceLevel(
        label=label,
        type=level_type,
        price=price,
        role=LevelRole.SUPPORT if price <= close else LevelRole.RESISTANCE,
        distance_percent=0.0,
        distance_atr=price - close,
        strength=strength,
        touch_count=2,
        timeframe="5m",
        recency=3,
    )


def pattern(kind: PatternType, direction: PatternDirection, detected: bool = True) -> PatternResult:
    return PatternResult(
        pattern=kind,
        name=kind.value,
        detected=detected,
        direction=direction,
        confidence=70.0 if detected else 0.0,
        strength=60.0 if detected else 0.0,
        context=PatternContext(
            prior_trend=Trend.SIDEWAYS,
            prior_move_atr=0.0,
            atr=1.0,
            range_vs_average=1.0,
            relative_volume=1.0,
            location=LevelLocation.NO_LEVELS,
        ),
        candle_index=0,
        timestamp=NOW,
    )


def timeframe(
    role: TimeframeRole, tf: Timeframe, direction: Trend, bias: float, **extra: Any
) -> TimeframeAnalysis:
    return TimeframeAnalysis(
        role=role, timeframe=tf, available=True, direction=direction, bias=bias, **extra
    )


def mtf(
    context: Trend = Trend.UP,
    context_bias: float = 60.0,
    alignment: float = 90.0,
    momentum_returning: bool = True,
) -> MultiTimeframeResult:
    return MultiTimeframeResult(
        symbol="TEST",
        overall_bias=PatternDirection.BULLISH if context == Trend.UP else PatternDirection.BEARISH,
        bias_score=context_bias,
        alignment_score=alignment,
        context_analysis=timeframe(TimeframeRole.CONTEXT, Timeframe.M60, context, context_bias),
        setup_analysis=timeframe(TimeframeRole.SETUP, Timeframe.M15, context, context_bias),
        entry_analysis=timeframe(
            TimeframeRole.ENTRY,
            Timeframe.M5,
            context,
            context_bias,
            confirmation=EntryConfirmation(
                reference_bias=PatternDirection.BULLISH,
                momentum_returning=momentum_returning,
            ),
        ),
        timestamp=NOW,
    )


def analysis(
    *,
    price: float,
    price_vs_vwap: Relationship = Relationship.ABOVE,
    vwap: float = 99.0,
    structure: Trend = Trend.UP,
    trend_quality: float = 70.0,
    trend_direction: Trend = Trend.UP,
    momentum: float = 65.0,
    rocs: dict[int, float] | None = None,
    relative_volume: float = 1.3,
    relative_strength: float | None = 70.0,
    ema_fast: float = 100.0,
    efficiency: float = 0.6,
    expanding: bool = False,
    candles: int = 120,
) -> SymbolAnalysis:
    return SymbolAnalysis(
        symbol="TEST",
        timeframe=Timeframe.M5,
        candles_analyzed=candles,
        price=price,
        data_as_of=NOW,
        vwap=VWAPResult.model_construct(vwap=vwap, price_vs_vwap=price_vs_vwap),
        moving_averages=MovingAverageResult.model_construct(ema_fast=ema_fast),
        volatility=VolatilityResult.model_construct(is_expanding=expanding),
        momentum=MomentumResult.model_construct(
            momentum_score=momentum, rate_of_change_percent=rocs or {3: 0.3, 10: 0.5, 20: 0.8}
        ),
        volume=VolumeResult.model_construct(relative_volume=relative_volume),
        structure=StructureResult.model_construct(trend=structure),
        range=RangeResult.model_construct(efficiency_ratio=efficiency),
        relative_strength=(
            RelativeStrengthResult.model_construct(relative_strength_score=relative_strength)
            if relative_strength is not None
            else None
        ),
        trend_quality=TrendQualityResult.model_construct(
            score=trend_quality, direction=trend_direction
        ),
    )


def regime(kind: MarketRegime, confidence: float = 80.0) -> RegimeResult:
    return RegimeResult(
        symbol="TEST",
        timeframe=Timeframe.M5,
        regime=kind,
        confidence=confidence,
        candles_analyzed=120,
        data_as_of=NOW,
        timestamp=NOW,
    )


def context(
    rows: list[Row],
    *,
    analysis_: SymbolAnalysis,
    regime_: RegimeResult,
    patterns: list[PatternResult] | None = None,
    markers: list[PatternMarker] | None = None,
    retracement: RetracementResult | None = None,
    levels: list[PriceLevel] | None = None,
    mtf_: MultiTimeframeResult | None = None,
    volumes: list[int] | None = None,
    start: datetime | None = None,
) -> StrategyContext:
    candles = candles_from_ohlc(
        rows, volumes=volumes, start=start or NOW - timedelta(minutes=5 * len(rows))
    )
    pa = PriceActionAnalysis(
        symbol="TEST",
        timeframe=Timeframe.M5,
        candles_analyzed=len(candles),
        price=candles[-1].close,
        data_as_of=candles[-1].timestamp,
        patterns=patterns or [],
        recent_patterns=markers or [],
        retracement=retracement,
        support_resistance=SupportResistanceResult.model_construct(levels=levels or []),
        multi_timeframe=mtf_,
        timestamp=NOW,
    )
    return StrategyContext(
        symbol="TEST",
        timeframe=Timeframe.M5,
        candles=candles,
        analysis=analysis_,
        regime=regime_,
        price_action=pa,
        atr=1.0,
        now=NOW,
    )


def quiet_rows(n: int, level_: float = 100.0, half_range: float = 0.5) -> list[Row]:
    rows: list[Row] = []
    for i in range(n):
        if i % 2 == 0:
            rows.append((level_, level_ + half_range, level_ - half_range, level_ + 0.2))
        else:
            rows.append((level_ + 0.2, level_ + half_range, level_ - half_range, level_))
    return rows


# --- trend continuation -----------------------------------------------------------------


def trend_long(**overrides: Any) -> StrategyContext:
    """Uptrend: impulse 90 -> 110, pulled back 50% to 100 (held at EMA 20,
    100.3), now 40% (102), a bottoming tail on the latest candle."""

    o: dict[str, Any] = {
        "regime": MarketRegime.STRONG_BULLISH,
        "retracement": RetracementResult(
            detected=True,
            direction=Trend.UP,
            impulse_start=90.0,
            impulse_end=110.0,
            impulse_size=20.0,
            impulse_size_atr=20.0,
            retracement_percentage=40.0,
            max_retracement_percentage=50.0,
            zone=RetracementZone.HEALTHY_PULLBACK,
            candles_since_extreme=8,
        ),
        "patterns": [pattern(PatternType.BT, PatternDirection.BULLISH)],
        "levels": None,
        "mtf": mtf(),
        "latest": (101.5, 102.4, 100.9, 102.2),
        "analysis": {},
    }
    o.update(overrides)
    latest = o["latest"]
    close = latest[3]
    levels = o["levels"]
    if levels is None:
        levels = [
            level("Swing high", LevelType.SWING_HIGH, 110.0, close),
            level("EMA 20", LevelType.EMA, 100.3, close),
            level("Swing low", LevelType.SWING_LOW, 100.0, close),  # made by the pullback itself
            level("Retracement 50%", LevelType.RETRACEMENT, 100.0, close),
        ]
    return context(
        [*quiet_rows(30, 104.0), latest],
        analysis_=analysis(price=close, **o["analysis"]),
        regime_=regime(o["regime"]),
        patterns=o["patterns"],
        retracement=o["retracement"],
        levels=levels,
        mtf_=o["mtf"],
    )


def trend_short(**overrides: Any) -> StrategyContext:
    """Mirror of trend_long: impulse 110 -> 90, pulled back to 100, now 98."""

    defaults: dict[str, Any] = {
        "regime": MarketRegime.STRONG_BEARISH,
        "retracement": RetracementResult(
            detected=True,
            direction=Trend.DOWN,
            impulse_start=110.0,
            impulse_end=90.0,
            impulse_size=20.0,
            impulse_size_atr=20.0,
            retracement_percentage=40.0,
            max_retracement_percentage=50.0,
            zone=RetracementZone.HEALTHY_PULLBACK,
            candles_since_extreme=8,
        ),
        "patterns": [pattern(PatternType.TT, PatternDirection.BEARISH)],
        "mtf": mtf(context=Trend.DOWN, context_bias=-60.0),
        "latest": (98.5, 99.1, 97.6, 97.8),
        "levels": [
            level("Swing low", LevelType.SWING_LOW, 90.0, 97.8),
            level("EMA 20", LevelType.EMA, 99.7, 97.8),
        ],
        "analysis": {
            "price_vs_vwap": Relationship.BELOW,
            "vwap": 101.0,
            "structure": Trend.DOWN,
            "trend_direction": Trend.DOWN,
            "momentum": 35.0,
            "relative_strength": 30.0,
        },
    }
    defaults.update(overrides)
    return trend_long(**defaults)


# --- breakout momentum ---------------------------------------------------------------------


def breakout_long(**overrides: Any) -> StrategyContext:
    """20 candles ranging 99-101 (2 ATR), then a candle closing at 101.4
    on 3x volume. NR markers inside the consolidation."""

    o: dict[str, Any] = {
        "breakout": (100.8, 101.5, 100.9, 101.4),
        "volume": 3000,
        "rows": None,
        "analysis": {"momentum": 66.0},
        "mtf": mtf(),
        "regime": MarketRegime.RANGE,
        "markers": None,
        "start": None,
    }
    o.update(overrides)
    rows = o["rows"] or quiet_rows(40, 100.0, half_range=1.0)
    rows = [*rows, o["breakout"]]
    volumes = [1000] * (len(rows) - 1) + [o["volume"]]
    start = o["start"] or NOW - timedelta(minutes=5 * len(rows))
    markers = o["markers"]
    if markers is None:
        markers = [
            PatternMarker(
                pattern=PatternType.NR,
                direction=PatternDirection.NEUTRAL,
                confidence=60,
                strength=50,
                timestamp=start + timedelta(minutes=5 * (len(rows) - 4)),
                price=101.0,
            )
        ]
    close = o["breakout"][3]
    return context(
        rows,
        analysis_=analysis(price=close, **o["analysis"]),
        regime_=regime(o["regime"]),
        markers=markers,
        mtf_=o["mtf"],
        volumes=volumes,
        start=start,
    )


# --- mean reversion -------------------------------------------------------------------------


def mean_reversion_short(**overrides: Any) -> StrategyContext:
    """Range 98-102 around a 99.5 VWAP; the latest candle tagged the range
    high (102.1) and closed weak with a topping tail."""

    o: dict[str, Any] = {
        "latest": (101.8, 102.1, 101.0, 101.1),
        "regime": MarketRegime.RANGE,
        "patterns": [pattern(PatternType.TT, PatternDirection.BEARISH)],
        "levels": None,
        "mtf": mtf(context=Trend.SIDEWAYS, context_bias=10.0, alignment=20.0),
        "analysis": {},
    }
    o.update(overrides)
    latest = o["latest"]
    close = latest[3]
    base: dict[str, Any] = {
        "price_vs_vwap": Relationship.ABOVE,
        "vwap": 99.5,
        "structure": Trend.SIDEWAYS,
        "trend_quality": 30.0,
        "trend_direction": Trend.SIDEWAYS,
        "momentum": 55.0,
        "rocs": {3: -0.2, 10: 0.4, 20: 0.5},
        "ema_fast": 100.2,
        "efficiency": 0.1,
        "relative_volume": 1.2,
    }
    base.update(o["analysis"])
    levels = o["levels"]
    if levels is None:
        levels = [
            level("Range high", LevelType.RANGE_HIGH, 102.0, close),
            level("Range low", LevelType.RANGE_LOW, 98.0, close),
        ]
    return context(
        [*quiet_rows(30, 100.0, half_range=1.5), latest],
        analysis_=analysis(price=close, **base),
        regime_=regime(o["regime"], 70.0),
        patterns=o["patterns"],
        levels=levels,
        mtf_=o["mtf"],
    )


def mean_reversion_long(**overrides: Any) -> StrategyContext:
    defaults: dict[str, Any] = {
        "latest": (98.2, 99.0, 97.9, 98.9),
        "patterns": [pattern(PatternType.BT, PatternDirection.BULLISH)],
        "analysis": {
            "price_vs_vwap": Relationship.BELOW,
            "vwap": 100.5,
            "rocs": {3: 0.2, 10: -0.4, 20: -0.5},
            "ema_fast": 99.8,
        },
    }
    defaults.update(overrides)
    return mean_reversion_short(**defaults)
