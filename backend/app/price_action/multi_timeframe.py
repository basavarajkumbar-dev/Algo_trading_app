"""Multi-timeframe analysis.

Three configurable roles (defaults from the project brief):

- context (60m): major trend, structure, directional bias, key levels
- setup   (15m): structure, pullback (retracement), nearest support /
                 resistance, momentum
- entry   (5m):  candle confirmation (price action patterns), momentum
                 returning in the bias direction, volume confirmation

Each timeframe gets a directional bias from -100 (bearish) to +100
(bullish), blended from its moving averages, swing structure, momentum
and VWAP position (Phase 4 engines). The overall bias is the weighted
blend across timeframes; the alignment score (0-100) measures how far
the timeframes agree with it.

All of this is descriptive. "Entry" names the timeframe's role from the
brief; nothing here produces an order, an entry price or a trade.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.market_data.base import utcnow
from app.market_data.schemas import Candle, Timeframe
from app.market_intelligence.analyzer import AnalysisConfig, SymbolAnalysis, analyze_candles
from app.market_intelligence.schemas import InsufficientDataError, Relationship, Trend
from app.market_intelligence.utils import clamp
from app.price_action.candle_patterns import detect_patterns
from app.price_action.config import PriceActionConfig
from app.price_action.retracement import RetracementResult, analyze_retracement
from app.price_action.schemas import PatternDirection, PatternResult, RetracementZone
from app.price_action.support_resistance import (
    PriceLevel,
    SupportResistanceResult,
    analyze_support_resistance,
    reference_levels,
)
from app.regime.config import RegimeConfig
from app.regime.detector import classify_regime
from app.regime.schemas import MarketRegime

MTF_NOTE = (
    "Multi-timeframe analysis describes how timeframes currently agree. "
    "It is not a trade signal and does not predict future movement."
)


class TimeframeRole(str, Enum):
    CONTEXT = "CONTEXT"
    SETUP = "SETUP"
    ENTRY = "ENTRY"


class SetupState(str, Enum):
    WITH_TREND = "WITH_TREND"
    PULLBACK_IN_TREND = "PULLBACK_IN_TREND"
    WEAKENING_PULLBACK = "WEAKENING_PULLBACK"
    IMPULSE_INVALIDATED = "IMPULSE_INVALIDATED"
    COUNTER_TREND = "COUNTER_TREND"
    NO_DIRECTIONAL_CONTEXT = "NO_DIRECTIONAL_CONTEXT"
    UNCLEAR = "UNCLEAR"


class EntryConfirmation(BaseModel):
    """Whether the entry timeframe currently agrees with the
    higher-timeframe bias. Descriptive only: not an order trigger."""

    reference_bias: PatternDirection
    aligned_patterns: list[str] = Field(default_factory=list)
    opposing_patterns: list[str] = Field(default_factory=list)
    candle_confirmation: bool = False
    momentum_returning: bool = False
    volume_confirmed: bool = False
    confirmations: int = 0  # of the three above


class TimeframeAnalysis(BaseModel):
    role: TimeframeRole
    timeframe: Timeframe
    available: bool
    unavailable_reason: str | None = None
    candles_analyzed: int = 0
    data_as_of: datetime | None = None
    price: float | None = None
    bias: float = 0.0  # -100 bearish .. +100 bullish
    direction: Trend = Trend.SIDEWAYS
    bias_components: dict[str, float] = Field(default_factory=dict)
    regime: MarketRegime | None = None
    regime_confidence: float | None = None
    trend_quality: float | None = None
    momentum_score: float | None = None
    structure_trend: Trend | None = None
    structure_state: str | None = None
    last_structure_event: str | None = None
    price_vs_vwap: Relationship | None = None
    ma_alignment: str | None = None
    # role-specific
    key_levels: list[PriceLevel] = Field(default_factory=list)  # context
    retracement: RetracementResult | None = None  # setup
    setup_state: SetupState | None = None  # setup
    nearest_support: PriceLevel | None = None  # setup
    nearest_resistance: PriceLevel | None = None  # setup
    patterns: list[PatternResult] = Field(default_factory=list)  # entry: detected only
    confirmation: EntryConfirmation | None = None  # entry
    relative_volume: float | None = None
    reasons: list[str] = Field(default_factory=list)


class MultiTimeframeResult(BaseModel):
    symbol: str
    overall_bias: PatternDirection
    bias_score: float  # weighted -100..+100
    alignment_score: float  # 0-100
    context_analysis: TimeframeAnalysis
    setup_analysis: TimeframeAnalysis
    entry_analysis: TimeframeAnalysis
    conflicts: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    timestamp: datetime
    note: str = MTF_NOTE


# --- bias -----------------------------------------------------------------------

_BIAS_WEIGHTS = {"moving_averages": 0.3, "structure": 0.3, "momentum": 0.25, "vwap": 0.15}


def _sign(trend: Trend | None) -> float:
    return 1.0 if trend == Trend.UP else -1.0 if trend == Trend.DOWN else 0.0


def timeframe_bias(analysis: SymbolAnalysis) -> tuple[float, dict[str, float]]:
    """Directional bias (-100..+100) from the Phase 4 engine outputs,
    with each component's contribution (-1..+1)."""

    components: dict[str, float] = {}
    if analysis.moving_averages is not None:
        ma = analysis.moving_averages
        components["moving_averages"] = _sign(ma.trend) * (0.5 + 0.5 * ma.ma_trend_score / 100.0)
    if analysis.structure is not None:
        st = analysis.structure
        components["structure"] = _sign(st.trend) * st.confidence / 100.0
    if analysis.momentum is not None:
        components["momentum"] = (analysis.momentum.momentum_score - 50.0) / 50.0
    if analysis.vwap is not None:
        rel = analysis.vwap.price_vs_vwap
        components["vwap"] = (
            1.0 if rel == Relationship.ABOVE else -1.0 if rel == Relationship.BELOW else 0.0
        )
    if not components:
        return 0.0, {}
    total = sum(_BIAS_WEIGHTS[k] for k in components)
    bias = 100.0 * sum(_BIAS_WEIGHTS[k] * v for k, v in components.items()) / total
    return round(clamp(bias, -100.0, 100.0), 1), {k: round(v, 3) for k, v in components.items()}


