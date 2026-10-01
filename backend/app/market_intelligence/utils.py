"""Small numeric helpers shared by the market intelligence engines."""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.market_intelligence.schemas import Relationship


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def percent_change(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return (new - old) / old * 100.0


def relationship(value: float, reference: float, tolerance_percent: float) -> Relationship:
    """ABOVE / BELOW / AT, where AT means within `tolerance_percent` of
    the reference."""

    if reference == 0:
        return Relationship.AT
    diff_percent = (value - reference) / reference * 100.0
    if abs(diff_percent) <= tolerance_percent:
        return Relationship.AT
    return Relationship.ABOVE if diff_percent > 0 else Relationship.BELOW


def slope_percent(series: Sequence[float | None], lookback: int) -> float | None:
    """Average percent change per candle of `series` over its last
    `lookback` steps. Expressed in percent (not price units) so slopes
    are comparable across instruments with very different prices.
    Returns None if either endpoint is unavailable."""

    if lookback < 1 or len(series) <= lookback:
        return None
    latest = series[-1]
    past = series[-1 - lookback]
    if latest is None or past is None or past == 0:
        return None
    return (latest - past) / past * 100.0 / lookback


def sigmoid_score(x: float, steepness: float = 1.0) -> float:
    """Map an unbounded value onto 0-100, with 0 -> 50."""

    z = clamp(-steepness * x, -60.0, 60.0)
    return 100.0 / (1.0 + math.exp(z))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))
