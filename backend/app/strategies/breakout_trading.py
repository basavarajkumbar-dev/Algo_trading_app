"""Strategy B: breakout trading with an explicit trigger (PDF: "Breakout
Trading").

Concept: identify a key level (a recent range high/low, or the day's
high/low), wait for price to break it, confirm the break with volume and a
strong candle (or a retest), and keep the stop close to the level.

This is a separate, versioned module. The existing Breakout Momentum
strategy is unchanged; the difference here is that a nearby level is
never enough: a candle must have CLOSED beyond the level with volume and
strength, and the order then waits for price to trade through that
confirming candle (a STOP entry, `trigger_valid_bars` candles), so no fill
happens before the trigger.

Required (all must hold):
- level:      a level from the `level_lookback` candles before the break
              (range no wider than `max_level_width_atr`), or the day's
              high/low so far
- breakout:   a close beyond the level by >= `min_close_beyond_atr` within
              the last `max_bars_since_breakout` candles
- holding:    the latest close is still beyond the level (back inside =
              breakout failure, invalid)
- volume:     the breakout candle's relative volume >= `min_relative_volume`
- strong:     the breakout candle's body >= `min_body_fraction` of its range
              and it closed near its extreme (`min_close_location`)
- retest:     (only if `require_retest`) a later candle came back to the
              level and closed beyond it again
- not_extended: the latest close is <= `max_extension_atr` beyond the level

Entry: STOP beyond the confirming candle (breakout or retest candle).
Stop: beyond the level / confirming candle extreme + `stop_buffer_atr`.
Target: the measured move (range height), at least the geometry minimum.
Invalidation / breakout failure: a close back inside the level.
"""

from __future__ import annotations

from typing import ClassVar

from app.market_intelligence.schemas import Relationship, Trend
from app.strategies.base import BaseStrategy, Features, check, side
from app.strategies.context import StrategyContext
from app.strategies.indicators import IST, body_fraction, close_location, relative_volume
from app.strategies.pdf_config import BreakoutTradingConfig
from app.strategies.schemas import EntryType, StrategyCheck, StrategyName, TradeDirection