def _direction(bias: float, threshold: float) -> Trend:
    if bias >= threshold:
        return Trend.UP
    if bias <= -threshold:
        return Trend.DOWN
    return Trend.SIDEWAYS


def _to_pattern_direction(trend: Trend) -> PatternDirection:
    return {
        Trend.UP: PatternDirection.BULLISH,
        Trend.DOWN: PatternDirection.BEARISH,
        Trend.SIDEWAYS: PatternDirection.NEUTRAL,
    }[trend]


# --- per timeframe -----------------------------------------------------------------


def _base_analysis(
    role: TimeframeRole,
    timeframe: Timeframe,
    candles: list[Candle],
    symbol: str,
    config: PriceActionConfig,
    analysis_config: AnalysisConfig,
    regime_config: RegimeConfig,
    now: datetime,
) -> tuple[TimeframeAnalysis, SymbolAnalysis | None]:
    minimum = config.multi_timeframe.min_candles
    if len(candles) < minimum:
        return (
            TimeframeAnalysis(
                role=role,
                timeframe=timeframe,
                available=False,
                unavailable_reason=f"Needs {minimum} closed {timeframe.value} candles; have {len(candles)}.",
                candles_analyzed=len(candles),
            ),
            None,
        )

    analysis = analyze_candles(symbol, timeframe, candles, config=analysis_config)
    bias, components = timeframe_bias(analysis)
    direction = _direction(bias, config.multi_timeframe.bias_threshold)
    regime = classify_regime(analysis, regime_config, now=now)
    result = TimeframeAnalysis(
        role=role,
        timeframe=timeframe,
        available=True,
        candles_analyzed=len(candles),
        data_as_of=candles[-1].timestamp,
        price=candles[-1].close,
        bias=bias,
        direction=direction,
        bias_components=components,
        regime=regime.regime,
        regime_confidence=regime.confidence,
        trend_quality=analysis.trend_quality.score if analysis.trend_quality else None,
        momentum_score=analysis.momentum.momentum_score if analysis.momentum else None,
        structure_trend=analysis.structure.trend if analysis.structure else None,
        structure_state=analysis.structure.structure_state.value if analysis.structure else None,
        last_structure_event=analysis.structure.last_event.value if analysis.structure else None,
        price_vs_vwap=analysis.vwap.price_vs_vwap if analysis.vwap else None,
        ma_alignment=analysis.moving_averages.alignment.value if analysis.moving_averages else None,
        relative_volume=analysis.volume.relative_volume if analysis.volume else None,
    )
    word = {Trend.UP: "bullish", Trend.DOWN: "bearish", Trend.SIDEWAYS: "no clear"}[direction]
    result.reasons.append(f"{timeframe.value}: {word} bias ({bias:+.0f})")
    if analysis.structure is not None:
        result.reasons.extend(analysis.structure.reasons[:1])
    return result, analysis


