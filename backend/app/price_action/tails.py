"""Bottoming Tail (BT) and Topping Tail (TT).

BT — a long lower wick (tail) with the close in the upper part of the
     range: price traded lower during the candle and was pushed back
     up. Described as BULLISH (rejection of lower prices).
TT — the mirror: a long upper wick and a close in the lower part of
     the range. Described as BEARISH (rejection of higher prices).

A tail is most meaningful after a move into it (a BT after a decline,
a TT after an advance) and at a level, so both raise confidence. Tiny
candles are ignored: a long tail on a candle a fraction of ATR wide
says little. A rejection describes what happened during the candle;
it does not mean the move is over.
"""

from __future__ import annotations

from app.market_intelligence.schemas import Trend
from app.market_intelligence.utils import clamp
from app.price_action.anatomy import (
    CandleAnatomy,
    PatternInputs,
    apply_context,
    insufficient,
    make_result,
)
from app.price_action.schemas import PatternDirection, PatternResult, PatternType


def _detect_tail(inputs: PatternInputs, index: int, *, bottoming: bool) -> PatternResult:
    cfg = inputs.config.tails
    pattern = PatternType.BT if bottoming else PatternType.TT
    needed = inputs.config.context.average_window
    if not inputs.has_history(index, needed):
        return insufficient(pattern, inputs, index, needed)

    candle: CandleAnatomy = inputs.anatomy[index]
    tail = candle.lower_wick if bottoming else candle.upper_wick
    tail_ratio = candle.lower_wick_ratio if bottoming else candle.upper_wick_ratio
    close_position = candle.close_position if bottoming else 1.0 - candle.close_position
    atr = inputs.reference_atr(index) or 0.0
    range_atr = candle.range / atr if atr else 0.0
    direction = PatternDirection.BULLISH if bottoming else PatternDirection.BEARISH
    context = inputs.context(index, direction)
    metrics = {
        "tail_ratio": tail_ratio,
        "tail_vs_body": tail / candle.body if candle.body > 0 else 99.0,
        "close_position": close_position,
        "range_atr": range_atr,
    }

    which = "lower" if bottoming else "upper"
    half = "upper" if bottoming else "lower"
    failures = []
    if tail_ratio < cfg.min_tail_ratio:
        failures.append(
            f"{which.capitalize()} tail is {tail_ratio:.0%} of the range "
            f"(needs >= {cfg.min_tail_ratio:.0%})"
        )
    if candle.body > 0 and tail < cfg.min_tail_vs_body * candle.body:
        failures.append(f"Tail is shorter than {cfg.min_tail_vs_body:g}x the body")
    if close_position < cfg.min_close_position:
        failures.append(f"Did not close in the {half} half of its range")
    if atr and range_atr < cfg.min_range_atr:
        failures.append(f"Candle too small to matter ({range_atr:.2f} ATR)")
    if failures:
        return make_result(
            pattern,
            inputs,
            index,
            detected=False,
            direction=direction,
            context=context,
            reasons=failures,
            metrics=metrics,
        )

    rejected = "lower" if bottoming else "higher"
    reasons = [
        f"{which.capitalize()} tail {tail_ratio:.0%} of the range: {rejected} prices rejected"
    ]
    confidence = 50.0

    previous = inputs.candles[index - 1]
    current = inputs.candles[index]
    if bottoming and current.low < previous.low:
        confidence += 5
        reasons.append("Undercut the previous low before recovering")
    elif not bottoming and current.high > previous.high:
        confidence += 5
        reasons.append("Overshot the previous high before falling back")
    if close_position >= 0.75:
        confidence += 5
        reasons.append(f"Closed in the {half} quarter of its range")

    confidence = apply_context(
        confidence,
        direction,
        context,
        inputs.config,
        reasons,
        reversal_after=Trend.DOWN if bottoming else Trend.UP,
    )
    # Strength: how dominant the tail is (50% -> 0, 90%+ -> 100) blended
    # with how large the candle is relative to ATR (1.5 ATR -> full).
    tail_score = clamp((tail_ratio - cfg.min_tail_ratio) / (0.9 - cfg.min_tail_ratio) * 100.0)
    size_score = clamp(range_atr / 1.5 * 100.0) if atr else 50.0
    return make_result(
        pattern,
        inputs,
        index,
        detected=True,
        direction=direction,
        context=context,
        reasons=reasons,
        confidence=confidence,
        strength=0.7 * tail_score + 0.3 * size_score,
        metrics=metrics,
    )


def detect_bottoming_tail(inputs: PatternInputs, index: int) -> PatternResult:
    return _detect_tail(inputs, index, bottoming=True)


def detect_topping_tail(inputs: PatternInputs, index: int) -> PatternResult:
    return _detect_tail(inputs, index, bottoming=False)
