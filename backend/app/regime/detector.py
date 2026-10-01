"""Market regime detector.

How it works:

1. `analyze_candles` runs every market intelligence engine.
2. Each candidate regime is scored 0-100 from a list of weighted,
   human-readable conditions (see `RegimeConfig` for weights and
   thresholds). STRONG_BEARISH mirrors STRONG_BULLISH.
3. HIGH_VOLATILITY wins outright above its override score (the
   conservative choice); otherwise the highest score wins.
4. If nothing reaches `min_regime_score`, or the data is insufficient,
   the regime is UNKNOWN with confidence 0 — never a guess.
5. Confidence is the winning score, reduced when the runner-up is
   close behind (an ambiguous picture deserves lower confidence).

The result carries the reasons (conditions met), counter-reasons
(conditions not met), every regime's score and the features used, so
any classification can be traced back to the numbers behind it.
Regimes describe the recent past; they are not predictions.
"""

from __future__ import annotations

from datetime import datetime

from app.market_data.base import utcnow
from app.market_data.schemas import Candle, Timeframe
from app.market_intelligence.analyzer import AnalysisConfig, SymbolAnalysis, analyze_candles
from app.market_intelligence.moving_average import MAAlignment
from app.market_intelligence.schemas import Relationship, Trend, VolatilityLevel
from app.regime.config import RegimeConfig
from app.regime.schemas import (
    FeatureValue,
    MarketRegime,
    RegimeCondition,
    RegimeResult,
    RegimeScore,
)

_REQUIRED_ENGINES = ("vwap", "moving_averages", "volatility", "momentum", "volume", "structure", "range")

#: Tie-break order: conservative regimes first.
_PRIORITY = (
    MarketRegime.HIGH_VOLATILITY,
    MarketRegime.BREAKOUT,
    MarketRegime.STRONG_BULLISH,
    MarketRegime.STRONG_BEARISH,
    MarketRegime.RANGE,
)


def _cond(weights: dict[str, float], key: str, met: bool, met_text: str, unmet_text: str) -> RegimeCondition:
    return RegimeCondition(
        key=key, weight=weights.get(key, 0.0), met=met, description=met_text if met else unmet_text
    )


def _score(regime: MarketRegime, conditions: list[RegimeCondition]) -> RegimeScore:
    total = sum(c.weight for c in conditions)
    met = sum(c.weight for c in conditions if c.met)
    return RegimeScore(
        regime=regime, score=round(met / total * 100.0, 2) if total else 0.0, conditions=conditions
    )


def _score_trend(a: SymbolAnalysis, cfg: RegimeConfig, sign: int) -> RegimeScore:
    assert a.vwap and a.moving_averages and a.momentum and a.structure and a.range and a.volume
    w = cfg.bullish_weights
    up = sign > 0
    word = "bullish" if up else "bearish"
    side = Relationship.ABOVE if up else Relationship.BELOW
    trend = Trend.UP if up else Trend.DOWN
    vwap, ma, mom, st, rg, vol = a.vwap, a.moving_averages, a.momentum, a.structure, a.range, a.volume
    vwap_slope = (vwap.vwap_slope_percent or 0.0) * sign
    ema_slope = (ma.ema_fast_slope_percent or 0.0) * sign
    direction_name = "above" if up else "below"

    conditions = [
        _cond(
            w, "price_vs_vwap", vwap.price_vs_vwap == side,
            f"Price {direction_name} VWAP ({vwap.distance_from_vwap_percent:+.2f}%)",
            f"Price not {direction_name} VWAP",
        ),
        _cond(
            w, "vwap_slope", vwap_slope > 0,
            f"{'Positive' if up else 'Negative'} VWAP slope",
            f"VWAP not sloping {'up' if up else 'down'}",
        ),
        _cond(
            w, "ema_slope", ema_slope > cfg.min_ema_slope_percent,
            f"{'Positive' if up else 'Negative'} EMA slope ({ma.ema_fast_slope_percent:+.3f}%/candle)",
            f"Fast EMA not sloping {'up' if up else 'down'}",
        ),
        _cond(
            w, "ema_alignment", ma.alignment == (MAAlignment.BULLISH if up else MAAlignment.BEARISH),
            f"{word.capitalize()} EMA alignment",
            f"EMAs not in {word} alignment",
        ),
        _cond(
            w, "structure", st.trend == trend,
            "Higher-high, higher-low structure" if up else "Lower-high, lower-low structure",
            f"Swing structure not {word}",
        ),
        _cond(
            w, "momentum", mom.direction == trend,
            f"{'Positive' if up else 'Negative'} momentum (score {mom.momentum_score:.0f})",
            f"Momentum not {word} (score {mom.momentum_score:.0f})",
        ),
        _cond(
            w, "directional_efficiency",
            rg.efficiency_ratio >= cfg.min_efficiency_for_trend and rg.net_move_atr * sign > 0,
            f"Directional price movement (efficiency {rg.efficiency_ratio:.2f})",
            f"Movement not directional enough (efficiency {rg.efficiency_ratio:.2f})",
        ),
        _cond(
            w, "volume", vol.relative_volume >= cfg.min_relative_volume_for_trend,
            f"Supportive volume ({vol.relative_volume:.1f}x average)",
            f"Below-average volume ({vol.relative_volume:.1f}x)",
        ),
    ]
    return _score(MarketRegime.STRONG_BULLISH if up else MarketRegime.STRONG_BEARISH, conditions)


