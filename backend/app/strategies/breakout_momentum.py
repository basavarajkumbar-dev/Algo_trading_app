"""Breakout momentum.

Concept (from the brief): CONSOLIDATION -> COMPRESSION -> VOLUME
BUILDUP -> BREAKOUT -> CONFIRMATION -> ENTRY CANDIDATE.

Reference range: the consolidation of the `consolidation_window`
candles before the breakout candle (it must be narrower than
`max_consolidation_width_atr`) or, failing that, the configurable
opening range (5 / 15 / 30 minutes from the 09:15 IST open).

Required (all must hold):
- range:        a valid reference range exists
- breakout:     a close beyond it within the last `max_candles_since_breakout` candles
- not_extended: price is no more than `max_extension_atr` past the level
                (beyond that it is chasing, not breaking out)
- volume:       the breakout candle's volume is at least `min_relative_volume`
                times the average

Weighted: a tight consolidation, compression (NR/NRB or contracting
ranges), volatility expansion on the breakout candle, momentum and VWAP
on the breakout side, the opening range broken the same way, and a
higher timeframe that is not against the breakout.

Candidates: entry at the latest close, stop beyond the breakout
candle / broken level, target at the measured move (range height
projected from the level), else a multiple of the risk.
"""

from __future__ import annotations

from typing import ClassVar

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import Relationship, Trend
from app.market_intelligence.utils import mean
from app.strategies.base import BaseStrategy, Features, check, side
from app.strategies.config import BreakoutMomentumConfig
from app.strategies.context import StrategyContext
from app.strategies.opening_range import compute_opening_range
from app.strategies.schemas import StrategyCheck, StrategyName, TradeDirection

_COMPRESSION_PATTERNS = {"NR", "NRB"}


def _range(candles: list[Candle]) -> tuple[float, float]:
    return max(c.high for c in candles), min(c.low for c in candles)


