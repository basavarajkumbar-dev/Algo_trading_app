"""Strategy C: volume-based trading (PDF: "Volume-Based Trading").

Concept: rising volume suggests strong buying or selling interest and a
likely continuation; price entering a demand/supply zone on high volume
suggests the zone will be respected (a reversal). The PDF says to combine
volume with moving averages or pivots. High volume alone is never a
signal here: every setup also needs a price-action condition and either
a trend (continuation) or a level (reversal).

Relative volume = the latest closed candle's volume / its baseline:
the same time of day over the previous `baseline_sessions` sessions
(default; intraday volume is U-shaped) or the previous
`baseline_lookback` candles.

Continuation (required): volume >= `min_relative_volume`; EMA fast/slow
trend and a strong candle (body >= `min_body_fraction`) in the trend
direction, closing beyond the fast EMA.
Reversal (required): volume >= `min_relative_volume`; price at a support
(longs) / resistance (shorts) level of strength >= `min_level_strength`
within `zone_tolerance_atr`; a rejection candle (wick >= `min_wick_fraction`
of the range on the level's side) closing away from the level.

Entry: STOP beyond the signal candle (`trigger_valid_bars`). Stop: beyond
the signal candle's other extreme + `stop_buffer_atr`. Target: nearest
structure level offering the minimum reward:risk, else the default R.
Invalidation: a close beyond the signal candle's far extreme.
"""

from __future__ import annotations

from typing import ClassVar

from app.market_intelligence.schemas import Relationship
from app.price_action.support_resistance import LevelRole
from app.strategies.base import BaseStrategy, Features, check
from app.strategies.context import StrategyContext
from app.strategies.indicators import body_fraction, moving_average, relative_volume
from app.strategies.pdf_config import VolumeTradingConfig
from app.strategies.schemas import EntryType, StrategyCheck, StrategyName, TradeDirection


