"""Strategy D: double moving-average crossover (PDF: "Double Moving
Average Crossover").

Concept: buy when the short-term average crosses above the long-term one,
sell when it crosses below; confirm trend strength with ADX; beware of
false signals in choppy markets.

Required (all must hold):
- crossover:  the fast MA crossed the slow MA on a CLOSED candle within the
              last `max_bars_since_cross` candles and is still on that side
              (a crossover on the forming candle is never used)
- price_side: the latest close is on the trade's side of the slow MA
- adx:        ADX >= `min_adx` (when `use_adx`) with +DI > -DI for longs,
              -DI > +DI for shorts
- not_choppy: at most `max_crosses_in_window` crossovers in the last
              `choppy_window` candles

Entry: MARKET after the confirming candle closed. Stop: beyond the
extreme of the last `swing_lookback` candles + `stop_buffer_atr`.
Target: structure level offering the minimum reward:risk, else the
default R. Invalidation / exit signal: an opposite crossover (the signal
then disappears; open positions exit on stop, target or square-off).
"""

from __future__ import annotations

from typing import ClassVar

from app.market_intelligence.schemas import Relationship
from app.strategies.base import BaseStrategy, Features, check
from app.strategies.context import StrategyContext
from app.strategies.indicators import adx_series, moving_average
from app.strategies.pdf_config import MovingAverageCrossoverConfig
from app.strategies.schemas import StrategyCheck, StrategyName, TradeDirection


class MovingAverageCrossoverStrategy(BaseStrategy):
    name = StrategyName.MA_CROSSOVER
    version = "1.0.0"
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("CROSSOVER", "crossover"),
        ("TREND_STRENGTH", "adx"),
        ("NOT_CHOPPY", "not_choppy"),
    )

    def __init__(self, config: MovingAverageCrossoverConfig | None = None) -> None:
        self.config = config or MovingAverageCrossoverConfig()
        super().__init__(self.config.geometry, self.config.weights)

    def analyze(self, ctx: StrategyContext) -> Features:
        cfg = self.config
        candles = ctx.candles
        closes = [c.close for c in candles]
        fast = moving_average(closes, cfg.fast_period, cfg.ma_type)
        slow = moving_average(closes, cfg.slow_period, cfg.ma_type)
        f: Features = {"atr": round(ctx.atr, 4), "price": ctx.price}
        signs: list[int] = []
        for a, b in zip(fast, slow, strict=True):
            signs.append(0 if a is None or b is None else (1 if a > b else (-1 if a < b else 0)))
        crosses = [
            i for i in range(1, len(signs)) if signs[i] != 0 and signs[i - 1] not in (0, signs[i])
        ]
        f["fast_ma"] = round(fast[-1], 4) if fast[-1] is not None else None
        f["slow_ma"] = round(slow[-1], 4) if slow[-1] is not None else None
        f["ma_side"] = {1: "ABOVE", -1: "BELOW"}.get(signs[-1] if signs else 0, "NONE")
        last = crosses[-1] if crosses else None
        f["bars_since_cross"] = len(candles) - 1 - last if last is not None else None
        f["cross_direction"] = ("UP" if signs[last] > 0 else "DOWN") if last is not None else None
        start = len(candles) - cfg.choppy_window
        f["crosses_in_window"] = sum(1 for i in crosses if i >= start)
        adx, pdi, mdi = adx_series(candles, cfg.adx_period)
        f["adx"] = round(adx[-1], 2) if adx[-1] is not None else None
        f["plus_di"] = round(pdi[-1], 2) if pdi[-1] is not None else None
        f["minus_di"] = round(mdi[-1], 2) if mdi[-1] is not None else None
        swing = candles[-cfg.swing_lookback :]
        f["swing_low"] = min(c.low for c in swing)
        f["swing_high"] = max(c.high for c in swing)
        f["price_vs_vwap"] = ctx.analysis.vwap.price_vs_vwap.value if ctx.analysis.vwap else None
        f["relative_volume"] = ctx.analysis.volume.relative_volume if ctx.analysis.volume else None
        f["market_regime"] = ctx.regime.regime.value
        return f

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        bars = f.get("bars_since_cross")
        cross = f.get("cross_direction")
        side_now = f.get("ma_side")
        fresh = isinstance(bars, int) and bars <= cfg.max_bars_since_cross
        direction = TradeDirection.NONE
        if fresh and cross == "UP" and side_now == "ABOVE":
            direction = TradeDirection.LONG
        elif fresh and cross == "DOWN" and side_now == "BELOW":
            direction = TradeDirection.SHORT
        checks = [
            check(
                "crossover",
                "Confirmed crossover",
                direction != TradeDirection.NONE,
                (
                    f"fast {cfg.ma_type}{cfg.fast_period} crossed {str(cross).lower()} through "
                    f"{cfg.ma_type}{cfg.slow_period} {bars} candle(s) ago"
                    if cross
                    else "no crossover"
                ),
                required=True,
            )
        ]
        price = float(f["price"])  # type: ignore[arg-type]
        slow = f.get("slow_ma")
        checks.append(
            check(
                "price_side",
                "Price beyond the slow MA",
                isinstance(slow, float)
                and ((price > slow) if direction == TradeDirection.LONG else (price < slow)),
                f"close {price} vs slow MA {slow}",
                required=True,
            )
        )
        adx, pdi, mdi = f.get("adx"), f.get("plus_di"), f.get("minus_di")
        di_ok = (
            isinstance(pdi, float)
            and isinstance(mdi, float)
            and (pdi > mdi if direction == TradeDirection.LONG else mdi > pdi)
        )
        adx_ok = isinstance(adx, float) and adx >= cfg.min_adx and di_ok
        checks.append(
            check(
                "adx",
                "Trend strength (ADX)",
                adx_ok or not cfg.use_adx,
                f"ADX {adx} (min {cfg.min_adx:g}), +DI {pdi} / -DI {mdi}",
                required=cfg.use_adx,
            )
        )
        crosses = int(f.get("crosses_in_window") or 0)
        checks.append(
            check(
                "not_choppy",
                "Not choppy",
                crosses <= cfg.max_crosses_in_window,
                f"{crosses} crossovers in {cfg.choppy_window} candles (max {cfg.max_crosses_in_window})",
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
        regime = f.get("market_regime")
        checks.append(
            check(
                "market",
                "Regime agrees",
                (direction == TradeDirection.LONG and regime == "STRONG_BULLISH")
                or (direction == TradeDirection.SHORT and regime == "STRONG_BEARISH"),
                f"regime {regime}",
                weight=w["market"],
            )
        )
        rv = f.get("relative_volume")
        checks.append(
            check(
                "volume",
                "Volume",
                isinstance(rv, float) and rv >= 1.0,
                f"{rv}x average",
                weight=w["volume"],
            )
        )
        checks.append(
            check("di", "Directional index", di_ok, f"+DI {pdi} / -DI {mdi}", weight=w["di"])
        )
        return direction, checks

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        return ctx.price, "next open after the confirming candle closed"

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        buffer = self.config.stop_buffer_atr * ctx.atr
        n = self.config.swing_lookback
        if direction == TradeDirection.LONG:
            return float(f["swing_low"]) - buffer, f"below the {n}-candle low"  # type: ignore[arg-type]
        return float(f["swing_high"]) + buffer, f"above the {n}-candle high"  # type: ignore[arg-type]
