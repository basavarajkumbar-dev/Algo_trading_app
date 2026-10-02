"""Trend continuation.

Concept (from the brief): TREND -> IMPULSE -> PULLBACK -> SUPPORT ->
CONFIRMATION -> ENTRY CANDIDATE.

Required (all must hold):
- trend:        a direction from the regime (STRONG_BULLISH / BEARISH) or,
                failing that, from the higher (context) timeframe
- impulse:      a significant impulse in the trend direction that is not stale
- pullback:     price retraced between `min_pullback_percent` and
                `max_pullback_percent` of it and has not gone deeper since
- confirmation: a price action pattern in the trend direction on the
                latest candle, or momentum turning back with the trend on a
                candle closing in the trend direction

Weighted (add confidence): regime, higher timeframe alignment, market
structure, VWAP side, trend quality, support under the pullback,
volume, momentum, relative strength, price action pattern.

Candidates: entry beyond the latest (confirmation) candle's extreme,
stop beyond the pullback extreme, target at the nearest structure
level offering enough reward (else a multiple of the risk).
"""

from __future__ import annotations

from typing import ClassVar

from app.market_intelligence.schemas import Relationship, Trend
from app.price_action.schemas import LevelType, PatternDirection
from app.regime.schemas import MarketRegime
from app.strategies.base import BaseStrategy, Features, check, side
from app.strategies.config import TrendContinuationConfig
from app.strategies.context import StrategyContext
from app.strategies.schemas import StrategyCheck, StrategyName, TradeDirection

_SELF_MADE = {
    LevelType.SWING_LOW,
    LevelType.SWING_HIGH,
    LevelType.INTRADAY_LOW,
    LevelType.INTRADAY_HIGH,
    LevelType.RANGE_LOW,
    LevelType.RANGE_HIGH,
}


def _to_trend(direction: TradeDirection) -> Trend:
    return {TradeDirection.LONG: Trend.UP, TradeDirection.SHORT: Trend.DOWN}.get(
        direction, Trend.SIDEWAYS
    )