class VolumeTradingStrategy(BaseStrategy):
    name = StrategyName.VOLUME_TRADING
    version = "1.0.0"
    entry_type = EntryType.STOP
    entry_valid_bars = 2
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("VOLUME", "volume"),
        ("SETUP", "setup"),
        ("CONFIRMATION", "price_action"),
    )

    def __init__(self, config: VolumeTradingConfig | None = None) -> None:
        self.config = config or VolumeTradingConfig()
        super().__init__(self.config.geometry, self.config.weights)

    def analyze(self, ctx: StrategyContext) -> Features:
        cfg = self.config
        candles = ctx.candles
        latest = ctx.latest
        f: Features = {"atr": round(ctx.atr, 4), "price": ctx.price}
        rv, basis = relative_volume(
            candles, cfg.baseline_lookback, mode=cfg.baseline, sessions=cfg.baseline_sessions
        )
        f["relative_volume"] = round(rv, 2) if rv is not None else None
        f["volume_baseline"] = basis
        closes = [c.close for c in candles]
        fast = moving_average(closes, cfg.trend_fast, "EMA")[-1]
        slow = moving_average(closes, cfg.trend_slow, "EMA")[-1]
        f["ema_fast"] = round(fast, 4) if fast is not None else None
        f["ema_slow"] = round(slow, 4) if slow is not None else None
        if fast is not None and slow is not None:
            f["trend"] = "UP" if fast > slow else ("DOWN" if fast < slow else "FLAT")
        else:
            f["trend"] = None
        rng = latest.high - latest.low
        f["candle_direction"] = (
            "UP"
            if latest.close > latest.open
            else ("DOWN" if latest.close < latest.open else "FLAT")
        )
        f["body"] = round(body_fraction(latest), 2)
        f["lower_wick"] = (
            round((min(latest.open, latest.close) - latest.low) / rng, 2) if rng else 0.0
        )
        f["upper_wick"] = (
            round((latest.high - max(latest.open, latest.close)) / rng, 2) if rng else 0.0
        )
        f["candle_high"] = latest.high
        f["candle_low"] = latest.low
        # nearest strong support / resistance to the candle's extremes
        sr = ctx.price_action.support_resistance
        tol = cfg.zone_tolerance_atr * ctx.atr
        support = resistance = None
        for lvl in sr.levels if sr is not None else []:
            if lvl.dynamic or lvl.strength < cfg.min_level_strength:
                continue
            if lvl.role in (LevelRole.SUPPORT,) and abs(latest.low - lvl.price) <= tol:
                support = support or lvl.label
            if lvl.role in (LevelRole.RESISTANCE,) and abs(latest.high - lvl.price) <= tol:
                resistance = resistance or lvl.label
        f["support_zone"] = support
        f["resistance_zone"] = resistance
        f["price_vs_vwap"] = ctx.analysis.vwap.price_vs_vwap.value if ctx.analysis.vwap else None
        f["momentum_score"] = (
            ctx.analysis.momentum.momentum_score if ctx.analysis.momentum else None
        )
        return f

    def _setup(self, f: Features) -> tuple[TradeDirection, str]:
        cfg = self.config
        fast = f.get("ema_fast")
        price = float(f["price"])  # type: ignore[arg-type]
        strong = float(f.get("body") or 0.0) >= cfg.min_body_fraction
        if cfg.enable_continuation and isinstance(fast, float) and strong:
            if f.get("trend") == "UP" and f.get("candle_direction") == "UP" and price > fast:
                return TradeDirection.LONG, "continuation"
            if f.get("trend") == "DOWN" and f.get("candle_direction") == "DOWN" and price < fast:
                return TradeDirection.SHORT, "continuation"
        if cfg.enable_reversal:
            if (
                f.get("support_zone")
                and float(f.get("lower_wick") or 0.0) >= cfg.min_wick_fraction
                and f.get("candle_direction") != "DOWN"
            ):
                return TradeDirection.LONG, "reversal"
            if (
                f.get("resistance_zone")
                and float(f.get("upper_wick") or 0.0) >= cfg.min_wick_fraction
                and f.get("candle_direction") != "UP"
            ):
                return TradeDirection.SHORT, "reversal"
        return TradeDirection.NONE, "none"

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        rv = f.get("relative_volume")
        checks = [
            check(
                "volume",
                "Volume expansion",
                isinstance(rv, float) and rv >= cfg.min_relative_volume,
                f"{rv}x ({f.get('volume_baseline')}), need {cfg.min_relative_volume:g}x",
                required=True,
            )
        ]
        direction, kind = self._setup(f)
        f["setup_kind"] = kind
        checks.append(
            check(
                "setup",
                "Trend or level",
                direction != TradeDirection.NONE,
                {
                    "continuation": f"{str(f.get('trend')).lower()} trend (EMA {cfg.trend_fast}/{cfg.trend_slow})",
                    "reversal": f"at {f.get('support_zone') or f.get('resistance_zone')}",
                }.get(kind, "no trend-aligned candle and no tested level"),
                required=True,
            )
        )
        checks.append(
            check(
                "price_action",
                "Price action",
                direction != TradeDirection.NONE,
                (
                    f"strong {str(f.get('candle_direction')).lower()} candle (body {f.get('body')})"
                    if kind == "continuation"
                    else f"rejection wick {f.get('lower_wick') if direction == TradeDirection.LONG else f.get('upper_wick')}"
                ),
                required=True,
            )
        )
        if direction == TradeDirection.NONE:
            return direction, checks
        wanted = (
            Relationship.ABOVE.value
            if direction == TradeDirection.LONG
            else Relationship.BELOW.value
        )
        checks.append(
            check(
                "vwap",
                "VWAP side",
                f.get("price_vs_vwap") == wanted,
                f"price {str(f.get('price_vs_vwap')).lower()} VWAP",
                weight=w["vwap"],
            )
        )
        m = f.get("momentum_score")
        checks.append(
            check(
                "momentum",
                "Momentum",
                isinstance(m, float) and (m >= 55 if direction == TradeDirection.LONG else m <= 45),
                f"score {m}",
                weight=w["momentum"],
            )
        )
        checks.append(
            check(
                "level",
                "Level confluence",
                bool(
                    f.get("support_zone")
                    if direction == TradeDirection.LONG
                    else f.get("resistance_zone")
                ),
                str(f.get("support_zone") or f.get("resistance_zone") or "none"),
                weight=w["level"],
            )
        )
        regime = ctx.regime.regime.value
        checks.append(
            check(
                "market",
                "Regime agrees",
                (direction == TradeDirection.LONG and regime == "STRONG_BULLISH")
                or (direction == TradeDirection.SHORT and regime == "STRONG_BEARISH")
                or kind == "reversal",
                f"regime {regime}",
                weight=w["market"],
            )
        )
        return direction, checks

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        if direction == TradeDirection.LONG:
            return float(f["candle_high"]), "trigger: a trade above the volume candle's high"  # type: ignore[arg-type]
        return float(f["candle_low"]), "trigger: a trade below the volume candle's low"  # type: ignore[arg-type]

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        buffer = self.config.stop_buffer_atr * ctx.atr
        if direction == TradeDirection.LONG:
            return float(f["candle_low"]) - buffer, "below the volume candle's low"  # type: ignore[arg-type]
        return float(f["candle_high"]) + buffer, "above the volume candle's high"  # type: ignore[arg-type]
