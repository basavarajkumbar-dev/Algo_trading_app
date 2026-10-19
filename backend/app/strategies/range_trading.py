"""Strategy A: range trading (PDF: "Range Trading").

Concept: in a sideways market, buy near support and sell near resistance;
RSI oversold/overbought near the boundary adds confirmation; trade inside
the range until a key level breaks.

Required (all must hold):
- range:       the last `lookback` closed candles (excluding the latest)
               form a range `min_width_atr`..`max_width_atr` wide whose
               high and low were each visited `min_touches` times
- not_trending: ADX(14) <= `max_adx` (the PDF: works when there is no
               clear trend) and the regime is not STRONG_BULLISH/BEARISH
- intact:      the latest candle closed inside the range (a close beyond a
               boundary invalidates the range)
- at_boundary: the latest close is in the outer `boundary_zone` of the
               range on the trade's side (bottom for longs, top for shorts)
- rsi:         RSI <= oversold for longs, >= overbought for shorts
               (when `use_rsi`)
- reversal:    the latest candle closed in the trade's direction

Entry: MARKET after the reversal candle has closed. Stop: beyond the range
boundary plus `stop_buffer_atr`. Target: `target_fraction` of the way to
the opposite boundary. Invalidation: a close beyond either boundary.
"""

from __future__ import annotations

from typing import ClassVar

from app.market_intelligence.schemas import Relationship
from app.regime.schemas import MarketRegime
from app.strategies.base import BaseStrategy, Features, check, side
from app.strategies.context import StrategyContext
from app.strategies.indicators import adx_series, rsi_series
from app.strategies.pdf_config import RangeTradingConfig
from app.strategies.schemas import StrategyCheck, StrategyName, TradeDirection