def _levels(
    candles: list[Candle], config: PriceActionConfig, retracement: RetracementResult | None
) -> SupportResistanceResult | None:
    try:
        return analyze_support_resistance(
            candles,
            day_candles=candles,
            retracement=retracement,
            config=config.levels,
            swing_config=config.swings,
        )
    except InsufficientDataError:
        return None


def classify_setup_state(
    context: Trend, setup: Trend, retracement: RetracementResult | None
) -> SetupState:
    if context == Trend.SIDEWAYS:
        return SetupState.NO_DIRECTIONAL_CONTEXT
    if retracement is not None and retracement.detected and retracement.direction == context:
        zone = retracement.zone
        if zone == RetracementZone.SHALLOW:
            return SetupState.WITH_TREND
        if zone in (RetracementZone.HEALTHY_PULLBACK, RetracementZone.DEEP_PULLBACK):
            return SetupState.PULLBACK_IN_TREND
        if zone in (RetracementZone.WEAKENING, RetracementZone.MAJOR_WEAKNESS):
            return SetupState.WEAKENING_PULLBACK
        return SetupState.IMPULSE_INVALIDATED
    if setup == context:
        return SetupState.WITH_TREND
    if setup != Trend.SIDEWAYS:
        return SetupState.COUNTER_TREND
    return SetupState.UNCLEAR


_SETUP_TEXT = {
    SetupState.WITH_TREND: "moving with the higher-timeframe trend",
    SetupState.PULLBACK_IN_TREND: "pulling back within the higher-timeframe trend",
    SetupState.WEAKENING_PULLBACK: "deep pullback: the higher-timeframe move is weakening",
    SetupState.IMPULSE_INVALIDATED: "the impulse has been fully retraced",
    SetupState.COUNTER_TREND: "moving against the higher-timeframe trend",
    SetupState.NO_DIRECTIONAL_CONTEXT: "no directional context from the higher timeframe",
    SetupState.UNCLEAR: "no clear setup structure",
}


def _confirmation(
    reference: PatternDirection,
    patterns: list[PatternResult],
    analysis: SymbolAnalysis,
    config: PriceActionConfig,
) -> EntryConfirmation:
    cfg = config.multi_timeframe
    detected = [p for p in patterns if p.detected]
    aligned = [
        p.pattern.value
        for p in detected
        if p.direction == reference and reference != PatternDirection.NEUTRAL
    ]
    opposing = [
        p.pattern.value
        for p in detected
        if p.direction != PatternDirection.NEUTRAL
        and p.direction != reference
        and reference != PatternDirection.NEUTRAL
    ]
    momentum_returning = False
    if analysis.momentum is not None and reference != PatternDirection.NEUTRAL:
        score = analysis.momentum.momentum_score
        rocs = analysis.momentum.rate_of_change_percent
        short = rocs[min(rocs)] if rocs else 0.0
        if reference == PatternDirection.BULLISH:
            momentum_returning = score >= cfg.momentum_return_threshold and short > 0
        else:
            momentum_returning = score <= 100 - cfg.momentum_return_threshold and short < 0
    volume_confirmed = (
        analysis.volume is not None and analysis.volume.relative_volume >= cfg.volume_confirmation
    )
    confirmation = EntryConfirmation(
        reference_bias=reference,
        aligned_patterns=aligned,
        opposing_patterns=opposing,
        candle_confirmation=bool(aligned),
        momentum_returning=momentum_returning,
        volume_confirmed=volume_confirmed,
    )
    confirmation.confirmations = sum(
        [confirmation.candle_confirmation, momentum_returning, volume_confirmed]
    )
    return confirmation