def _score_range(a: SymbolAnalysis, cfg: RegimeConfig) -> RegimeScore:
    assert a.vwap and a.moving_averages and a.momentum and a.structure and a.range
    w = cfg.range_weights
    conditions = [
        _cond(w, "no_ma_trend", a.moving_averages.trend == Trend.SIDEWAYS,
              "No moving-average trend", f"Moving averages trending {a.moving_averages.trend.value.lower()}"),
        _cond(w, "vwap_crossings", a.vwap.recent_crossings >= cfg.min_vwap_crossings_for_range,
              f"Repeated VWAP crossings ({a.vwap.recent_crossings} recently)",
              f"Few VWAP crossings ({a.vwap.recent_crossings})"),
        _cond(w, "low_efficiency", a.range.efficiency_ratio <= cfg.max_efficiency_for_range,
              f"Limited directional movement (efficiency {a.range.efficiency_ratio:.2f})",
              f"Price moving directionally (efficiency {a.range.efficiency_ratio:.2f})"),
        _cond(w, "flat_momentum", a.momentum.direction == Trend.SIDEWAYS,
              "Flat momentum", f"Momentum {a.momentum.direction.value.lower()}"),
        _cond(w, "no_structure_trend", a.structure.trend == Trend.SIDEWAYS,
              "Mixed swing structure", f"Swing structure trending {a.structure.trend.value.lower()}"),
        _cond(w, "inside_range", a.range.breakout_direction is None,
              f"Price inside its {a.range.window}-candle range",
              "Price has left its recent range"),
    ]
    return _score(MarketRegime.RANGE, conditions)


def _score_breakout(a: SymbolAnalysis, cfg: RegimeConfig) -> RegimeScore:
    assert a.range and a.volume and a.volatility and a.momentum
    w = cfg.breakout_weights
    rg = a.range
    direction = rg.breakout_direction
    where = "above" if direction == Trend.UP else "below"
    conditions = [
        _cond(w, "prior_compression", rg.is_compressed,
              f"Compression before the move (ranges {rg.compression_ratio:.2f}x baseline)",
              f"No prior compression (ranges {rg.compression_ratio:.2f}x baseline)"),
        _cond(w, "range_break", direction is not None,
              f"Close broke {where} the {rg.window}-candle range ({rg.breakout_distance_atr:.2f} ATR)",
              f"No break of the {rg.window}-candle range"),
        _cond(w, "volume_expansion", a.volume.relative_volume >= cfg.min_relative_volume_for_breakout,
              f"Volume expansion ({a.volume.relative_volume:.1f}x average)",
              f"No volume expansion ({a.volume.relative_volume:.1f}x)"),
        _cond(w, "range_expansion", a.volatility.latest_range_atr >= cfg.min_range_expansion_atr,
              f"Wide range candle ({a.volatility.latest_range_atr:.1f}x ATR)",
              f"Normal candle size ({a.volatility.latest_range_atr:.1f}x ATR)"),
        _cond(w, "momentum_agrees", direction is not None and a.momentum.direction == direction,
              "Momentum in the breakout direction", "Momentum not confirming a breakout"),
    ]
    return _score(MarketRegime.BREAKOUT, conditions)