class RangeTradingStrategy(BaseStrategy):
    name = StrategyName.RANGE_TRADING
    version = "1.0.0"
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("RANGE", "range"),
        ("NOT_TRENDING", "not_trending"),
        ("AT_BOUNDARY", "at_boundary"),
        ("CONFIRMATION", "reversal"),
    )

    def __init__(self, config: RangeTradingConfig | None = None) -> None:
        self.config = config or RangeTradingConfig()
        super().__init__(self.config.geometry, self.config.weights)

    def analyze(self, ctx: StrategyContext) -> Features:
        cfg = self.config
        candles = ctx.candles
        f: Features = {
            "atr": round(ctx.atr, 4),
            "price": ctx.price,
            "regime": ctx.regime.regime.value,
        }
        window = candles[-cfg.lookback - 1 : -1]
        if len(window) < cfg.lookback or ctx.atr <= 0:
            f["range_found"] = False
            return f
        high = max(c.high for c in window)
        low = min(c.low for c in window)
        tol = cfg.touch_tolerance_atr * ctx.atr
        f["range_found"] = True
        f["range_high"] = high
        f["range_low"] = low
        f["range_width_atr"] = round((high - low) / ctx.atr, 2)
        f["touches_high"] = sum(1 for c in window if c.high >= high - tol)
        f["touches_low"] = sum(1 for c in window if c.low <= low + tol)
        latest = ctx.latest
        f["inside"] = low <= latest.close <= high
        f["position_in_range"] = (
            round((latest.close - low) / (high - low), 3) if high > low else None
        )
        f["candle_direction"] = (
            "UP"
            if latest.close > latest.open
            else ("DOWN" if latest.close < latest.open else "FLAT")
        )
        rsi = rsi_series([c.close for c in candles], cfg.rsi_period)[-1]
        f["rsi"] = round(rsi, 2) if rsi is not None else None
        adx, _, _ = adx_series(candles, cfg.adx_period)
        f["adx"] = round(adx[-1], 2) if adx[-1] is not None else None
        f["price_vs_vwap"] = ctx.analysis.vwap.price_vs_vwap.value if ctx.analysis.vwap else None
        f["relative_volume"] = ctx.analysis.volume.relative_volume if ctx.analysis.volume else None
        return f

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        checks: list[StrategyCheck] = []
        found = bool(f.get("range_found"))
        width = f.get("range_width_atr")
        th, tl = int(f.get("touches_high") or 0), int(f.get("touches_low") or 0)
        range_ok = (
            found
            and isinstance(width, float)
            and cfg.min_width_atr <= width <= cfg.max_width_atr
            and th >= cfg.min_touches
            and tl >= cfg.min_touches
        )
        checks.append(
            check(
                "range",
                "Range",
                range_ok,
                (
                    f"{width} ATR range over {cfg.lookback} candles, {th} touches high / {tl} low"
                    if found
                    else "not enough candles"
                ),
                required=True,
            )
        )
        adx = f.get("adx")
        trending_regime = f.get("regime") in (
            MarketRegime.STRONG_BULLISH.value,
            MarketRegime.STRONG_BEARISH.value,
        )
        calm = isinstance(adx, float) and adx <= cfg.max_adx and not trending_regime
        checks.append(
            check(
                "not_trending",
                "Not trending",
                calm,
                f"ADX {adx} (max {cfg.max_adx:g}), regime {f.get('regime')}",
                required=True,
            )
        )
        checks.append(
            check(
                "intact",
                "Range intact",
                bool(f.get("inside")),
                "latest close inside the range" if f.get("inside") else "closed beyond a boundary",
                required=True,
            )
        )
        pos = f.get("position_in_range")
        direction = TradeDirection.NONE
        if isinstance(pos, float):
            if pos <= cfg.boundary_zone:
                direction = TradeDirection.LONG
            elif pos >= 1 - cfg.boundary_zone:
                direction = TradeDirection.SHORT
        checks.append(
            check(
                "at_boundary",
                "At a boundary",
                direction != TradeDirection.NONE,
                f"close at {pos} of the range (outer {cfg.boundary_zone:.0%} needed)",
                required=True,
            )
        )
        rsi = f.get("rsi")
        rsi_ok = isinstance(rsi, float) and (
            (direction == TradeDirection.LONG and rsi <= cfg.rsi_oversold)
            or (direction == TradeDirection.SHORT and rsi >= cfg.rsi_overbought)
        )
        checks.append(
            check(
                "rsi",
                "RSI",
                rsi_ok or not cfg.use_rsi,
                f"RSI {rsi} (long <= {cfg.rsi_oversold:g}, short >= {cfg.rsi_overbought:g})",
                required=cfg.use_rsi,
                weight=0.0 if cfg.use_rsi else w["rsi"],
            )
        )
        wanted = {"LONG": "UP", "SHORT": "DOWN"}.get(direction.value)
        reversal = wanted is not None and f.get("candle_direction") == wanted
        checks.append(
            check(
                "reversal",
                "Reversal candle",
                reversal,
                f"latest candle {str(f.get('candle_direction')).lower()}",
                required=True,
            )
        )
        if direction == TradeDirection.NONE:
            return direction, checks
        checks.append(
            check(
                "touches",
                "Well-tested boundary",
                (tl if direction == TradeDirection.LONG else th) >= cfg.min_touches + 1,
                f"{tl if direction == TradeDirection.LONG else th} touches",
                weight=w["touches"],
            )
        )
        # a fade wants price stretched away from VWAP on the trade's far side
        fade_side = (
            Relationship.BELOW.value
            if direction == TradeDirection.LONG
            else Relationship.ABOVE.value
        )
        checks.append(
            check(
                "vwap",
                "VWAP",
                f.get("price_vs_vwap") == fade_side,
                f"price {str(f.get('price_vs_vwap')).lower()} VWAP",
                weight=w["vwap"],
            )
        )
        rv = f.get("relative_volume")
        checks.append(
            check(
                "volume",
                "Quiet volume",
                isinstance(rv, float) and rv <= 1.5,
                f"{rv}x average (a surge may mean a breakout)",
                weight=w["volume"],
            )
        )
        return direction, checks

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        return ctx.price, "next open after the reversal candle closed"

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        buffer = self.config.stop_buffer_atr * ctx.atr
        if direction == TradeDirection.LONG:
            return float(f["range_low"]) - buffer, "below the range low"  # type: ignore[arg-type]
        return float(f["range_high"]) + buffer, "above the range high"  # type: ignore[arg-type]

    def calculate_target(
        self,
        ctx: StrategyContext,
        direction: TradeDirection,
        entry: float,
        stop: float,
        f: Features,
    ) -> tuple[float, str]:
        high, low = float(f["range_high"]), float(f["range_low"])  # type: ignore[arg-type]
        far = high if direction == TradeDirection.LONG else low
        target = entry + side(direction) * abs(far - entry) * self.config.target_fraction
        return target, f"{self.config.target_fraction:.0%} of the way to the far boundary"