class TrendContinuationStrategy(BaseStrategy):
    name = StrategyName.TREND_CONTINUATION
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("TREND", "trend"),
        ("IMPULSE", "impulse"),
        ("PULLBACK", "pullback"),
        ("SUPPORT", "support"),
        ("CONFIRMATION", "confirmation"),
    )

    def __init__(self, config: TrendContinuationConfig | None = None) -> None:
        self.config = config or TrendContinuationConfig()
        super().__init__(self.config.geometry, self.config.weights)

    # --- analyze ------------------------------------------------------------------

    def analyze(self, ctx: StrategyContext) -> Features:
        a, pa = ctx.analysis, ctx.price_action
        mtf = pa.multi_timeframe
        f: Features = {
            "regime": ctx.regime.regime.value,
            "regime_confidence": ctx.regime.confidence,
            "atr": round(ctx.atr, 4),
            "price": ctx.price,
        }

        if ctx.regime.regime == MarketRegime.STRONG_BULLISH:
            direction, source = TradeDirection.LONG, "regime"
        elif ctx.regime.regime == MarketRegime.STRONG_BEARISH:
            direction, source = TradeDirection.SHORT, "regime"
        elif (
            mtf is not None
            and mtf.context_analysis.available
            and mtf.context_analysis.direction != Trend.SIDEWAYS
        ):
            direction = (
                TradeDirection.LONG
                if mtf.context_analysis.direction == Trend.UP
                else TradeDirection.SHORT
            )
            source = "higher timeframe"
        else:
            direction, source = TradeDirection.NONE, "none"
        f["trend_direction"] = direction.value
        f["trend_source"] = source

        if mtf is not None:
            f["context_direction"] = mtf.context_analysis.direction.value
            f["context_bias"] = mtf.context_analysis.bias
            f["alignment_score"] = mtf.alignment_score
            confirmation = mtf.entry_analysis.confirmation
            f["momentum_returning"] = bool(confirmation and confirmation.momentum_returning)
        f["structure_trend"] = a.structure.trend.value if a.structure else None
        f["price_vs_vwap"] = a.vwap.price_vs_vwap.value if a.vwap else None
        f["vwap"] = a.vwap.vwap if a.vwap else None
        f["trend_quality"] = a.trend_quality.score if a.trend_quality else None
        f["trend_quality_direction"] = a.trend_quality.direction.value if a.trend_quality else None
        f["momentum_score"] = a.momentum.momentum_score if a.momentum else None
        f["relative_volume"] = a.volume.relative_volume if a.volume else None
        f["relative_strength"] = (
            a.relative_strength.relative_strength_score if a.relative_strength else None
        )
        f["relative_strength_rank"] = ctx.relative_strength_rank

        r = pa.retracement
        f["impulse_detected"] = bool(r and r.detected)
        if r is not None and r.detected and r.impulse_end is not None and r.impulse_size:
            f["impulse_direction"] = r.direction.value if r.direction else None
            f["impulse_size_atr"] = r.impulse_size_atr
            f["impulse_end"] = r.impulse_end
            f["retracement_percent"] = r.retracement_percentage
            f["max_retracement_percent"] = r.max_retracement_percentage
            f["retracement_zone"] = r.zone.value if r.zone else None
            f["candles_since_extreme"] = r.candles_since_extreme
            deepest = (r.max_retracement_percentage or 0.0) / 100.0 * r.impulse_size
            f["pullback_extreme"] = round(
                r.impulse_end - deepest if r.direction == Trend.UP else r.impulse_end + deepest, 4
            )

        sign = side(direction)
        aligned = [
            p.pattern.value
            for p in pa.patterns
            if p.detected
            and p.pattern.value in self.config.confirmation_patterns
            and (
                (p.direction == PatternDirection.BULLISH and sign > 0)
                or (p.direction == PatternDirection.BEARISH and sign < 0)
            )
        ]
        f["confirmation_patterns"] = ", ".join(aligned) or None
        latest = ctx.latest
        f["latest_closes_with_trend"] = (latest.close - latest.open) * sign > 0

        support = self._support_near_pullback(ctx, direction, f)
        f["support_levels"] = ", ".join(support) or None
        return f

    def _support_near_pullback(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> list[str]:
        """Support (resistance for shorts) near the pullback extreme. Levels
        the pullback itself created (its own swing / day / range extreme)
        and retracement levels (always at the pullback by construction)
        do not count: they would make the check true by definition."""

        sr = ctx.price_action.support_resistance
        extreme = f.get("pullback_extreme")
        if sr is None or not isinstance(extreme, float) or direction == TradeDirection.NONE:
            return []
        tolerance = self.config.support_proximity_atr * ctx.atr
        own = 0.05 * ctx.atr
        found = []
        for level in sr.levels:
            if level.type == LevelType.RETRACEMENT:
                continue
            if level.type in _SELF_MADE and abs(level.price - extreme) <= own:
                continue
            if abs(level.price - extreme) <= tolerance:
                found.append(level.label)
        return sorted(set(found))

    # --- validate -----------------------------------------------------------------

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        direction = TradeDirection(f["trend_direction"])
        trend = _to_trend(direction)
        sign = side(direction)
        checks: list[StrategyCheck] = []

        checks.append(
            check(
                "trend",
                "Trend",
                direction != TradeDirection.NONE,
                (
                    f"{trend.value.lower()} from {f['trend_source']}"
                    if direction != TradeDirection.NONE
                    else "no directional regime or higher-timeframe trend"
                ),
                required=True,
            )
        )

        impulse_ok = (
            bool(f.get("impulse_detected"))
            and f.get("impulse_direction") == trend.value
            and int(f.get("candles_since_extreme") or 0) <= cfg.max_candles_since_extreme
        )
        if not f.get("impulse_detected"):
            impulse_detail = "no significant impulse"
        elif f.get("impulse_direction") != trend.value:
            impulse_detail = (
                f"latest impulse is {str(f.get('impulse_direction')).lower()}, against the trend"
            )
        elif not impulse_ok:
            impulse_detail = f"impulse extreme is {f.get('candles_since_extreme')} candles old"
        else:
            impulse_detail = f"{f.get('impulse_size_atr')} ATR impulse with the trend"
        checks.append(check("impulse", "Impulse", impulse_ok, impulse_detail, required=True))

        deepest = f.get("max_retracement_percent")
        current = f.get("retracement_percent")
        pullback_ok = (
            isinstance(deepest, float)
            and isinstance(current, float)
            and cfg.min_pullback_percent <= deepest <= cfg.max_pullback_percent
        )
        pullback_detail = (
            f"pulled back {deepest:.0f}% (healthy range {cfg.min_pullback_percent:g}-"
            f"{cfg.max_pullback_percent:g}%), now {current:.0f}%"
            if isinstance(deepest, float) and isinstance(current, float)
            else "no pullback to measure"
        )
        checks.append(check("pullback", "Pullback", pullback_ok, pullback_detail, required=True))

        support = f.get("support_levels")
        checks.append(
            check(
                "support",
                "Support",
                bool(support),
                (
                    f"pullback held near {support}"
                    if support
                    else "no independent level under the pullback"
                ),
                weight=w["support"],
            )
        )

        patterns = f.get("confirmation_patterns")
        momentum_back = bool(f.get("momentum_returning")) and bool(
            f.get("latest_closes_with_trend")
        )
        if patterns:
            confirm_detail = f"{patterns} on the latest candle"
        elif momentum_back:
            confirm_detail = "momentum returning with a candle closing in the trend direction"
        else:
            confirm_detail = "no pattern or momentum turn in the trend direction yet"
        checks.append(
            check(
                "confirmation",
                "Confirmation",
                bool(patterns) or momentum_back,
                confirm_detail,
                required=True,
            )
        )

        if direction == TradeDirection.NONE:
            return direction, checks

        regime_ok = (sign > 0 and f["regime"] == MarketRegime.STRONG_BULLISH.value) or (
            sign < 0 and f["regime"] == MarketRegime.STRONG_BEARISH.value
        )
        checks.append(
            check(
                "regime",
                "Regime",
                regime_ok,
                f"{f['regime']} ({f['regime_confidence']:.0f})",
                weight=w["regime"],
            )
        )
        alignment = f.get("alignment_score")
        htf_ok = (
            f.get("context_direction") == trend.value
            and isinstance(alignment, float)
            and alignment >= cfg.min_alignment
        )
        checks.append(
            check(
                "higher_timeframe",
                "Higher timeframe",
                htf_ok,
                f"context {str(f.get('context_direction')).lower()}, alignment {alignment}",
                weight=w["higher_timeframe"],
            )
        )
        checks.append(
            check(
                "structure",
                "Market structure",
                f.get("structure_trend") == trend.value,
                f"swing structure {str(f.get('structure_trend')).lower()}",
                weight=w["structure"],
            )
        )
        wanted = Relationship.ABOVE.value if sign > 0 else Relationship.BELOW.value
        checks.append(
            check(
                "vwap",
                "VWAP",
                f.get("price_vs_vwap") == wanted,
                f"price {str(f.get('price_vs_vwap')).lower()} VWAP",
                weight=w["vwap"],
            )
        )
        quality = f.get("trend_quality")
        checks.append(
            check(
                "trend_quality",
                "Trend quality",
                isinstance(quality, float)
                and quality >= cfg.min_trend_quality
                and f.get("trend_quality_direction") == trend.value,
                f"{quality} ({str(f.get('trend_quality_direction')).lower()})",
                weight=w["trend_quality"],
            )
        )
        rv = f.get("relative_volume")
        checks.append(
            check(
                "volume",
                "Volume",
                isinstance(rv, float) and rv >= 1.0,
                f"latest candle {rv}x average volume",
                weight=w["volume"],
            )
        )
        momentum = f.get("momentum_score")
        momentum_ok = isinstance(momentum, float) and (
            momentum >= 55 if sign > 0 else momentum <= 45
        )
        checks.append(
            check("momentum", "Momentum", momentum_ok, f"score {momentum}", weight=w["momentum"])
        )
        rs = f.get("relative_strength")
        rs_ok = isinstance(rs, float) and (
            rs >= cfg.min_relative_strength if sign > 0 else rs <= 100 - cfg.min_relative_strength
        )
        checks.append(
            check(
                "relative_strength",
                "Relative strength",
                rs_ok,
                (
                    f"score {rs} vs NIFTY 50"
                    if rs is not None
                    else "not available (benchmark or index)"
                ),
                weight=w["relative_strength"],
            )
        )
        checks.append(
            check(
                "pattern",
                "Price action",
                bool(patterns),
                str(patterns) if patterns else "no aligned pattern",
                weight=w["pattern"],
            )
        )
        return direction, checks

    # --- candidates ---------------------------------------------------------------

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        if direction == TradeDirection.LONG:
            return ctx.latest.high, "above the confirmation candle's high"
        return ctx.latest.low, "below the confirmation candle's low"

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        extreme = float(f["pullback_extreme"])  # type: ignore[arg-type]
        buffer = self.geometry.stop_buffer_atr * ctx.atr
        if direction == TradeDirection.LONG:
            return extreme - buffer, "below the pullback low"
        return extreme + buffer, "above the pullback high"
