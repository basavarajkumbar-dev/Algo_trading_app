"""ConfidenceScoringEngine: a 0-100 score for a strategy candidate.

Nine components (weights from ScoringConfig, the brief's initial model):
market alignment, relative strength, trend quality, VWAP, market
structure, retracement, volume, momentum, price action.

Each component scores 0-1 *relative to the candidate's direction and
strategy*. Most read the same for trend continuation and breakout
momentum; for mean reversion, which fades a stretch inside a range,
some invert: a calm market and a weak trend are what that setup needs.
A component that does not apply to a strategy is excluded and the
other weights are scaled up, rather than scoring it as zero.

The score measures how much of the weighted evidence is present. It is
not a probability of success and not proof of profitability.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.market_intelligence.schemas import Relationship, Trend
from app.market_intelligence.utils import clamp
from app.price_action.config import RetracementConfig
from app.price_action.retracement import zone_for
from app.price_action.schemas import PatternDirection, RetracementZone
from app.signals.config import SCORE_COMPONENTS, CategoryConfig, ScoringConfig
from app.signals.schemas import (
    MarketContext,
    ScoreBreakdown,
    ScoreComponent,
    SignalCategory,
)
from app.strategies.context import StrategyContext
from app.strategies.schemas import StrategyName, StrategyResult, TradeDirection

LABELS: dict[str, str] = {
    "market_alignment": "Market alignment",
    "relative_strength": "Relative strength",
    "trend_quality": "Trend quality",
    "vwap": "VWAP",
    "market_structure": "Market structure",
    "retracement": "Retracement",
    "volume": "Volume",
    "momentum": "Momentum",
    "price_action": "Price action",
}

_DIRECTIONAL_PATTERNS = {"COG", "WRB", "BT", "TT"}
_RETRACEMENT_SCORES: dict[RetracementZone, float] = {
    RetracementZone.HEALTHY_PULLBACK: 1.0,
    RetracementZone.DEEP_PULLBACK: 0.8,
    RetracementZone.SHALLOW: 0.6,
    RetracementZone.WEAKENING: 0.3,
    RetracementZone.MAJOR_WEAKNESS: 0.1,
    RetracementZone.INVALIDATION_CONTEXT: 0.0,
}


@dataclass
class _Inputs:
    ctx: StrategyContext
    result: StrategyResult
    market: MarketContext
    sign: float  # +1 buy, -1 sell
    fade: bool  # mean reversion


# A scorer returns (score 0-1 or None if not applicable, detail).
Scorer = Callable[[_Inputs], tuple[float | None, str]]


def _bias_side(bias: float | None, sign: float, full: float = 40.0) -> float:
    """0..1: how far a -100..+100 bias leans the trade's way (0.5 = neutral)."""

    if bias is None:
        return 0.5
    return (clamp(sign * bias / full, -1.0, 1.0) + 1.0) / 2.0


def market_alignment(i: _Inputs) -> tuple[float | None, str]:
    mtf = i.ctx.price_action.multi_timeframe
    symbol_bias = mtf.bias_score if mtf is not None else None
    alignment = mtf.alignment_score if mtf is not None else None
    market_bias = (
        i.market.bias_score
        if i.market.available and i.ctx.symbol != i.market.benchmark_symbol
        else None
    )

    if i.fade:
        parts = [1.0 - abs(b) / 100.0 for b in (market_bias, symbol_bias) if b is not None]
        if not parts:
            return None, "no market or timeframe data"
        return sum(parts) / len(parts), (
            f"mean reversion wants calm: market bias {market_bias}, symbol bias {symbol_bias}"
        )

    parts = []
    detail = []
    if market_bias is not None:
        parts.append(_bias_side(market_bias, i.sign))
        detail.append(f"NIFTY 50 bias {market_bias:+.0f}")
    if symbol_bias is not None and alignment is not None:
        on_side = symbol_bias * i.sign > 0
        parts.append(alignment / 100.0 if on_side else 0.0)
        side_word = "with" if on_side else "against"
        detail.append(
            f"own timeframe bias {symbol_bias:+.0f} ({side_word} the trade), "
            f"timeframe alignment {alignment:.0f}/100"
        )
    if not parts:
        return None, "no market or timeframe data"
    return sum(parts) / len(parts), ", ".join(detail)