def analyze_multi_timeframe(
    symbol: str,
    candles_by_timeframe: dict[Timeframe, list[Candle]],
    *,
    config: PriceActionConfig | None = None,
    analysis_config: AnalysisConfig | None = None,
    regime_config: RegimeConfig | None = None,
    now: datetime | None = None,
    entry_patterns: list[PatternResult] | None = None,
) -> MultiTimeframeResult:
    """`entry_patterns`: patterns already evaluated on the latest entry
    candle (with level context) by the caller, to avoid recomputing."""

    cfg = config or PriceActionConfig()
    mtf = cfg.multi_timeframe
    acfg = analysis_config or AnalysisConfig()
    rcfg = regime_config or RegimeConfig()
    stamp = now or utcnow()

    def run(
        role: TimeframeRole, timeframe: Timeframe
    ) -> tuple[TimeframeAnalysis, SymbolAnalysis | None, list[Candle]]:
        candles = candles_by_timeframe.get(timeframe, [])
        result, analysis = _base_analysis(role, timeframe, candles, symbol, cfg, acfg, rcfg, stamp)
        return result, analysis, candles

    context, _, context_candles = run(TimeframeRole.CONTEXT, mtf.context_timeframe)
    setup, _, setup_candles = run(TimeframeRole.SETUP, mtf.setup_timeframe)
    entry, entry_analysis, entry_candles = run(TimeframeRole.ENTRY, mtf.entry_timeframe)

    # Context: the strongest nearby levels on the context timeframe.
    if context.available:
        levels = _levels(context_candles, cfg, None)
        if levels is not None:
            context.key_levels = sorted(levels.levels, key=lambda lvl: lvl.strength, reverse=True)[
                :4
            ]
            if context.key_levels:
                top = context.key_levels[0]
                context.reasons.append(
                    f"Key level: {top.label} {top.price:g} (strength {top.strength:.0f})"
                )

    # Setup: pullback and nearest levels on the setup timeframe.
    if setup.available:
        setup.retracement = analyze_retracement(setup_candles, cfg.retracement, cfg.swings)
        levels = _levels(setup_candles, cfg, setup.retracement)
        if levels is not None:
            setup.nearest_support = levels.nearest_support
            setup.nearest_resistance = levels.nearest_resistance
        reference_trend = context.direction if context.available else Trend.SIDEWAYS
        setup.setup_state = classify_setup_state(
            reference_trend, setup.direction, setup.retracement
        )
        setup.reasons.append(f"Setup: {_SETUP_TEXT[setup.setup_state]}")
        if setup.retracement.detected and setup.retracement.retracement_percentage is not None:
            setup.reasons.append(
                f"Retraced {setup.retracement.retracement_percentage:.0f}% of the last "
                f"{setup.retracement.direction.value.lower() if setup.retracement.direction else ''} impulse"
            )

    # Higher-timeframe reference for the entry timeframe: context and setup only.
    htf_weights = [
        (mtf.context_weight, context),
        (mtf.setup_weight, setup),
    ]
    htf_total = sum(w for w, tf in htf_weights if tf.available)
    htf_bias = (
        sum(w * tf.bias for w, tf in htf_weights if tf.available) / htf_total if htf_total else 0.0
    )
    reference = _to_pattern_direction(_direction(htf_bias, mtf.bias_threshold))

    if entry.available and entry_analysis is not None:
        if entry_patterns is not None:
            patterns = entry_patterns
        else:
            levels = _levels(entry_candles, cfg, None)
            refs = reference_levels(levels) if levels is not None else None
            patterns = detect_patterns(entry_candles, levels=refs, config=cfg.patterns)
        entry.patterns = [p for p in patterns if p.detected]
        entry.confirmation = _confirmation(reference, patterns, entry_analysis, cfg)
        conf = entry.confirmation
        if reference == PatternDirection.NEUTRAL:
            entry.reasons.append("No higher-timeframe bias to confirm")
        else:
            entry.reasons.append(
                f"{conf.confirmations} of 3 confirmations for the {reference.value.lower()} "
                "higher-timeframe bias (descriptive only, not an order)"
            )
            if conf.aligned_patterns:
                entry.reasons.append(f"Aligned patterns: {', '.join(conf.aligned_patterns)}")
            if conf.opposing_patterns:
                entry.reasons.append(f"Opposing patterns: {', '.join(conf.opposing_patterns)}")

    # Overall bias and alignment.
    weighted = [
        (mtf.context_weight, context),
        (mtf.setup_weight, setup),
        (mtf.entry_weight, entry),
    ]
    available = [(w, tf) for w, tf in weighted if tf.available]
    total = sum(w for w, _ in available)
    bias_score = sum(w * tf.bias for w, tf in available) / total if total else 0.0
    overall_trend = _direction(bias_score, mtf.bias_threshold)
    overall = _to_pattern_direction(overall_trend)

    # Alignment: how strongly the timeframes agree on one direction.
    # Each timeframe scores its bias on the overall side, scaled so
    # `full_agreement_bias` counts fully (-1..+1, so an opposing
    # timeframe subtracts); weighted, floored at 0, then x100.
    side = 1.0 if bias_score > 0 else -1.0 if bias_score < 0 else 0.0

    def agreement(tf: TimeframeAnalysis) -> float:
        return clamp(side * tf.bias / mtf.full_agreement_bias, -1.0, 1.0)

    alignment = (
        100.0 * max(0.0, sum(w * agreement(tf) for w, tf in available) / total) if total else 0.0
    )

    conflicts: list[str] = []
    names = {
        TimeframeRole.CONTEXT: "context",
        TimeframeRole.SETUP: "setup",
        TimeframeRole.ENTRY: "entry",
    }
    # Conflicts are judged against the context timeframe when it has a
    # direction (the classic multi-timeframe question: does the lower
    # timeframe agree with the higher one?), otherwise against the
    # overall bias.
    if context.available and context.direction != Trend.SIDEWAYS:
        reference_trend, reference_name = (
            context.direction,
            f"{context.timeframe.value} context",
        )
        compared = [tf for _, tf in available if tf.role != TimeframeRole.CONTEXT]
    else:
        reference_trend, reference_name = overall_trend, "overall bias"
        compared = [tf for _, tf in available]
    if reference_trend != Trend.SIDEWAYS:
        for tf in compared:
            if tf.direction not in (reference_trend, Trend.SIDEWAYS):
                conflicts.append(
                    f"{tf.timeframe.value} {names[tf.role]} is {tf.direction.value.lower()} "
                    f"against the {reference_trend.value.lower()} {reference_name}"
                )
    reasons = [
        f"Overall bias {overall.value.lower()} ({bias_score:+.0f}), alignment {alignment:.0f}/100",
    ]
    if not context.available:
        reasons.append(f"Context timeframe unavailable: {context.unavailable_reason}")
    if overall_trend == Trend.SIDEWAYS:
        reasons.append("No overall direction: alignment is low by construction")
    for _, tf in available:
        reasons.append(
            f"{tf.timeframe.value} {names[tf.role]}: {tf.direction.value.lower()} ({tf.bias:+.0f})"
        )

    return MultiTimeframeResult(
        symbol=symbol,
        overall_bias=overall,
        bias_score=round(bias_score, 1),
        alignment_score=round(alignment, 1),
        context_analysis=context,
        setup_analysis=setup,
        entry_analysis=entry,
        conflicts=conflicts,
        reasons=reasons,
        timestamp=stamp,
    )
