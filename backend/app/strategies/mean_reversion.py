"""Mean reversion.

Concept (from the brief): UPPER RANGE -> EXTREME -> REVERSAL -> VWAP /
MEAN, or the mirror from the lower range. Only for range conditions:
it must never fade a strong trend.

Required (all must hold):
- not_trending:   trend quality, directional efficiency and the
                  higher-timeframe bias are all below their limits
                  (any one of them high means "strong trend": refuse)
- range_intact:   a range high and low exist and price has not closed
                  out of the range (no breakout / breakdown level)
- at_boundary:    the latest candle reached the range high (for a short)
                  or the range low (for a long)
- vwap_deviation: price is stretched at least `min_vwap_deviation_atr`
                  from VWAP, on the side of that boundary
- reversal:       a reversal pattern against the stretch on the latest candle

Weighted: the regime is RANGE, extension from the fast EMA, momentum
slowing, a strong level at the boundary, volume on the reversal.

Candidates: entry beyond the reversal candle, stop beyond the range
extreme, target at VWAP (the mean). Without enough room to VWAP the
setup is ineligible.
"""

from __future__ import annotations

from typing import ClassVar

from app.price_action.schemas import LevelType, PatternDirection
from app.regime.schemas import MarketRegime
from app.strategies.base import BaseStrategy, Features, check, side
from app.strategies.config import MeanReversionConfig
from app.strategies.context import StrategyContext
from app.strategies.schemas import StrategyCheck, StrategyName, TradeDirection


