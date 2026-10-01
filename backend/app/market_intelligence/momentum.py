"""Momentum engine.

A configurable 0-100 momentum score built from rate-of-change over
several lookback windows. Each window's ROC is normalised by the
instrument's recent per-candle volatility (so it is roughly a z-score:
"how many typical moves has price travelled?"). That makes the score
comparable across timeframes and instruments, unlike raw percentage
ROC, which is tiny on 5-minute candles and large on daily ones.

50 = no momentum, above 50 = upward momentum, below 50 = downward.
Momentum describes the recent past; it does not imply continuation.
"""

from __future__ import annotations

import math

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, Trend
from app.market_intelligence.utils import percent_change, sigmoid_score, stdev

#: (lookback_candles, weight) pairs.
DEFAULT_LOOKBACKS: tuple[tuple[int, float], ...] = ((3, 0.5), (10, 0.3), (20, 0.2))
DEFAULT_VOLATILITY_WINDOW = 30
DEFAULT_STEEPNESS = 1.2
#: |composite z| below this counts as no meaningful momentum.
DEFAULT_DIRECTION_THRESHOLD = 0.35


class MomentumResult(EngineResult):
    momentum_score: float
    direction: Trend
    composite_z: float
    rate_of_change_percent: dict[int, float]
    normalized_rate_of_change: dict[int, float]
    volatility_normalized: bool


def analyze_momentum(
    candles: list[Candle],
    *,
    lookbacks: tuple[tuple[int, float], ...] = DEFAULT_LOOKBACKS,
    volatility_window: int = DEFAULT_VOLATILITY_WINDOW,
    steepness: float = DEFAULT_STEEPNESS,
    direction_threshold: float = DEFAULT_DIRECTION_THRESHOLD,
) -> MomentumResult:
    if len(candles) < 2:
        raise InsufficientDataError("Momentum analysis needs at least 2 candles.")

    closes = [c.close for c in candles]
    tail = closes[-(volatility_window + 1) :]
    returns = [
        r for a, b in zip(tail, tail[1:], strict=False) if (r := percent_change(b, a)) is not None
    ]
    sigma = stdev(returns)
    normalize = sigma > 0 and len(returns) >= 5

    roc_by_period: dict[int, float] = {}
    z_by_period: dict[int, float] = {}
    weighted = 0.0
    total_weight = 0.0
    for period, weight in lookbacks:
        if len(closes) <= period:
            continue
        roc = percent_change(closes[-1], closes[-1 - period])
        if roc is None:
            continue
        z = roc / (sigma * math.sqrt(period)) if normalize else roc
        roc_by_period[period] = round(roc, 4)
        z_by_period[period] = round(z, 4)
        weighted += z * weight
        total_weight += weight

    composite = weighted / total_weight if total_weight else 0.0
    score = round(sigmoid_score(composite, steepness), 2)

    if composite > direction_threshold:
        direction = Trend.UP
    elif composite < -direction_threshold:
        direction = Trend.DOWN
    else:
        direction = Trend.SIDEWAYS

    reasons: list[str] = []
    if not roc_by_period:
        reasons.append("Not enough candles for any momentum lookback")
    elif direction == Trend.UP:
        reasons.append(f"Positive momentum (score {score:.0f})")
    elif direction == Trend.DOWN:
        reasons.append(f"Negative momentum (score {score:.0f})")
    else:
        reasons.append("Momentum is flat")

    return MomentumResult(
        momentum_score=score,
        direction=direction,
        composite_z=round(composite, 4),
        rate_of_change_percent=roc_by_period,
        normalized_rate_of_change=z_by_period,
        volatility_normalized=normalize,
        reasons=reasons,
    )
