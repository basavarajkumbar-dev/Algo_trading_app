"""Wide Range Body (WRB).

A candle whose real body is much larger than recent bodies and makes
up most of its own range: one side was clearly in control for the
whole candle. Direction follows the candle's colour.

Strength scales with how outsized the body is; confidence rises when
the candle closes near its extreme, breaks the recent high/low and is
backed by volume. A WRB into a nearby level is marked down, since it
may simply be running into supply or demand.
"""

from __future__ import annotations

from app.market_intelligence.utils import clamp
from app.price_action.anatomy import PatternInputs, apply_context, insufficient, make_result
from app.price_action.schemas import CandleColor, PatternDirection, PatternResult, PatternType


def detect_wide_range_body(inputs: PatternInputs, index: int) -> PatternResult:
    cfg = inputs.config.wide_range
    needed = max(inputs.config.context.average_window, cfg.breakout_lookback)
    if not inputs.has_history(index, needed):
        return insufficient(PatternType.WRB, inputs, index, needed)

    candle = inputs.anatomy[index]
    average_body = inputs.average_body(index) or 0.0
    multiple = candle.body / average_body if average_body else 0.0
    if candle.color == CandleColor.BULLISH:
        direction = PatternDirection.BULLISH
    elif candle.color == CandleColor.BEARISH:
        direction = PatternDirection.BEARISH
    else:
        direction = PatternDirection.NEUTRAL
    context = inputs.context(index, direction)
    metrics = {"body_vs_average": multiple, "body_ratio": candle.body_ratio}

    failures = []
    if direction == PatternDirection.NEUTRAL:
        failures.append("Doji or zero-range candle has no body to speak of")
    if multiple < cfg.min_body_vs_average:
        failures.append(
            f"Body is {multiple:.2f}x the average (needs >= {cfg.min_body_vs_average:.1f}x)"
        )
    if candle.body_ratio < cfg.min_body_ratio:
        failures.append(
            f"Body is {candle.body_ratio:.0%} of the range (needs >= {cfg.min_body_ratio:.0%})"
        )
    if failures:
        return make_result(
            PatternType.WRB,
            inputs,
            index,
            detected=False,
            direction=direction,
            context=context,
            reasons=failures,
            metrics=metrics,
        )

    bullish = direction == PatternDirection.BULLISH
    side = "buyers" if bullish else "sellers"
    reasons = [
        f"Body {multiple:.1f}x the average, {candle.body_ratio:.0%} of its range: {side} in control"
    ]
    confidence = 55.0

    close_strength = candle.close_position if bullish else 1.0 - candle.close_position
    metrics["close_strength"] = close_strength
    if close_strength >= cfg.strong_close_position:
        confidence += 15
        reasons.append("Closed near its extreme")

    prior = inputs.candles[index - cfg.breakout_lookback : index]
    if bullish and candle.close > max(c.high for c in prior):
        confidence += 10
        reasons.append(f"Closed above the high of the last {cfg.breakout_lookback} candles")
    elif not bullish and candle.close < min(c.low for c in prior):
        confidence += 10
        reasons.append(f"Closed below the low of the last {cfg.breakout_lookback} candles")

    confidence = apply_context(confidence, direction, context, inputs.config, reasons)
    strength = (multiple - 1.0) / (cfg.full_strength_multiple - 1.0) * 100.0
    return make_result(
        PatternType.WRB,
        inputs,
        index,
        detected=True,
        direction=direction,
        context=context,
        reasons=reasons,
        confidence=confidence,
        strength=clamp(strength),
        metrics=metrics,
    )