class MeanReversionStrategy(BaseStrategy):
    name = StrategyName.MEAN_REVERSION
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("RANGE", "range_intact"),
        ("RANGE_EDGE", "at_boundary"),
        ("EXTREME", "vwap_deviation"),
        ("REVERSAL", "reversal"),
    )

    def __init__(self, config: MeanReversionConfig | None = None) -> None:
        self.config = config or MeanReversionConfig()
        super().__init__(self.config.geometry, self.config.weights)

    # --- analyze ------------------------------------------------------------------

    def analyze(self, ctx: StrategyContext) -> Features:
        cfg, a, pa = self.config, ctx.analysis, ctx.price_action
        latest = ctx.latest
        f: Features = {
            "regime": ctx.regime.regime.value,
            "atr": round(ctx.atr, 4),
            "price": ctx.price,
            "trend_quality": a.trend_quality.score if a.trend_quality else None,
            "efficiency_ratio": a.range.efficiency_ratio if a.range else None,
        }
        mtf = pa.multi_timeframe
        f["context_bias"] = (
            mtf.context_analysis.bias if mtf and mtf.context_analysis.available else None
        )

        sr = pa.support_resistance
        levels = sr.levels if sr is not None else []
        by_type = {lvl.type: lvl for lvl in levels}
        high = by_type.get(LevelType.RANGE_HIGH)
        low = by_type.get(LevelType.RANGE_LOW)
        f["range_high"] = high.price if high else None
        f["range_low"] = low.price if low else None
        f["range_high_strength"] = high.strength if high else None
        f["range_low_strength"] = low.strength if low else None
        broken = [
            t.value for t in (LevelType.BREAKOUT_LEVEL, LevelType.BREAKDOWN_LEVEL) if t in by_type
        ]
        f["range_broken"] = ",".join(broken) or None

        proximity = cfg.boundary_proximity_atr * ctx.atr
        side_name = None
        if high is not None and latest.high >= high.price - proximity:
            side_name = "SHORT"
        elif low is not None and latest.low <= low.price + proximity:
            side_name = "LONG"
        f["fade_direction"] = side_name

        vwap = a.vwap.vwap if a.vwap else None
        f["vwap"] = vwap
        f["vwap_deviation_atr"] = (
            round((ctx.price - vwap) / ctx.atr, 2) if vwap is not None else None
        )
        ema = a.moving_averages.ema_fast if a.moving_averages else None
        f["extension_atr"] = round((ctx.price - ema) / ctx.atr, 2) if ema is not None else None

        rocs = a.momentum.rate_of_change_percent if a.momentum else {}
        if rocs:
            short_period, long_period = min(rocs), max(rocs)
            f["roc_short"] = rocs[short_period]
            f["roc_long"] = rocs[long_period]

        sign = side(TradeDirection(side_name)) if side_name else 0.0
        reversal = [
            p.pattern.value
            for p in pa.patterns
            if p.detected
            and p.pattern.value in cfg.reversal_patterns
            and (
                (p.direction == PatternDirection.BULLISH and sign > 0)
                or (p.direction == PatternDirection.BEARISH and sign < 0)
            )
        ]
        f["reversal_patterns"] = ",".join(reversal) or None
        f["relative_volume"] = a.volume.relative_volume if a.volume else None
        return f

    # --- validate -----------------------------------------------------------------

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        raw = f.get("fade_direction")
        direction = TradeDirection(raw) if isinstance(raw, str) else TradeDirection.NONE
        sign = side(direction)
        checks: list[StrategyCheck] = []

        quality = f.get("trend_quality")
        efficiency = f.get("efficiency_ratio")
        bias = f.get("context_bias")
        trending = []
        if isinstance(quality, float) and quality > cfg.max_trend_quality:
            trending.append(f"trend quality {quality:.0f} > {cfg.max_trend_quality:g}")
        if isinstance(efficiency, float) and efficiency > cfg.max_efficiency_ratio:
            trending.append(f"efficiency {efficiency:.2f} > {cfg.max_efficiency_ratio:g}")
        if isinstance(bias, float) and abs(bias) > cfg.max_higher_timeframe_bias:
            trending.append(f"higher-timeframe bias {bias:+.0f}")
        checks.append(
            check(
                "not_trending",
                "No strong trend",
                not trending,
                (
                    "; ".join(trending) + " (strong trend: not faded)"
                    if trending
                    else "trend measures are low"
                ),
                required=True,
            )
        )

        intact = (
            f.get("range_high") is not None
            and f.get("range_low") is not None
            and not f.get("range_broken")
        )
        checks.append(
            check(
                "range_intact",
                "Range intact",
                intact,
                (
                    f"range {f.get('range_low')}-{f.get('range_high')}"
                    if intact
                    else (
                        f"range broken ({f.get('range_broken')})"
                        if f.get("range_broken")
                        else "no range boundaries"
                    )
                ),
                required=True,
                weight=w["range_regime"] / 2,
            )
        )
        at_edge = direction != TradeDirection.NONE
        checks.append(
            check(
                "at_boundary",
                "At range boundary",
                at_edge,
                (
                    ("at the range high" if sign < 0 else "at the range low")
                    if at_edge
                    else "price is inside the range, away from both boundaries"
                ),
                required=True,
            )
        )
        deviation = f.get("vwap_deviation_atr")
        stretched = (
            isinstance(deviation, float)
            and sign != 0
            and -deviation * sign >= cfg.min_vwap_deviation_atr
        )
        checks.append(
            check(
                "vwap_deviation",
                "VWAP deviation",
                stretched,
                f"{deviation} ATR from VWAP (needs {cfg.min_vwap_deviation_atr:g} on the stretched side)",
                required=True,
                weight=w["vwap_deviation"],
            )
        )
        reversal = f.get("reversal_patterns")
        checks.append(
            check(
                "reversal",
                "Reversal price action",
                bool(reversal),
                (
                    f"{reversal} on the latest candle"
                    if reversal
                    else "no reversal pattern on the latest candle"
                ),
                required=True,
                weight=w["reversal"],
            )
        )

        checks.append(
            check(
                "range_regime",
                "Range regime",
                f["regime"] == MarketRegime.RANGE.value,
                f"regime {f['regime']}",
                weight=w["range_regime"] / 2,
            )
        )
        extension = f.get("extension_atr")
        checks.append(
            check(
                "extension",
                "Extension",
                isinstance(extension, float)
                and sign != 0
                and -extension * sign >= cfg.min_extension_atr,
                f"{extension} ATR from the fast EMA",
                weight=w["extension"],
            )
        )
        roc_short, roc_long = f.get("roc_short"), f.get("roc_long")
        slowing = (
            isinstance(roc_short, float)
            and isinstance(roc_long, float)
            and sign != 0
            and (roc_short * sign >= 0 or abs(roc_short) < abs(roc_long) / 3)
        )
        checks.append(
            check(
                "slowdown",
                "Momentum slowdown",
                slowing,
                f"short ROC {roc_short}% vs longer ROC {roc_long}%",
                weight=w["slowdown"],
            )
        )
        strength = f.get("range_high_strength") if sign < 0 else f.get("range_low_strength")
        checks.append(
            check(
                "level",
                "Boundary level",
                isinstance(strength, float) and strength >= 50,
                f"boundary strength {strength}",
                weight=w["level"],
            )
        )
        rv = f.get("relative_volume")
        checks.append(
            check(
                "volume",
                "Volume",
                isinstance(rv, float) and rv >= 1.0,
                f"reversal candle {rv}x average volume",
                weight=w["volume"],
            )
        )
        return direction, checks

    # --- candidates ---------------------------------------------------------------

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        if direction == TradeDirection.LONG:
            return ctx.latest.high, "above the reversal candle's high"
        return ctx.latest.low, "below the reversal candle's low"

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        buffer = self.geometry.stop_buffer_atr * ctx.atr
        if direction == TradeDirection.LONG:
            low = min(ctx.latest.low, float(f["range_low"]))  # type: ignore[arg-type]
            return low - buffer, "below the range low and the reversal candle"
        high = max(ctx.latest.high, float(f["range_high"]))  # type: ignore[arg-type]
        return high + buffer, "above the range high and the reversal candle"

    def calculate_target(
        self,
        ctx: StrategyContext,
        direction: TradeDirection,
        entry: float,
        stop: float,
        f: Features,
    ) -> tuple[float, str]:
        return float(f["vwap"]), "VWAP (the mean)"  # type: ignore[arg-type]