def relative_strength(i: _Inputs) -> tuple[float | None, str]:
    rs = i.ctx.analysis.relative_strength
    if rs is None:
        return None, "not available (no benchmark comparison)"
    score = rs.relative_strength_score
    rank = i.ctx.relative_strength_rank
    rank_text = f", rank {rank}/{i.ctx.universe_size}" if rank is not None else ""
    return clamp(i.sign * (score - 50.0) / 30.0, 0.0, 1.0), f"RS score {score:.0f}{rank_text}"


def trend_quality(i: _Inputs) -> tuple[float | None, str]:
    tq = i.ctx.analysis.trend_quality
    if tq is None:
        return None, "not available"
    if i.fade:
        return (
            1.0 - tq.score / 100.0,
            f"{tq.score:.0f}: the weaker the trend, the better for a fade",
        )
    wanted = Trend.UP if i.sign > 0 else Trend.DOWN
    if tq.direction == wanted:
        value = tq.score / 100.0
    elif tq.direction == Trend.SIDEWAYS:
        value = 0.3 * tq.score / 100.0
    else:
        value = 0.0
    return value, f"{tq.score:.0f} ({tq.direction.value.lower()})"


def vwap(i: _Inputs) -> tuple[float | None, str]:
    v = i.ctx.analysis.vwap
    if v is None:
        return None, "not available"
    if i.fade:
        deviation = (i.ctx.price - v.vwap) / i.ctx.atr if i.ctx.atr else 0.0
        stretch = -deviation * i.sign  # a short fade wants price above VWAP
        return clamp(stretch / 2.0, 0.0, 1.0), f"{deviation:+.2f} ATR from VWAP"
    wanted = Relationship.ABOVE if i.sign > 0 else Relationship.BELOW
    if v.price_vs_vwap == wanted:
        value = 1.0
    elif v.price_vs_vwap == Relationship.AT:
        value = 0.5
    else:
        value = 0.0
    return value, f"price {v.price_vs_vwap.value.lower()} VWAP"


def market_structure(i: _Inputs) -> tuple[float | None, str]:
    st = i.ctx.analysis.structure
    if st is None:
        return None, "not available"
    if i.fade:
        value = 1.0 if st.trend == Trend.SIDEWAYS else 0.3
        return value, f"structure {st.trend.value.lower()} ({st.structure_state.value.lower()})"
    wanted = Trend.UP if i.sign > 0 else Trend.DOWN
    if st.trend == wanted:
        value = max(0.6, st.confidence / 100.0)
    elif st.trend == Trend.SIDEWAYS:
        value = 0.3
    else:
        value = 0.0
    label = (
        "higher highs / higher lows"
        if st.trend == Trend.UP
        else ("lower highs / lower lows" if st.trend == Trend.DOWN else "mixed swings")
    )
    return value, f"{label} ({st.structure_state.value.lower().replace('_', ' ')})"


def retracement(i: _Inputs) -> tuple[float | None, str]:
    if i.result.strategy != StrategyName.TREND_CONTINUATION:
        return None, "not part of this strategy"
    r = i.ctx.price_action.retracement
    if r is None or not r.detected or r.max_retracement_percentage is None:
        return 0.0, "no measurable pullback"
    zone = r.max_zone or zone_for(r.max_retracement_percentage, RetracementConfig().zone_bounds)
    return _RETRACEMENT_SCORES[zone], (
        f"deepest pullback {r.max_retracement_percentage:.0f}% ({zone.value.lower().replace('_', ' ')})"
    )


def volume(i: _Inputs) -> tuple[float | None, str]:
    rv = i.result.features.get("breakout_relative_volume")
    if not isinstance(rv, int | float):
        rv = i.ctx.analysis.volume.relative_volume if i.ctx.analysis.volume else None
    if rv is None:
        return None, "not available"
    return clamp((float(rv) - 0.8) / 1.2, 0.0, 1.0), f"{float(rv):.2f}x average volume"


def momentum(i: _Inputs) -> tuple[float | None, str]:
    m = i.ctx.analysis.momentum
    if m is None:
        return None, "not available"
    if i.fade:
        slowing = next((c.passed for c in i.result.checks if c.key == "slowdown"), False)
        return (1.0 if slowing else 0.3), (
            "momentum slowing" if slowing else "momentum not slowing yet"
        )
    return (
        clamp(i.sign * (m.momentum_score - 50.0) / 30.0, 0.0, 1.0),
        f"score {m.momentum_score:.0f}",
    )