def _score_high_volatility(a: SymbolAnalysis, cfg: RegimeConfig) -> RegimeScore:
    assert a.volatility
    w = cfg.high_volatility_weights
    v = a.volatility
    flips_needed = cfg.min_reversal_fraction_for_volatility * max(v.character_window - 1, 1)
    conditions = [
        _cond(w, "atr_elevated", v.volatility_level in (VolatilityLevel.HIGH, VolatilityLevel.EXTREME),
              f"ATR elevated ({v.atr_ratio:.2f}x baseline)", f"ATR near baseline ({v.atr_ratio:.2f}x)"),
        _cond(w, "atr_expanding", v.is_expanding, "ATR expanding", "ATR not expanding"),
        _cond(w, "large_wicks", v.average_wick_ratio >= cfg.min_wick_ratio_for_volatility,
              f"Large wicks ({v.average_wick_ratio:.0%} of range)",
              f"Normal wicks ({v.average_wick_ratio:.0%} of range)"),
        _cond(w, "reversals", v.direction_changes >= flips_needed,
              f"Rapid reversals ({v.direction_changes} direction changes in {v.character_window} candles)",
              f"Few reversals ({v.direction_changes} in {v.character_window} candles)"),
        _cond(w, "large_candles", v.latest_range_atr >= cfg.min_large_candle_atr,
              f"Large candle ({v.latest_range_atr:.1f}x ATR)",
              f"No unusually large candle ({v.latest_range_atr:.1f}x ATR)"),
    ]
    return _score(MarketRegime.HIGH_VOLATILITY, conditions)


def extract_features(a: SymbolAnalysis) -> dict[str, FeatureValue]:
    """Flat, JSON-friendly view of the numbers the detector used."""

    f: dict[str, FeatureValue] = {
        "price": a.price,
        "candles_analyzed": a.candles_analyzed,
    }
    if a.vwap:
        f |= {
            "vwap": a.vwap.vwap,
            "price_vs_vwap": a.vwap.price_vs_vwap.value,
            "distance_from_vwap_percent": a.vwap.distance_from_vwap_percent,
            "vwap_slope_percent": a.vwap.vwap_slope_percent,
            "vwap_recent_crossings": a.vwap.recent_crossings,
        }
    if a.moving_averages:
        ma = a.moving_averages
        f |= {
            "ema_fast": ma.ema_fast,
            "ema_slow": ma.ema_slow,
            "ma_long": ma.ma,
            "ema_fast_slope_percent": ma.ema_fast_slope_percent,
            "ema_separation_percent": ma.separation_percent,
            "ma_alignment": ma.alignment.value,
            "ma_trend": ma.trend.value,
        }
    if a.trend_quality:
        f["trend_quality_score"] = a.trend_quality.score
    if a.structure:
        f |= {
            "structure_trend": a.structure.trend.value,
            "structure_state": a.structure.structure_state.value,
            "structure_event": a.structure.last_event.value,
        }
    if a.momentum:
        f |= {"momentum_score": a.momentum.momentum_score, "momentum_direction": a.momentum.direction.value}
    if a.volume:
        f |= {"relative_volume": a.volume.relative_volume, "volume_trend": a.volume.volume_trend.value}
    if a.volatility:
        v = a.volatility
        f |= {
            "atr_percent": v.atr_percent,
            "atr_ratio": v.atr_ratio,
            "volatility_level": v.volatility_level.value,
            "average_wick_ratio": v.average_wick_ratio,
            "direction_changes": v.direction_changes,
            "latest_range_atr": v.latest_range_atr,
        }
    if a.range:
        f |= {
            "efficiency_ratio": a.range.efficiency_ratio,
            "compression_ratio": a.range.compression_ratio,
            "breakout_direction": a.range.breakout_direction.value if a.range.breakout_direction else None,
        }
    if a.relative_strength:
        f["relative_strength_score"] = a.relative_strength.relative_strength_score
    return f


