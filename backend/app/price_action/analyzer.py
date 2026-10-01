"""Runs the whole price action engine for one symbol.

Inputs are closed candles per timeframe. The entry timeframe (5m by
default) gets the full treatment: every pattern detector on the latest
candle (with support/resistance as context), recent pattern markers,
significant swings, retracement and support/resistance. The
multi-timeframe engine then adds the context and setup timeframes.

Each part is independent: if one lacks data it is reported under
`unavailable` and the others still run. Nothing here creates a trade.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.market_data.base import utcnow
from app.market_data.schemas import Candle, Timeframe
from app.market_intelligence.analyzer import AnalysisConfig
from app.market_intelligence.schemas import InsufficientDataError
from app.price_action.anatomy import PatternInputs
from app.price_action.candle_patterns import detect_patterns, scan_patterns
from app.price_action.config import PriceActionConfig
from app.price_action.multi_timeframe import MultiTimeframeResult, analyze_multi_timeframe
from app.price_action.retracement import RetracementResult, analyze_retracement
from app.price_action.schemas import PATTERN_DISCLAIMER, PatternMarker, PatternResult
from app.price_action.support_resistance import (
    SupportResistanceResult,
    analyze_support_resistance,
    reference_levels,
)
from app.price_action.swing_structure import SwingStructureResult, analyze_swing_structure
from app.regime.config import RegimeConfig


class PriceActionAnalysis(BaseModel):
    symbol: str
    timeframe: Timeframe
    candles_analyzed: int
    price: float | None
    data_as_of: datetime | None
    patterns: list[PatternResult] = Field(default_factory=list)  # every detector, latest candle
    recent_patterns: list[PatternMarker] = Field(default_factory=list)
    swing_structure: SwingStructureResult | None = None
    retracement: RetracementResult | None = None
    support_resistance: SupportResistanceResult | None = None
    multi_timeframe: MultiTimeframeResult | None = None
    unavailable: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime
    note: str = PATTERN_DISCLAIMER


def analyze_price_action(
    symbol: str,
    candles_by_timeframe: dict[Timeframe, list[Candle]],
    *,
    day_candles: list[Candle] | None = None,
    config: PriceActionConfig | None = None,
    analysis_config: AnalysisConfig | None = None,
    regime_config: RegimeConfig | None = None,
    now: datetime | None = None,
) -> PriceActionAnalysis:
    cfg = config or PriceActionConfig()
    entry_tf = cfg.multi_timeframe.entry_timeframe
    candles = candles_by_timeframe.get(entry_tf, [])
    stamp = now or utcnow()
    result = PriceActionAnalysis(
        symbol=symbol,
        timeframe=entry_tf,
        candles_analyzed=len(candles),
        price=candles[-1].close if candles else None,
        data_as_of=candles[-1].timestamp if candles else None,
        timestamp=stamp,
    )
    if not candles:
        result.unavailable["all"] = f"No closed {entry_tf.value} candles yet."
        return result

    try:
        result.swing_structure = analyze_swing_structure(candles, cfg.swings)
    except InsufficientDataError as exc:
        result.unavailable["swing_structure"] = str(exc)

    result.retracement = analyze_retracement(candles, cfg.retracement, cfg.swings)

    days = day_candles or candles_by_timeframe.get(cfg.multi_timeframe.setup_timeframe) or candles
    try:
        result.support_resistance = analyze_support_resistance(
            candles,
            day_candles=days,
            retracement=result.retracement,
            config=cfg.levels,
            swing_config=cfg.swings,
        )
    except InsufficientDataError as exc:
        result.unavailable["support_resistance"] = str(exc)

    levels = (
        reference_levels(result.support_resistance)
        if result.support_resistance is not None
        else None
    )
    result.patterns = detect_patterns(candles, inputs=PatternInputs(candles, cfg.patterns, levels))
    result.recent_patterns = scan_patterns(candles, config=cfg.patterns)

    result.multi_timeframe = analyze_multi_timeframe(
        symbol,
        candles_by_timeframe,
        config=cfg,
        analysis_config=analysis_config,
        regime_config=regime_config,
        now=stamp,
        entry_patterns=result.patterns,
    )
    return result
