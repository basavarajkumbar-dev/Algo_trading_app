"""Trend Quality Score (0-100).

Combines the factors the project brief lists — direction, slope,
structure, price distance, momentum, volume and volatility — into one
number describing how clean and well-supported the current trend is.
0 means no usable trend; 100 means every factor agrees. The direction
itself (up/down/none) is reported separately.

Weights are configurable via `TrendQualityWeights`.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.market_intelligence.momentum import MomentumResult
from app.market_intelligence.moving_average import MAAlignment, MovingAverageResult
from app.market_intelligence.schemas import EngineResult, Trend, VolatilityLevel
from app.market_intelligence.structure import StructureResult
from app.market_intelligence.utils import clamp
from app.market_intelligence.volatility import VolatilityResult
from app.market_intelligence.volume import VolumeResult


class TrendQualityWeights(BaseModel):
    direction: float = 20
    slope: float = 15
    structure: float = 20
    distance: float = 10
    momentum: float = 15
    volume: float = 10
    volatility: float = 10
    slope_full_scale_percent: float = 0.05
    extension_percent: float = 1.5


class TrendQualityResult(EngineResult):
    direction: Trend
    score: float
    components: dict[str, float]


def compute_trend_quality(
    moving_averages: MovingAverageResult,
    structure: StructureResult | None,
    momentum: MomentumResult | None,
    volume: VolumeResult | None,
    volatility: VolatilityResult | None,
    *,
    weights: TrendQualityWeights | None = None,
) -> TrendQualityResult:
    w = weights or TrendQualityWeights()
    direction = moving_averages.trend
    components: dict[str, float] = {}
    reasons: list[str] = []

    if direction == Trend.SIDEWAYS:
        return TrendQualityResult(
            direction=direction,
            score=round(clamp(10.0 * (moving_averages.ma_trend_score / 100.0)), 2),
            components={},
            reasons=["No established moving-average trend"],
        )

    sign = 1 if direction == Trend.UP else -1
    expected_alignment = MAAlignment.BULLISH if sign > 0 else MAAlignment.BEARISH

    components["direction"] = (
        w.direction if moving_averages.alignment == expected_alignment else (w.direction * 0.5)
    )

    slope = (moving_averages.ema_fast_slope_percent or 0.0) * sign
    components["slope"] = w.slope * clamp(slope / w.slope_full_scale_percent, 0, 1)

    if structure is not None and structure.trend == direction:
        components["structure"] = w.structure * structure.confidence / 100.0
        reasons.append("Swing structure agrees with the trend")
    else:
        components["structure"] = 0.0

    distance = abs(moving_averages.distance_from_ema_fast_percent or 0.0)
    over = max(0.0, distance - w.extension_percent) / w.extension_percent
    components["distance"] = w.distance * clamp(1.0 - over, 0, 1)
    if over > 0:
        reasons.append("Price extended from the fast EMA")

    if momentum is not None and momentum.direction == direction:
        strength = abs(momentum.momentum_score - 50.0) / 50.0
        components["momentum"] = w.momentum * clamp(strength * 2, 0, 1)
        reasons.append("Momentum agrees with the trend")
    else:
        components["momentum"] = 0.0

    if volume is not None:
        components["volume"] = w.volume * clamp(volume.relative_volume / 1.5, 0, 1)
    else:
        components["volume"] = 0.0

    if volatility is None:
        components["volatility"] = w.volatility * 0.5
    else:
        components["volatility"] = (
            w.volatility
            * {
                VolatilityLevel.LOW: 1.0,
                VolatilityLevel.NORMAL: 1.0,
                VolatilityLevel.HIGH: 0.5,
                VolatilityLevel.EXTREME: 0.0,
            }[volatility.volatility_level]
        )
        if volatility.volatility_level == VolatilityLevel.EXTREME:
            reasons.append("Extreme volatility undermines trend quality")

    total_weight = (
        w.direction + w.slope + w.structure + w.distance + w.momentum + w.volume + w.volatility
    )
    score = sum(components.values()) / total_weight * 100.0 if total_weight else 0.0
    return TrendQualityResult(
        direction=direction,
        score=round(clamp(score), 2),
        components={k: round(v, 2) for k, v in components.items()},
        reasons=reasons,
    )
