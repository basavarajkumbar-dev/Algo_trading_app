"""Candle anatomy and pattern context, shared by every detector.

`PatternInputs` precomputes, once per candle series, everything the
detectors need (per-candle anatomy, ATR) and builds a `PatternContext`
for any candle. Context is computed only from candles *up to and
including* the candle being evaluated, so a pattern found on a past
candle is judged exactly as it could have been at the time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import Trend
from app.market_intelligence.utils import clamp, mean
from app.market_intelligence.volatility import compute_atr_series
from app.price_action.config import PatternConfig
from app.price_action.schemas import (
    PATTERN_NAMES,
    CandleColor,
    LevelLocation,
    NearbyLevel,
    PatternContext,
    PatternDirection,
    PatternResult,
    PatternType,
    ReferenceLevel,
)


@dataclass(frozen=True)
class CandleAnatomy:
    open: float
    high: float
    low: float
    close: float
    range: float
    body: float
    upper_wick: float
    lower_wick: float
    color: CandleColor

    @property
    def body_ratio(self) -> float:
        return self.body / self.range if self.range > 0 else 0.0

    @property
    def upper_wick_ratio(self) -> float:
        return self.upper_wick / self.range if self.range > 0 else 0.0

    @property
    def lower_wick_ratio(self) -> float:
        return self.lower_wick / self.range if self.range > 0 else 0.0

    @property
    def close_position(self) -> float:
        """0 = closed on the low, 1 = closed on the high."""

        return (self.close - self.low) / self.range if self.range > 0 else 0.5


def candle_anatomy(candle: Candle, doji_body_ratio: float = 0.1) -> CandleAnatomy:
    body = abs(candle.close - candle.open)
    candle_range = max(candle.high - candle.low, 0.0)
    if candle_range == 0 or body <= doji_body_ratio * candle_range:
        color = CandleColor.DOJI
    else:
        color = CandleColor.BULLISH if candle.close > candle.open else CandleColor.BEARISH
    return CandleAnatomy(
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        range=candle_range,
        body=body,
        upper_wick=candle.high - max(candle.open, candle.close),
        lower_wick=min(candle.open, candle.close) - candle.low,
        color=color,
    )


class PatternInputs:
    """Precomputed per-series data for the detectors."""

    def __init__(
        self,
        candles: list[Candle],
        config: PatternConfig | None = None,
        levels: Sequence[ReferenceLevel] | None = None,
    ) -> None:
        self.candles = candles
        self.config = config or PatternConfig()
        self.levels = list(levels) if levels is not None else None
        self.anatomy = [candle_anatomy(c, self.config.doji_body_ratio) for c in candles]
        self.atr = compute_atr_series(candles, self.config.context.atr_period) if candles else []

    # --- per-candle reference values ----------------------------------------

    def _window(self, index: int) -> range:
        w = self.config.context.average_window
        return range(max(0, index - w), index)

    def has_history(self, index: int, needed: int | None = None) -> bool:
        return index >= (needed if needed is not None else self.config.context.average_window)

    def average_range(self, index: int) -> float | None:
        values = [self.anatomy[j].range for j in self._window(index)]
        return mean(values) if values else None

    def average_body(self, index: int) -> float | None:
        values = [self.anatomy[j].body for j in self._window(index)]
        return mean(values) if values else None

    def relative_volume(self, index: int) -> float | None:
        values = [self.candles[j].volume for j in self._window(index)]
        average = mean(values) if values else 0.0
        return self.candles[index].volume / average if average > 0 else None

    def reference_atr(self, index: int) -> float | None:
        """ATR just before the candle, so the pattern candle does not
        inflate its own yardstick; falls back to the average range."""

        for j in (index - 1, index):
            if 0 <= j < len(self.atr) and self.atr[j]:
                return self.atr[j]
        return self.average_range(index)

    # --- context ----------------------------------------------------------------

    def context(self, index: int, direction: PatternDirection) -> PatternContext:
        cfg = self.config.context
        atr = self.reference_atr(index)
        start = index - 1 - cfg.trend_lookback
        prior_move_atr: float | None = None
        prior_trend = Trend.SIDEWAYS
        if start >= 0 and atr:
            prior_move_atr = (self.candles[index - 1].close - self.candles[start].close) / atr
            if prior_move_atr >= cfg.trend_threshold_atr:
                prior_trend = Trend.UP
            elif prior_move_atr <= -cfg.trend_threshold_atr:
                prior_trend = Trend.DOWN

        average_range = self.average_range(index)
        range_vs_average = self.anatomy[index].range / average_range if average_range else None

        location = LevelLocation.NO_LEVELS
        nearest: NearbyLevel | None = None
        if self.levels and atr:
            candle = self.candles[index]

            def gap(level: ReferenceLevel) -> float:
                """Distance from the candle's high-low range, in ATR;
                0 when the candle traded through the level."""

                if candle.low <= level.price <= candle.high:
                    return 0.0
                edge = candle.low if level.price < candle.low else candle.high
                return abs(level.price - edge) / atr

            closest = min(self.levels, key=gap)
            nearest = NearbyLevel(
                label=closest.label, price=closest.price, distance_atr=round(gap(closest), 2)
            )
            near = [lvl for lvl in self.levels if gap(lvl) <= cfg.level_proximity_atr]
            body_low = min(candle.open, candle.close)
            body_high = max(candle.open, candle.close)
            # A level inside the body was crossed by the candle, not tested:
            # calling it support or resistance would overstate the context.
            crossed = [lvl for lvl in near if body_low < lvl.price < body_high]
            tested = [lvl for lvl in near if lvl not in crossed]
            if tested:
                # A level at or below the close acts as support, above it as
                # resistance. Prefer the side that matters for the pattern's
                # direction; otherwise take the closest.
                supports = [lvl for lvl in tested if lvl.price <= candle.close]
                resistances = [lvl for lvl in tested if lvl.price > candle.close]
                if direction == PatternDirection.BULLISH and supports:
                    chosen, location = min(supports, key=gap), LevelLocation.AT_SUPPORT
                elif direction == PatternDirection.BEARISH and resistances:
                    chosen, location = min(resistances, key=gap), LevelLocation.AT_RESISTANCE
                else:
                    chosen = min(tested, key=gap)
                    location = (
                        LevelLocation.AT_SUPPORT
                        if chosen.price <= candle.close
                        else LevelLocation.AT_RESISTANCE
                    )
                nearest = NearbyLevel(
                    label=chosen.label, price=chosen.price, distance_atr=round(gap(chosen), 2)
                )
            elif crossed:
                chosen = min(crossed, key=lambda lvl: abs(lvl.price - candle.close))
                location = LevelLocation.THROUGH_LEVEL
                nearest = NearbyLevel(label=chosen.label, price=chosen.price, distance_atr=0.0)
            else:
                location = LevelLocation.BETWEEN_LEVELS

        return PatternContext(
            prior_trend=prior_trend,
            prior_move_atr=round(prior_move_atr, 2) if prior_move_atr is not None else None,
            atr=round(atr, 4) if atr else None,
            range_vs_average=round(range_vs_average, 2) if range_vs_average else None,
            relative_volume=(
                round(rv, 2) if (rv := self.relative_volume(index)) is not None else None
            ),
            location=location,
            nearest_level=nearest,
            levels_considered=self.levels is not None,
        )


def apply_context(
    base: float,
    direction: PatternDirection,
    context: PatternContext,
    config: PatternConfig,
    reasons: list[str],
    *,
    reversal_after: Trend | None = None,
    volume_matters: bool = True,
) -> float:
    """Adjust a detector's base confidence for where the pattern sits.

    - a bullish pattern at support (bearish at resistance) gains;
      a bullish pattern at resistance (bearish at support) loses;
      a neutral pattern at any level gains half the bonus;
    - `reversal_after`: the prior trend that makes this pattern a
      potential turn (e.g. a bottoming tail after a decline) gains;
    - unusually high volume gains.
    """

    confidence = base
    level = context.nearest_level
    if context.location == LevelLocation.AT_SUPPORT and level is not None:
        if direction == PatternDirection.BULLISH:
            confidence += config.support_bonus
            reasons.append(f"Formed at support ({level.label} {level.price:g})")
        elif direction == PatternDirection.BEARISH:
            confidence -= config.against_level_penalty
            reasons.append(f"Bearish candle sitting on support ({level.label})")
        else:
            confidence += config.support_bonus / 2
            reasons.append(f"Formed at a level ({level.label} {level.price:g})")
    elif context.location == LevelLocation.AT_RESISTANCE and level is not None:
        if direction == PatternDirection.BEARISH:
            confidence += config.support_bonus
            reasons.append(f"Formed at resistance ({level.label} {level.price:g})")
        elif direction == PatternDirection.BULLISH:
            confidence -= config.against_level_penalty
            reasons.append(f"Bullish candle running into resistance ({level.label})")
        else:
            confidence += config.support_bonus / 2
            reasons.append(f"Formed at a level ({level.label} {level.price:g})")

    if context.location == LevelLocation.THROUGH_LEVEL and level is not None:
        reasons.append(f"Body crossed {level.label} ({level.price:g}): no level bonus")

    if reversal_after is not None and context.prior_trend == reversal_after:
        confidence += config.reversal_context_bonus
        move = "decline" if reversal_after == Trend.DOWN else "advance"
        reasons.append(f"Follows a {move} ({context.prior_move_atr:+.1f} ATR)")

    if (
        volume_matters
        and context.relative_volume is not None
        and context.relative_volume >= config.volume_threshold
    ):
        confidence += config.volume_bonus
        reasons.append(f"Volume {context.relative_volume:.1f}x average")

    return round(clamp(confidence), 1)


def make_result(
    pattern: PatternType,
    inputs: PatternInputs,
    index: int,
    *,
    detected: bool,
    direction: PatternDirection,
    context: PatternContext,
    reasons: list[str],
    confidence: float = 0.0,
    strength: float = 0.0,
    metrics: dict[str, float] | None = None,
) -> PatternResult:
    return PatternResult(
        pattern=pattern,
        name=PATTERN_NAMES[pattern],
        detected=detected,
        direction=direction if detected else PatternDirection.NEUTRAL,
        confidence=confidence if detected else 0.0,
        strength=round(clamp(strength), 1) if detected else 0.0,
        context=context,
        reasons=reasons,
        metrics={k: round(v, 4) for k, v in (metrics or {}).items()},
        candle_index=index,
        timestamp=inputs.candles[index].timestamp,
    )


def insufficient(
    pattern: PatternType, inputs: PatternInputs, index: int, needed: int
) -> PatternResult:
    return make_result(
        pattern,
        inputs,
        index,
        detected=False,
        direction=PatternDirection.NEUTRAL,
        context=inputs.context(index, PatternDirection.NEUTRAL),
        reasons=[f"Needs {needed} prior candles; only {index} available"],
    )
