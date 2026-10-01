"""Retracement engine.

1. Find significant swings (ATR zig-zag, see swing_structure.py) and the
   legs between them.
2. The impulse is the largest leg among the last `search_legs` (the leg
   in progress included) that spans at least `impulse_min_atr` ATR.
   Choosing the largest stops a pullback inside a bigger move from
   being mistaken for the impulse.
3. The impulse runs from its start pivot A to B, the furthest price
   reached in the impulse direction since A.
4. Retracement = how much of A->B price has given back, now (from the
   latest close) and at its deepest since B.

Zones (configurable bounds): < 40% shallow, 40-50% healthy pullback,
50-60% deep pullback, 60-80% weakening, 80-100% major weakness,
>= 100% invalidation context (the whole impulse is retraced).

Retracement levels are reference zones only. Price can and does move
straight through them; they are not guaranteed reversal points.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import Trend
from app.market_intelligence.utils import clamp, mean
from app.price_action.config import RetracementConfig, SwingConfig
from app.price_action.schemas import LEVEL_DISCLAIMER, RetracementZone
from app.price_action.swing_structure import (
    SwingLeg,
    legs_from_pivots,
    reference_atr,
    zigzag_pivots,
)


class RetracementQuality(str, Enum):
    STRONG = "STRONG"
    HEALTHY = "HEALTHY"
    ACCEPTABLE = "ACCEPTABLE"
    WEAK = "WEAK"
    VERY_WEAK = "VERY_WEAK"
    INVALIDATED = "INVALIDATED"


_QUALITY: dict[RetracementZone, RetracementQuality] = {
    RetracementZone.SHALLOW: RetracementQuality.STRONG,
    RetracementZone.HEALTHY_PULLBACK: RetracementQuality.HEALTHY,
    RetracementZone.DEEP_PULLBACK: RetracementQuality.ACCEPTABLE,
    RetracementZone.WEAKENING: RetracementQuality.WEAK,
    RetracementZone.MAJOR_WEAKNESS: RetracementQuality.VERY_WEAK,
    RetracementZone.INVALIDATION_CONTEXT: RetracementQuality.INVALIDATED,
}

# Confidence points by zone: a clean, moderate pullback is the clearest
# reading of an impulse; a full retracement muddies what the impulse was.
_ZONE_CONFIDENCE: dict[RetracementZone, float] = {
    RetracementZone.SHALLOW: 15.0,
    RetracementZone.HEALTHY_PULLBACK: 20.0,
    RetracementZone.DEEP_PULLBACK: 10.0,
    RetracementZone.WEAKENING: 0.0,
    RetracementZone.MAJOR_WEAKNESS: -10.0,
    RetracementZone.INVALIDATION_CONTEXT: -20.0,
}

ZONE_LABELS: dict[RetracementZone, str] = {
    RetracementZone.SHALLOW: "Shallow",
    RetracementZone.HEALTHY_PULLBACK: "Healthy pullback",
    RetracementZone.DEEP_PULLBACK: "Deep pullback",
    RetracementZone.WEAKENING: "Weakening",
    RetracementZone.MAJOR_WEAKNESS: "Major weakness",
    RetracementZone.INVALIDATION_CONTEXT: "Invalidation context",
}


def zone_for(
    percentage: float, bounds: tuple[float, float, float, float, float]
) -> RetracementZone:
    zones = [
        RetracementZone.SHALLOW,
        RetracementZone.HEALTHY_PULLBACK,
        RetracementZone.DEEP_PULLBACK,
        RetracementZone.WEAKENING,
        RetracementZone.MAJOR_WEAKNESS,
    ]
    for bound, zone in zip(bounds, zones, strict=True):
        if percentage < bound:
            return zone
    return RetracementZone.INVALIDATION_CONTEXT


class RetracementLevel(BaseModel):
    percent: float
    price: float


class RetracementResult(BaseModel):
    detected: bool
    direction: Trend | None = None  # direction of the impulse
    swing_high: float | None = None
    swing_low: float | None = None
    swing_high_time: datetime | None = None
    swing_low_time: datetime | None = None
    impulse_start: float | None = None
    impulse_end: float | None = None
    impulse_size: float | None = None
    impulse_size_atr: float | None = None
    impulse_candles: int | None = None
    retracement_percentage: float | None = None  # at the latest close
    max_retracement_percentage: float | None = None  # deepest since the impulse extreme
    zone: RetracementZone | None = None
    max_zone: RetracementZone | None = None
    quality: RetracementQuality | None = None
    levels: list[RetracementLevel] = Field(default_factory=list)
    strength: float = 0.0  # 0-100: how strong the impulse was
    confidence: float = 0.0  # 0-100: how clear this reading is
    candles_since_extreme: int | None = None
    reasons: list[str] = Field(default_factory=list)
    note: str = LEVEL_DISCLAIMER


def _choose_impulse(legs: list[SwingLeg], cfg: RetracementConfig) -> SwingLeg | None:
    candidates = [leg for leg in legs[-cfg.search_legs :] if leg.size_atr >= cfg.impulse_min_atr]
    if not candidates:
        return None
    return max(candidates, key=lambda leg: (leg.size, leg.end.index))


def analyze_retracement(
    candles: list[Candle],
    config: RetracementConfig | None = None,
    swing_config: SwingConfig | None = None,
) -> RetracementResult:
    cfg = config or RetracementConfig()
    swing_cfg = swing_config or SwingConfig()
    if len(candles) < swing_cfg.atr_period + 2:
        return RetracementResult(
            detected=False, reasons=["Not enough candles to find significant swings"]
        )

    atr = reference_atr(candles, swing_cfg.atr_period)
    legs = legs_from_pivots(zigzag_pivots(candles, swing_cfg.reversal_atr * atr), atr)
    impulse = _choose_impulse(legs, cfg)
    if impulse is None:
        largest = max((leg.size_atr for leg in legs[-cfg.search_legs :]), default=0.0)
        return RetracementResult(
            detected=False,
            reasons=[
                f"No impulse of at least {cfg.impulse_min_atr:g} ATR among the last "
                f"{cfg.search_legs} swing legs (largest {largest:.1f} ATR)"
            ],
        )

    up = impulse.direction == Trend.UP
    start = impulse.start
    after_start = candles[start.index :]
    if up:
        offset = max(range(len(after_start)), key=lambda k: (after_start[k].high, -k))
        end_price = after_start[offset].high
    else:
        offset = min(range(len(after_start)), key=lambda k: (after_start[k].low, k))
        end_price = after_start[offset].low
    end_index = start.index + offset
    size = abs(end_price - start.price)
    if size == 0:
        return RetracementResult(detected=False, reasons=["Impulse has no size"])

    close = candles[-1].close
    after_end = candles[end_index + 1 :]
    if up:
        current = (end_price - close) / size * 100.0
        deepest = (end_price - min(c.low for c in after_end)) / size * 100.0 if after_end else 0.0
    else:
        current = (close - end_price) / size * 100.0
        deepest = (max(c.high for c in after_end) - end_price) / size * 100.0 if after_end else 0.0
    current = max(current, 0.0)
    deepest = max(deepest, current)

    zone = zone_for(current, cfg.zone_bounds)
    max_zone = zone_for(deepest, cfg.zone_bounds)
    sign = -1.0 if up else 1.0
    levels = [
        RetracementLevel(percent=p, price=round(end_price + sign * p / 100.0 * size, 4))
        for p in cfg.levels
    ]

    # Strength of the impulse: size (50%), straightness (30%), volume (20%).
    size_atr = size / atr if atr else 0.0
    size_score = clamp(size_atr / cfg.full_strength_atr * 100.0)
    impulse_candles = candles[start.index : end_index + 1]
    path = sum(
        abs(b.close - a.close) for a, b in zip(impulse_candles, impulse_candles[1:], strict=False)
    )
    efficiency = (
        clamp(abs(impulse_candles[-1].close - impulse_candles[0].close) / path, 0, 1)
        if path
        else 0.0
    )
    before = candles[max(0, start.index - cfg.volume_window) : start.index]
    baseline_volume = mean([c.volume for c in before]) if before else 0.0
    impulse_volume = mean([c.volume for c in impulse_candles])
    volume_ratio = impulse_volume / baseline_volume if baseline_volume else 1.0
    volume_score = clamp((volume_ratio - 0.8) / 0.7 * 100.0)  # 0.8x -> 0, 1.5x -> 100
    strength = 0.5 * size_score + 0.3 * efficiency * 100.0 + 0.2 * volume_score

    since_extreme = len(candles) - 1 - end_index
    significance = clamp(size_atr / (2 * cfg.impulse_min_atr), 0, 1)
    confidence = 40.0 + 30.0 * significance + _ZONE_CONFIDENCE[zone]
    if since_extreme > cfg.stale_after_candles:
        confidence -= 10.0

    direction_word = "up" if up else "down"
    pullback_word = "down" if up else "up"
    reasons = [
        f"Impulse {direction_word} {size_atr:.1f} ATR over {end_index - start.index} candles "
        f"({start.price:g} to {end_price:g})",
        f"Price has retraced {current:.0f}% of it ({ZONE_LABELS[zone].lower()})",
    ]
    if deepest > current + 1:
        reasons.append(f"Deepest pullback so far {deepest:.0f}% ({ZONE_LABELS[max_zone].lower()})")
    if current < 5:
        reasons.append(
            f"Price is still near the impulse extreme; no meaningful pullback {pullback_word} yet"
        )
    if zone == RetracementZone.INVALIDATION_CONTEXT:
        reasons.append("The entire impulse has been given back")
    if efficiency >= 0.6:
        reasons.append(f"Impulse was direct (efficiency {efficiency:.2f})")
    if volume_ratio >= 1.3:
        reasons.append(f"Impulse volume {volume_ratio:.1f}x the prior average")
    if since_extreme > cfg.stale_after_candles:
        reasons.append(f"Impulse extreme is {since_extreme} candles old")

    high_price, low_price = (end_price, start.price) if up else (start.price, end_price)
    high_time = candles[end_index].timestamp if up else start.timestamp
    low_time = start.timestamp if up else candles[end_index].timestamp
    return RetracementResult(
        detected=True,
        direction=impulse.direction,
        swing_high=high_price,
        swing_low=low_price,
        swing_high_time=high_time,
        swing_low_time=low_time,
        impulse_start=start.price,
        impulse_end=end_price,
        impulse_size=round(size, 4),
        impulse_size_atr=round(size_atr, 2),
        impulse_candles=end_index - start.index,
        retracement_percentage=round(current, 1),
        max_retracement_percentage=round(deepest, 1),
        zone=zone,
        max_zone=max_zone,
        quality=_QUALITY[zone],
        levels=levels,
        strength=round(clamp(strength), 1),
        confidence=round(clamp(confidence), 1),
        candles_since_extreme=since_extreme,
        reasons=reasons,
    )