class BreakoutMomentumStrategy(BaseStrategy):
    name = StrategyName.BREAKOUT_MOMENTUM
    stages: ClassVar[tuple[tuple[str, str], ...]] = (
        ("CONSOLIDATION", "range"),
        ("COMPRESSION", "compression"),
        ("VOLUME_BUILDUP", "volume"),
        ("BREAKOUT", "breakout"),
        ("CONFIRMATION", "momentum"),
    )

    def __init__(self, config: BreakoutMomentumConfig | None = None) -> None:
        self.config = config or BreakoutMomentumConfig()
        super().__init__(self.config.geometry, self.config.weights)

    # --- analyze ------------------------------------------------------------------

    def _consolidation_breakout(self, ctx: StrategyContext) -> dict[str, float | int | str] | None:
        """Most recent candle (within the allowed lag) that closed beyond
        the high/low of the consolidation window before it."""

        cfg = self.config
        candles = ctx.candles
        for lag in range(cfg.max_candles_since_breakout):
            i = len(candles) - 1 - lag
            window = candles[i - cfg.consolidation_window : i]
            if i - cfg.consolidation_window < 0:
                break
            high, low = _range(window)
            close = candles[i].close
            if close > high:
                return {"index": i, "direction": "LONG", "level": high, "high": high, "low": low}
            if close < low:
                return {"index": i, "direction": "SHORT", "level": low, "high": high, "low": low}
        return None

    def _first_close_beyond(self, candles: list[Candle], level: float, upward: bool) -> int | None:
        """Index of the candle (within the allowed lag) whose close first
        crossed `level`: it closed beyond it and the candle before did not."""

        oldest = max(1, len(candles) - self.config.max_candles_since_breakout)
        for i in range(len(candles) - 1, oldest - 1, -1):
            beyond = candles[i].close > level if upward else candles[i].close < level
            before = candles[i - 1].close > level if upward else candles[i - 1].close < level
            if beyond and not before:
                return i
        return None

    def analyze(self, ctx: StrategyContext) -> Features:
        cfg, a, pa = self.config, ctx.analysis, ctx.price_action
        candles = ctx.candles
        f: Features = {
            "regime": ctx.regime.regime.value,
            "atr": round(ctx.atr, 4),
            "price": ctx.price,
        }

        orb = compute_opening_range(
            candles,
            minutes=cfg.opening_range_minutes,
            session_start=cfg.session_start,
            timezone=cfg.session_timezone,
        )
        f["opening_range_minutes"] = cfg.opening_range_minutes
        f["opening_range_available"] = orb.available
        f["opening_range_note"] = orb.reason
        f["opening_range_high"] = orb.high
        f["opening_range_low"] = orb.low
        orb_direction = None
        if orb.available and orb.high is not None and orb.low is not None and orb.end is not None:
            if ctx.price > orb.high:
                orb_direction = "LONG"
            elif ctx.price < orb.low:
                orb_direction = "SHORT"
        f["opening_range_breakout"] = orb_direction

        found = self._consolidation_breakout(ctx)
        reference = None
        if found is not None:
            width_atr = (float(found["high"]) - float(found["low"])) / ctx.atr
            f["consolidation_width_atr"] = round(width_atr, 2)
            if width_atr <= cfg.max_consolidation_width_atr:
                reference = "consolidation"
        if (
            reference is None
            and orb_direction is not None
            and orb.high is not None
            and orb.low is not None
        ):
            index = self._first_close_beyond(
                candles, orb.high if orb_direction == "LONG" else orb.low, orb_direction == "LONG"
            )
            if index is not None:
                found = {
                    "index": index,
                    "direction": orb_direction,
                    "level": orb.high if orb_direction == "LONG" else orb.low,
                    "high": orb.high,
                    "low": orb.low,
                }
                reference = "opening range"
        f["reference_range"] = reference

        if found is not None and reference is not None:
            i = int(found["index"])
            breakout = candles[i]
            direction = TradeDirection(str(found["direction"]))
            sign = side(direction)
            level = float(found["level"])
            f["breakout_direction"] = direction.value
            f["breakout_level"] = level
            f["range_high"] = float(found["high"])
            f["range_low"] = float(found["low"])
            f["candles_since_breakout"] = len(candles) - 1 - i
            f["breakout_candle_high"] = breakout.high
            f["breakout_candle_low"] = breakout.low
            f["extension_atr"] = round((ctx.price - level) * sign / ctx.atr, 2)
            previous = candles[max(0, i - 20) : i]
            avg_volume = mean([c.volume for c in previous])
            avg_range = mean([c.high - c.low for c in previous])
            f["breakout_relative_volume"] = (
                round(breakout.volume / avg_volume, 2) if avg_volume else None
            )
            f["breakout_range_multiple"] = (
                round((breakout.high - breakout.low) / avg_range, 2) if avg_range else None
            )
            window = candles[max(0, i - cfg.consolidation_window) : i]
            baseline = candles[max(0, i - 100) : i]
            window_tr = mean([c.high - c.low for c in window])
            baseline_tr = mean([c.high - c.low for c in baseline])
            f["consolidation_range_ratio"] = (
                round(window_tr / baseline_tr, 2) if baseline_tr else None
            )
            start = breakout.timestamp
            lookback_start = candles[max(0, i - cfg.compression_lookback)].timestamp
            compression = sorted(
                {
                    m.pattern.value
                    for m in pa.recent_patterns
                    if m.pattern.value in _COMPRESSION_PATTERNS
                    and lookback_start <= m.timestamp < start
                }
            )
            f["compression_patterns"] = ",".join(compression) or None
        else:
            f["breakout_direction"] = None

        f["momentum_score"] = a.momentum.momentum_score if a.momentum else None
        f["price_vs_vwap"] = a.vwap.price_vs_vwap.value if a.vwap else None
        f["volatility_expanding"] = a.volatility.is_expanding if a.volatility else None
        mtf = pa.multi_timeframe
        f["context_direction"] = mtf.context_analysis.direction.value if mtf else None
        return f

    # --- validate -----------------------------------------------------------------

    def validate(
        self, ctx: StrategyContext, f: Features
    ) -> tuple[TradeDirection, list[StrategyCheck]]:
        cfg, w = self.config, self.weights
        raw = f.get("breakout_direction")
        direction = TradeDirection(raw) if isinstance(raw, str) else TradeDirection.NONE
        sign = side(direction)
        reference = f.get("reference_range")
        checks: list[StrategyCheck] = []

        if reference == "consolidation":
            range_detail = f"{f.get('consolidation_width_atr')} ATR consolidation over {cfg.consolidation_window} candles"
        elif reference == "opening range":
            range_detail = f"{cfg.opening_range_minutes}m opening range"
        elif f.get("consolidation_width_atr") is not None:
            range_detail = (
                f"range is {f.get('consolidation_width_atr')} ATR wide (max "
                f"{cfg.max_consolidation_width_atr:g}); {f.get('opening_range_note')}"
            )
        else:
            range_detail = f"no recent close beyond a consolidation; {f.get('opening_range_note')}"
        checks.append(
            check(
                "range",
                "Reference range",
                reference is not None,
                range_detail,
                required=True,
                weight=w["consolidation"],
            )
        )

        breakout_ok = direction != TradeDirection.NONE
        checks.append(
            check(
                "breakout",
                "Breakout",
                breakout_ok,
                (
                    f"closed {'above' if sign > 0 else 'below'} {f.get('breakout_level')} "
                    f"{f.get('candles_since_breakout')} candle(s) ago"
                    if breakout_ok
                    else "no close beyond the range"
                ),
                required=True,
            )
        )
        extension = f.get("extension_atr")
        checks.append(
            check(
                "not_extended",
                "Not extended",
                isinstance(extension, float) and extension <= cfg.max_extension_atr,
                (
                    f"{extension} ATR past the level (max {cfg.max_extension_atr:g})"
                    if isinstance(extension, float)
                    else "no breakout level"
                ),
                required=True,
            )
        )
        rv = f.get("breakout_relative_volume")
        checks.append(
            check(
                "volume",
                "Volume",
                isinstance(rv, float) and rv >= cfg.min_relative_volume,
                f"breakout candle {rv}x average volume (min {cfg.min_relative_volume:g}x)",
                required=True,
                weight=w["volume"],
            )
        )

        ratio = f.get("consolidation_range_ratio")
        patterns = f.get("compression_patterns")
        compressed = bool(patterns) or (isinstance(ratio, float) and ratio <= 0.8)
        checks.append(
            check(
                "compression",
                "Compression",
                compressed,
                f"{patterns or 'no NR/NRB'}; candle ranges {ratio}x their longer average",
                weight=w["compression"],
            )
        )
        multiple = f.get("breakout_range_multiple")
        checks.append(
            check(
                "expansion",
                "Volatility expansion",
                (isinstance(multiple, float) and multiple >= cfg.expansion_range_multiple)
                or bool(f.get("volatility_expanding")),
                f"breakout candle {multiple}x the average range",
                weight=w["expansion"],
            )
        )
        momentum = f.get("momentum_score")
        checks.append(
            check(
                "momentum",
                "Momentum",
                isinstance(momentum, float)
                and (momentum >= 55 if sign > 0 else momentum <= 45)
                and sign != 0,
                f"score {momentum}",
                weight=w["momentum"],
            )
        )
        wanted = Relationship.ABOVE.value if sign > 0 else Relationship.BELOW.value
        checks.append(
            check(
                "vwap",
                "VWAP",
                sign != 0 and f.get("price_vs_vwap") == wanted,
                f"price {str(f.get('price_vs_vwap')).lower()} VWAP",
                weight=w["vwap"],
            )
        )
        checks.append(
            check(
                "opening_range",
                "Opening range",
                sign != 0 and f.get("opening_range_breakout") == direction.value,
                str(f.get("opening_range_note")),
                weight=w["opening_range"],
            )
        )
        against = Trend.DOWN.value if sign > 0 else Trend.UP.value
        checks.append(
            check(
                "higher_timeframe",
                "Higher timeframe",
                sign != 0 and f.get("context_direction") not in (None, against),
                f"context {str(f.get('context_direction')).lower()}",
                weight=w["higher_timeframe"],
            )
        )
        return direction, checks

    # --- candidates ---------------------------------------------------------------

    def calculate_entry(
        self, ctx: StrategyContext, direction: TradeDirection, f: Features
    ) -> tuple[float, str]:
        return ctx.price, "latest close after the breakout"

    def calculate_stop_loss(
        self, ctx: StrategyContext, direction: TradeDirection, entry: float, f: Features
    ) -> tuple[float, str]:
        level = float(f["breakout_level"])  # type: ignore[arg-type]
        buffer = self.geometry.stop_buffer_atr * ctx.atr
        if direction == TradeDirection.LONG:
            low = min(float(f["breakout_candle_low"]), level)  # type: ignore[arg-type]
            return low - buffer, "below the breakout candle and the broken level"
        high = max(float(f["breakout_candle_high"]), level)  # type: ignore[arg-type]
        return high + buffer, "above the breakout candle and the broken level"

    def calculate_target(
        self,
        ctx: StrategyContext,
        direction: TradeDirection,
        entry: float,
        stop: float,
        f: Features,
    ) -> tuple[float, str]:
        sign = side(direction)
        risk = abs(entry - stop)
        height = float(f["range_high"]) - float(f["range_low"])  # type: ignore[arg-type]
        measured = float(f["breakout_level"]) + sign * height  # type: ignore[arg-type]
        f["measured_move_target"] = round(measured, 4)
        if (measured - entry) * sign >= self.geometry.min_reward_risk * risk:
            return measured, "measured move (range height from the level)"
        return (
            entry + sign * self.geometry.default_reward_risk * risk,
            f"{self.geometry.default_reward_risk:g}R (measured move too short)",
        )
