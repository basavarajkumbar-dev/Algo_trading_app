"""Narrow Range (NR) and Narrow Range Body (NRB).

NR  — the candle's high-low range is the narrowest of the last N
      candles (NR7 by default; NR4 is also reported). A sign of
      contraction: volatility tends to come in cycles, so a narrow
      candle describes a pause, not a direction.
NRB — the candle's real body is small, both against its own range and
      against recent bodies: indecision between buyers and sellers.

Both are NEUTRAL: they carry no direction on their own. Context (prior
trend, nearby level) is reported so later layers can interpret them,
e.g. a narrow candle on support after a pullback.
"""

from __future__ import annotations

from app.market_intelligence.utils import clamp
from app.price_action.anatomy import PatternInputs, apply_context, insufficient, make_result
from app.price_action.schemas import PatternDirection, PatternResult, PatternType


def detect_narrow_range(inputs: PatternInputs, index: int) -> PatternResult:
    cfg = inputs.config.narrow_range
    needed = max(cfg.lookback - 1, inputs.config.context.average_window)
    if not inputs.has_history(index, needed):
        return insufficient(PatternType.NR, inputs, index, needed)

    candle = inputs.anatomy[index]
    previous_ranges = [inputs.anatomy[j].range for j in range(index - cfg.lookback + 1, index)]
    short_ranges = previous_ranges[-(cfg.short_lookback - 1) :]
    is_nr = candle.range > 0 and candle.range < min(previous_ranges)
    is_nr_short = candle.range > 0 and candle.range < min(short_ranges)
    average_range = inputs.average_range(index) or 0.0
    ratio = candle.range / average_range if average_range else 1.0
    previous = inputs.anatomy[index - 1]
    inside_bar = candle.high <= previous.high and candle.low >= previous.low

    direction = PatternDirection.NEUTRAL
    context = inputs.context(index, direction)
    metrics = {
        "range": candle.range,
        "range_vs_average": ratio,
        "nr_short": float(is_nr_short),
        "inside_bar": float(inside_bar),
    }

    if not is_nr:
        reason = (
            f"Range is not the narrowest of the last {cfg.lookback} candles"
            if candle.range > 0
            else "Candle has no range"
        )
        if is_nr_short:
            reason += f" (it is an NR{cfg.short_lookback})"
        return make_result(
            PatternType.NR,
            inputs,
            index,
            detected=False,
            direction=direction,
            context=context,
            reasons=[reason],
            metrics=metrics,
        )

    reasons = [f"Narrowest range of the last {cfg.lookback} candles ({ratio:.2f}x average)"]
    confidence = 50.0
    if ratio <= cfg.strong_ratio:
        confidence += 15
        reasons.append("Range is half the recent average or less")
    if inside_bar:
        confidence += 15
        reasons.append("Inside bar: high and low within the previous candle")
    confidence = apply_context(
        confidence, direction, context, inputs.config, reasons, volume_matters=False
    )
    reasons.append("Contraction only: says nothing about the direction of any expansion")
    return make_result(
        PatternType.NR,
        inputs,
        index,
        detected=True,
        direction=direction,
        context=context,
        reasons=reasons,
        confidence=confidence,
        strength=clamp(100.0 * (1.0 - ratio)),
        metrics=metrics,
    )


def detect_narrow_range_body(inputs: PatternInputs, index: int) -> PatternResult:
    cfg = inputs.config.narrow_body
    needed = inputs.config.context.average_window
    if not inputs.has_history(index, needed):
        return insufficient(PatternType.NRB, inputs, index, needed)

    candle = inputs.anatomy[index]
    average_body = inputs.average_body(index) or 0.0
    average_range = inputs.average_range(index) or 0.0
    body_vs_average = candle.body / average_body if average_body else 1.0
    direction = PatternDirection.NEUTRAL
    context = inputs.context(index, direction)
    metrics = {
        "body_ratio": candle.body_ratio,
        "body_vs_average": body_vs_average,
        "range_vs_average": candle.range / average_range if average_range else 1.0,
    }

    failures = []
    if candle.range == 0:
        failures.append("Candle has no range")
    if candle.body_ratio > cfg.max_body_ratio:
        failures.append(
            f"Body is {candle.body_ratio:.0%} of the range (needs <= {cfg.max_body_ratio:.0%})"
        )
    if body_vs_average > cfg.max_body_vs_average:
        failures.append(
            f"Body is {body_vs_average:.2f}x the average body "
            f"(needs <= {cfg.max_body_vs_average:.2f}x)"
        )
    if failures:
        return make_result(
            PatternType.NRB,
            inputs,
            index,
            detected=False,
            direction=direction,
            context=context,
            reasons=failures,
            metrics=metrics,
        )

    reasons = [
        f"Small body: {candle.body_ratio:.0%} of its range, "
        f"{body_vs_average:.2f}x the average body"
    ]
    confidence = 50.0
    if average_range and candle.range <= average_range:
        confidence += 15
        reasons.append("Range is also below average")
    if candle.upper_wick > 0 and candle.lower_wick > 0:
        balance = min(candle.upper_wick, candle.lower_wick) / max(
            candle.upper_wick, candle.lower_wick
        )
        if balance >= 0.5:
            confidence += 5
            reasons.append("Wicks on both sides: two-way rejection")
    confidence = apply_context(
        confidence, direction, context, inputs.config, reasons, volume_matters=False
    )
    reasons.append("Indecision only: no direction on its own")
    return make_result(
        PatternType.NRB,
        inputs,
        index,
        detected=True,
        direction=direction,
        context=context,
        reasons=reasons,
        confidence=confidence,
        strength=clamp(100.0 * (1.0 - body_vs_average)),
        metrics=metrics,
    )