def classify_regime(
    analysis: SymbolAnalysis,
    config: RegimeConfig | None = None,
    *,
    now: datetime | None = None,
) -> RegimeResult:
    cfg = config or RegimeConfig()
    base = {
        "symbol": analysis.symbol,
        "timeframe": analysis.timeframe,
        "candles_analyzed": analysis.candles_analyzed,
        "data_as_of": analysis.data_as_of,
        "timestamp": now or utcnow(),
        "features": extract_features(analysis),
    }

    if analysis.candles_analyzed < cfg.min_candles:
        return RegimeResult(
            **base,
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            reasons=[f"Not enough data: {analysis.candles_analyzed} candles (need {cfg.min_candles})"],
        )
    missing = [name for name in _REQUIRED_ENGINES if getattr(analysis, name) is None]
    if missing:
        return RegimeResult(
            **base,
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            reasons=[f"Analysis unavailable: {', '.join(missing)}"],
        )

    regime_scores = [
        _score_high_volatility(analysis, cfg),
        _score_breakout(analysis, cfg),
        _score_trend(analysis, cfg, 1),
        _score_trend(analysis, cfg, -1),
        _score_range(analysis, cfg),
    ]
    by_regime = {s.regime: s for s in regime_scores}
    scores = {s.regime: s.score for s in regime_scores}
    ranked = sorted(regime_scores, key=lambda s: (-s.score, _PRIORITY.index(s.regime)))

    high_vol = by_regime[MarketRegime.HIGH_VOLATILITY]
    winner = high_vol if high_vol.score >= cfg.high_volatility_override_score else ranked[0]
    runner_up = max((s for s in regime_scores if s is not winner), key=lambda s: s.score)

    if winner.score < cfg.min_regime_score:
        return RegimeResult(
            **base,
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            scores=scores,
            reasons=[
                f"No regime reached the minimum score of {cfg.min_regime_score:.0f}",
                f"Closest: {ranked[0].regime.value} ({ranked[0].score:.0f})",
            ],
        )

    penalty = 0.5 * max(0.0, runner_up.score - (winner.score - cfg.ambiguity_margin))
    confidence = max(0.0, min(100.0, winner.score - penalty))

    met = sorted((c for c in winner.conditions if c.met), key=lambda c: -c.weight)
    unmet = sorted((c for c in winner.conditions if not c.met), key=lambda c: -c.weight)
    counter = [c.description for c in unmet]
    if penalty > 0:
        counter.append(f"Also resembles {runner_up.regime.value} (score {runner_up.score:.0f})")
    reasons = [c.description for c in met]
    if winner is high_vol and ranked[0] is not high_vol:
        reasons.append("High volatility takes precedence over other regimes")

    return RegimeResult(
        **base,
        regime=winner.regime,
        confidence=round(confidence, 1),
        scores=scores,
        reasons=reasons,
        counter_reasons=counter,
    )


def detect_regime(
    symbol: str,
    timeframe: Timeframe,
    candles: list[Candle],
    *,
    benchmark_candles: list[Candle] | None = None,
    analysis_config: AnalysisConfig | None = None,
    regime_config: RegimeConfig | None = None,
) -> tuple[RegimeResult, SymbolAnalysis]:
    analysis = analyze_candles(
        symbol, timeframe, candles, benchmark_candles=benchmark_candles, config=analysis_config
    )
    return classify_regime(analysis, regime_config), analysis