def price_action(i: _Inputs) -> tuple[float | None, str]:
    wanted = PatternDirection.BULLISH if i.sign > 0 else PatternDirection.BEARISH
    aligned = [
        p
        for p in i.ctx.price_action.patterns
        if p.detected and p.pattern.value in _DIRECTIONAL_PATTERNS and p.direction == wanted
    ]
    if aligned:
        best = max(aligned, key=lambda p: p.confidence)
        return (
            best.confidence / 100.0,
            f"{', '.join(p.pattern.value for p in aligned)} on the latest candle",
        )
    compression = i.result.features.get("compression_patterns")
    if i.result.strategy == StrategyName.BREAKOUT_MOMENTUM and compression:
        return 0.6, f"compression before the breakout ({compression})"
    return 0.0, "no aligned pattern on the latest candle"


SCORERS: dict[str, Scorer] = {
    "market_alignment": market_alignment,
    "relative_strength": relative_strength,
    "trend_quality": trend_quality,
    "vwap": vwap,
    "market_structure": market_structure,
    "retracement": retracement,
    "volume": volume,
    "momentum": momentum,
    "price_action": price_action,
}


def categorize(score: float, categories: CategoryConfig) -> SignalCategory:
    if score >= categories.elite:
        return SignalCategory.ELITE
    if score >= categories.strong:
        return SignalCategory.STRONG
    if score >= categories.watch:
        return SignalCategory.WATCH
    return SignalCategory.IGNORE


class ConfidenceScoringEngine:
    def __init__(
        self, scoring: ScoringConfig | None = None, categories: CategoryConfig | None = None
    ) -> None:
        self.scoring = scoring or ScoringConfig()
        self.categories = categories or CategoryConfig()

    def score(
        self, ctx: StrategyContext, result: StrategyResult, market: MarketContext
    ) -> ScoreBreakdown:
        if result.direction == TradeDirection.NONE:
            raise ValueError("Only directional candidates can be scored")
        inputs = _Inputs(
            ctx=ctx,
            result=result,
            market=market,
            sign=1.0 if result.direction == TradeDirection.LONG else -1.0,
            fade=result.strategy == StrategyName.MEAN_REVERSION,
        )
        weights = self.scoring.weights
        raw: dict[str, tuple[float | None, str]] = {}
        for key in SCORE_COMPONENTS:
            try:
                raw[key] = SCORERS[key](inputs)
            except Exception as exc:  # noqa: BLE001 - one bad input must not void the score
                # Counted as zero evidence, never as "not applicable": an
                # error must not shift weight onto the other components.
                raw[key] = (0.0, f"could not score ({type(exc).__name__}); counted as 0")

        applicable = {k for k, (value, _) in raw.items() if value is not None and weights[k] > 0}
        total_weight = sum(weights[k] for k in applicable)
        scale = 100.0 / total_weight if total_weight else 0.0

        components: list[ScoreComponent] = []
        total = 0.0
        for key in SCORE_COMPONENTS:
            value, detail = raw[key]
            is_applicable = key in applicable
            effective = weights[key] * scale if is_applicable else 0.0
            points = effective * (value or 0.0) if is_applicable else 0.0
            total += points
            components.append(
                ScoreComponent(
                    key=key,
                    label=LABELS[key],
                    weight=weights[key],
                    applicable=is_applicable,
                    effective_weight=round(effective, 2),
                    score=round(clamp(value, 0.0, 1.0), 3) if value is not None else None,
                    points=round(points, 2),
                    passed=is_applicable and (value or 0.0) >= self.scoring.pass_threshold,
                    detail=detail,
                )
            )

        total = round(clamp(total), 1)
        return ScoreBreakdown(
            total=total,
            category=categorize(total, self.categories),
            executable=total >= self.categories.executable_threshold,
            executable_threshold=self.categories.executable_threshold,
            components=components,
        )


def explain(breakdown: ScoreBreakdown) -> list[str]:
    """The brief's "WHY SIGNAL?" list: one line per component, then the score."""

    lines = []
    for c in breakdown.components:
        if not c.applicable:
            lines.append(f"– {c.label}: {c.detail}")
        else:
            mark = "✓" if c.passed else "✗"
            lines.append(f"{mark} {c.label}: {c.detail} ({c.points:.1f}/{c.effective_weight:.1f})")
    lines.append(f"Final score {breakdown.total:.0f} / 100 ({breakdown.category.value})")
    return lines
