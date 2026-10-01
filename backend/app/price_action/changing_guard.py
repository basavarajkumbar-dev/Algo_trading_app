"""Changing of the Guard (COG).

A change in which side controls the candles:

- Bullish COG: a run of at least `min_run` bearish candles (each with a
  lower high than the one before, when `require_progressive_run`),
  followed by a bullish candle that closes above the previous candle's
  high.
- Bearish COG: a run of bullish candles (higher lows), followed by a
  bearish candle that closes below the previous candle's low.

Strength reflects how decisively control changed: how many of the run's
highs (lows) the new candle closed beyond and by how much in ATR terms.
A COG describes a shift in short-term control; it does not mean the
prior move has ended.
"""

from __future__ import annotations

from app.market_intelligence.schemas import Trend
from app.market_intelligence.utils import clamp
from app.price_action.anatomy import PatternInputs, apply_context, insufficient, make_result
from app.price_action.schemas import CandleColor, PatternDirection, PatternResult, PatternType


def _run_length(inputs: PatternInputs, index: int, color: CandleColor) -> int:
    """Consecutive candles of `color` ending at index-1, each extending
    the run (lower highs for a bearish run, higher lows for a bullish
    one) when the config requires it."""

    progressive = inputs.config.changing_guard.require_progressive_run
    length = 0
    j = index - 1
    while j >= 0 and inputs.anatomy[j].color == color:
        if progressive and length > 0:
            later = inputs.candles[j + 1]
            earlier = inputs.candles[j]
            if color == CandleColor.BEARISH and not later.high < earlier.high:
                break
            if color == CandleColor.BULLISH and not later.low > earlier.low:
                break
        length += 1
        j -= 1
    return length


def detect_changing_guard(inputs: PatternInputs, index: int) -> PatternResult:
    cfg = inputs.config.changing_guard
    needed = max(inputs.config.context.average_window, cfg.min_run)
    if not inputs.has_history(index, needed):
        return insufficient(PatternType.COG, inputs, index, needed)

    candle = inputs.anatomy[index]
    previous = inputs.candles[index - 1]
    if candle.color == CandleColor.BULLISH:
        direction = PatternDirection.BULLISH
        run_color = CandleColor.BEARISH
    elif candle.color == CandleColor.BEARISH:
        direction = PatternDirection.BEARISH
        run_color = CandleColor.BULLISH
    else:
        direction = PatternDirection.NEUTRAL
        run_color = CandleColor.DOJI
    context = inputs.context(index, direction)

    run = _run_length(inputs, index, run_color) if direction != PatternDirection.NEUTRAL else 0
    bullish = direction == PatternDirection.BULLISH
    if bullish:
        crossed = candle.close > previous.high
    elif direction == PatternDirection.BEARISH:
        crossed = candle.close < previous.low
    else:
        crossed = False
    metrics = {"run_length": float(run)}

    failures = []
    if direction == PatternDirection.NEUTRAL:
        failures.append("Doji: no side took control")
    else:
        old = "bearish" if bullish else "bullish"
        if run < cfg.min_run:
            failures.append(f"Only {run} prior {old} candles in a run (needs {cfg.min_run})")
        if not crossed:
            edge = "high" if bullish else "low"
            failures.append(f"Did not close beyond the previous candle's {edge}")
    if failures:
        return make_result(
            PatternType.COG,
            inputs,
            index,
            detected=False,
            direction=direction,
            context=context,
            reasons=failures,
            metrics=metrics,
        )

    run_candles = inputs.candles[index - run : index]
    if bullish:
        taken_out = sum(1 for c in run_candles if candle.close > c.high)
        beyond = candle.close - previous.high
    else:
        taken_out = sum(1 for c in run_candles if candle.close < c.low)
        beyond = previous.low - candle.close
    atr = inputs.reference_atr(index) or 0.0
    beyond_atr = beyond / atr if atr else 0.0
    metrics.update({"run_extremes_taken": float(taken_out), "beyond_atr": beyond_atr})

    old, new = ("sellers", "buyers") if bullish else ("buyers", "sellers")
    edge = "high" if bullish else "low"
    reasons = [
        f"After {run} candles controlled by {old}, closed beyond the previous {edge}: "
        f"{new} took over",
        f"Closed beyond {taken_out} of the run's {run} {edge}s",
    ]
    confidence = 55.0
    if run >= cfg.min_run + 1:
        confidence += 10
        reasons.append("Long run before the change")
    close_strength = candle.close_position if bullish else 1.0 - candle.close_position
    if close_strength >= cfg.strong_close_position:
        confidence += 10
        reasons.append("Closed near its extreme")
    confidence = apply_context(
        confidence,
        direction,
        context,
        inputs.config,
        reasons,
        reversal_after=Trend.DOWN if bullish else Trend.UP,
    )
    strength = 60.0 * (taken_out / run) + 40.0 * clamp(beyond_atr / 1.0 * 100.0) / 100.0
    return make_result(
        PatternType.COG,
        inputs,
        index,
        detected=True,
        direction=direction,
        context=context,
        reasons=reasons,
        confidence=confidence,
        strength=strength,
        metrics=metrics,
    )
