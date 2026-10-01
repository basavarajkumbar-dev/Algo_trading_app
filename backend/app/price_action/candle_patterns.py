"""Candle pattern detection: runs every detector over a candle series.

Detectors (one module each):

- COG  Changing of the Guard   changing_guard.py
- NR   Narrow Range            narrow_range.py
- NRB  Narrow Range Body       narrow_range.py
- WRB  Wide Range Body         wide_range.py
- BT   Bottoming Tail          tails.py
- TT   Topping Tail            tails.py

Every detector returns a `PatternResult` with detected, direction,
confidence, strength, context and reasons, including when nothing was
detected (the reasons then say why). Patterns are contextual features
for later scoring layers. None of them is a trade signal.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.market_data.schemas import Candle
from app.price_action.anatomy import PatternInputs
from app.price_action.changing_guard import detect_changing_guard
from app.price_action.config import PatternConfig
from app.price_action.narrow_range import detect_narrow_range, detect_narrow_range_body
from app.price_action.schemas import (
    PatternDirection,
    PatternMarker,
    PatternResult,
    PatternType,
    ReferenceLevel,
)
from app.price_action.tails import detect_bottoming_tail, detect_topping_tail
from app.price_action.wide_range import detect_wide_range_body

Detector = Callable[[PatternInputs, int], PatternResult]

DETECTORS: dict[PatternType, Detector] = {
    PatternType.COG: detect_changing_guard,
    PatternType.NR: detect_narrow_range,
    PatternType.NRB: detect_narrow_range_body,
    PatternType.WRB: detect_wide_range_body,
    PatternType.BT: detect_bottoming_tail,
    PatternType.TT: detect_topping_tail,
}


def detect_patterns(
    candles: list[Candle],
    *,
    index: int = -1,
    levels: Sequence[ReferenceLevel] | None = None,
    config: PatternConfig | None = None,
    inputs: PatternInputs | None = None,
) -> list[PatternResult]:
    """Evaluate every detector on one candle (the latest by default).
    `levels` (e.g. from the support/resistance engine) feed the
    location part of each pattern's context."""

    if not candles:
        return []
    data = inputs or PatternInputs(candles, config, levels)
    position = index if index >= 0 else len(candles) + index
    return [detector(data, position) for detector in DETECTORS.values()]


def scan_patterns(
    candles: list[Candle],
    *,
    lookback: int | None = None,
    config: PatternConfig | None = None,
) -> list[PatternMarker]:
    """Detected patterns on each of the last `lookback` candles, for
    chart markers. Run without levels on purpose: today's levels did
    not exist when those candles formed, so using them would be
    look-ahead."""

    cfg = config or PatternConfig()
    data = PatternInputs(candles, cfg, levels=None)
    count = lookback if lookback is not None else cfg.scan_candles
    markers: list[PatternMarker] = []
    for position in range(max(0, len(candles) - count), len(candles)):
        for detector in DETECTORS.values():
            result = detector(data, position)
            if not result.detected:
                continue
            candle = candles[position]
            markers.append(
                PatternMarker(
                    pattern=result.pattern,
                    direction=result.direction,
                    confidence=result.confidence,
                    strength=result.strength,
                    timestamp=candle.timestamp,
                    price=(
                        candle.low if result.direction == PatternDirection.BULLISH else candle.high
                    ),
                )
            )
    return markers