class BreakoutTradingStrategy(BaseStrategy):
    name = StrategyName.BREAKOUT_TRADING
    version = "1.0.0"
    entry_type = EntryType.STOP
    entry_valid_bars = 2
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("LEVEL", "level"),
        ("BREAKOUT", "breakout"),
        ("VOLUME", "volume"),
        ("CONFIRMATION", "strong"),
    )

    def __init__(self, config: BreakoutTradingConfig | None = None) -> None:
        self.config = config or BreakoutTradingConfig()
        super().__init__(self.config.geometry, self.config.weights)

    def analyze(self, ctx: StrategyContext) -> Features:
        cfg = self.config
        candles = ctx.candles
        atr = ctx.atr
        f: Features = {"atr": round(atr, 4), "price": ctx.price, "breakout_found": False}
        n = len(candles)
        if atr <= 0 or n < cfg.level_lookback + cfg.max_bars_since_breakout + 1:
            return f
        today = candles[-1].timestamp.astimezone(IST).date()
        # the most recent qualifying breakout candle within the window
        for lag in range(cfg.max_bars_since_breakout):
            i = n - 1 - lag
            bar = candles[i]
            base = candles[i - cfg.level_lookback : i]
            levels: list[tuple[str, float, float, TradeDirection]] = []
            hi, lo = max(c.high for c in base), min(c.low for c in base)
            if (hi - lo) <= cfg.max_level_width_atr * atr:
                levels.append(("range high", hi, hi - lo, TradeDirection.LONG))
                levels.append(("range low", lo, hi - lo, TradeDirection.SHORT))
            if cfg.use_day_extremes:
                day = [c for c in candles[:i] if c.timestamp.astimezone(IST).date() == today]
                if len(day) >= 3:
                    dh, dl = max(c.high for c in day), min(c.low for c in day)
                    levels.append(("day high", dh, dh - dl, TradeDirection.LONG))
                    levels.append(("day low", dl, dh - dl, TradeDirection.SHORT))
            for label, level, height, direction in levels:
                beyond = (bar.close - level) * side(direction)
                if beyond >= cfg.min_close_beyond_atr * atr:
                    rv, basis = relative_volume(candles[: i + 1], cfg.level_lookback)
                    f.update(
                        {
                            "breakout_found": True,
                            "direction": direction.value,
                            "level_label": label,
                            "level": round(level, 4),
                            "measured_move": round(height, 4),
                            "bars_since_breakout": lag,
                            "breakout_index": i,
                            "breakout_high": bar.high,
                            "breakout_low": bar.low,
                            "breakout_relative_volume": round(rv, 2) if rv is not None else None,
                            "volume_baseline": basis,
                            "breakout_body": round(body_fraction(bar), 2),
                            "breakout_close_location": round(close_location(bar), 2),
                        }
                    )
                    break
            if f["breakout_found"]:
                break
        if not f["breakout_found"]:
            return f
        direction = TradeDirection(str(f["direction"]))
        sign = side(direction)
        level = float(f["level"])  # type: ignore[arg-type]
        latest = ctx.latest
        f["holding"] = (latest.close - level) * sign > 0
        f["extension_atr"] = round((latest.close - level) * sign / atr, 2)
        # retest: after the breakout, a candle touched the level and closed beyond it
        idx = int(f["breakout_index"])  # type: ignore[arg-type]
        tol = cfg.retest_tolerance_atr * atr
        retest_index = None
        for j in range(idx + 1, n):
            c = candles[j]
            touched = (
                (c.low <= level + tol)
                if direction == TradeDirection.LONG
                else (c.high >= level - tol)
            )
            if touched and (c.close - level) * sign > 0:
                retest_index = j
        f["retest"] = retest_index is not None
        confirm = candles[retest_index] if retest_index is not None else candles[idx]
        f["confirm_high"] = confirm.high
        f["confirm_low"] = confirm.low
        f["confirm_candle"] = "retest" if retest_index is not None else "breakout"
        f["price_vs_vwap"] = ctx.analysis.vwap.price_vs_vwap.value if ctx.analysis.vwap else None
        f["momentum_score"] = (
            ctx.analysis.momentum.momentum_score if ctx.analysis.momentum else None
        )
        mtf = ctx.price_action.multi_timeframe
        f["context_direction"] = mtf.context_analysis.direction.value if mtf else None
        return f

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        found = bool(f.get("breakout_found"))
        direction = TradeDirection(str(f["direction"])) if found else TradeDirection.NONE
        checks = [
            check(
                "level",
                "Key level",
                found,
                f"{f.get('level_label')} {f.get('level')}" if found else "no level broken",
                required=True,
            ),
            check(
                "breakout",
                "Close beyond the level",
                found,
                (
                    f"{f.get('bars_since_breakout')} candle(s) ago"
                    if found
                    else "no close beyond a level"
                ),
                required=True,
            ),
        ]
        if not found:
            return direction, checks
        checks.append(
            check(
                "holding",
                "Still beyond the level",
                bool(f.get("holding")),
                "holding" if f.get("holding") else "closed back inside: breakout failed",
                required=True,
            )
        )
        rv = f.get("breakout_relative_volume")
        checks.append(
            check(
                "volume",
                "Breakout volume",
                isinstance(rv, float) and rv >= cfg.min_relative_volume,
                f"{rv}x ({f.get('volume_baseline')}), need {cfg.min_relative_volume:g}x",
                required=True,
            )
        )
        body, loc = f.get("breakout_body"), f.get("breakout_close_location")
        strong_loc = (
            loc
            if direction == TradeDirection.LONG
            else (1 - loc if isinstance(loc, float) else None)
        )
        checks.append(
            check(
                "strong",
                "Strong breakout candle",
                isinstance(body, float)
                and body >= cfg.min_body_fraction
                and isinstance(strong_loc, float)
                and strong_loc >= cfg.min_close_location,
                f"body {body} of range, close location {strong_loc}",
                required=True,
            )
        )
        checks.append(
            check(
                "retest",
                "Retest held",
                bool(f.get("retest")),
                "level retested and held" if f.get("retest") else "no retest yet",
                required=cfg.require_retest,
                weight=0.0 if cfg.require_retest else w["retest"],
            )
        )
        ext = f.get("extension_atr")
        checks.append(
            check(
                "not_extended",
                "Not extended",
                isinstance(ext, float) and ext <= cfg.max_extension_atr,
                f"{ext} ATR beyond the level (max {cfg.max_extension_atr:g})",
                required=True,
            )
        )
        trend = Trend.UP.value if direction == TradeDirection.LONG else Trend.DOWN.value
        checks.append(
            check(
                "context",
                "Higher timeframe",
                f.get("context_direction") in (trend, Trend.SIDEWAYS.value),
                f"context {str(f.get('context_direction')).lower()}",
                weight=w["context"],
            )
        )
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
        return direction, checks

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        which = f.get("confirm_candle")
        if direction == TradeDirection.LONG:
            return float(f["confirm_high"]), f"trigger: a trade above the {which} candle's high"  # type: ignore[arg-type]
        return float(f["confirm_low"]), f"trigger: a trade below the {which} candle's low"  # type: ignore[arg-type]

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        buffer = self.config.stop_buffer_atr * ctx.atr
        level = float(f["level"])  # type: ignore[arg-type]
        if direction == TradeDirection.LONG:
            return min(level, float(f["confirm_low"])) - buffer, "below the level / confirming low"  # type: ignore[arg-type]
        return max(level, float(f["confirm_high"])) + buffer, "above the level / confirming high"  # type: ignore[arg-type]

    def calculate_target(
        self,
        ctx: StrategyContext,
        direction: TradeDirection,
        entry: float,
        stop: float,
        f: Features,
    ) -> tuple[float, str]:
        risk = abs(entry - stop)
        move = float(f.get("measured_move") or 0.0)
        sign = side(direction)
        if move >= self.geometry.min_reward_risk * risk:
            return entry + sign * move, "measured move (height of the broken range)"
        return entry + sign * self.geometry.default_reward_risk * risk, (
            f"{self.geometry.default_reward_risk:g}R (measured move too small)"
        )
