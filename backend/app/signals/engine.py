"""SignalEngine: eligible strategy candidates -> scored TradingSignals.

For every eligible StrategyResult: score it (ConfidenceScoringEngine),
compute a quantity candidate, and assemble the signal with its reasons
("why this signal"), the strategy's own checks and the strategy
version. Nothing here sizes for a real account or contacts a broker.
"""

from __future__ import annotations

import math
from datetime import datetime

from app.market_data.base import utcnow
from app.market_intelligence.utils import mean
from app.regime.schemas import MarketRegime
from app.signals.config import QuantityConfig, SignalEngineConfig
from app.signals.schemas import MarketContext, SignalDirection, TradingSignal
from app.signals.scoring import ConfidenceScoringEngine, explain
from app.strategies.context import StrategyContext
from app.strategies.router import SymbolStrategyEvaluation
from app.strategies.schemas import StrategyResult, TradeDirection


def to_signal_direction(direction: TradeDirection) -> SignalDirection:
    return {
        TradeDirection.LONG: SignalDirection.BUY,
        TradeDirection.SHORT: SignalDirection.SELL,
    }.get(direction, SignalDirection.NONE)


def quantity_candidate(entry: float, stop: float, config: QuantityConfig) -> tuple[int, str]:
    """Shares such that hitting the stop loses about `risk_per_trade_percent`
    of `capital`, capped so the position is worth at most
    `max_position_value`. A candidate for the Risk Engine, not a size."""

    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or entry <= 0:
        return 0, "no valid risk per share"
    risk_budget = config.capital * config.risk_per_trade_percent / 100.0
    by_risk = math.floor(risk_budget / risk_per_share)
    by_value = math.floor(config.max_position_value / entry)
    quantity = max(0, min(by_risk, by_value))
    limiter = "position value cap" if by_value < by_risk else "risk budget"
    note = (
        f"{quantity} shares: risk {config.risk_per_trade_percent:g}% of "
        f"{config.capital:,.0f} at {risk_per_share:.2f}/share, capped at "
        f"{config.max_position_value:,.0f} position value (limited by {limiter}). "
        "Candidate only; the Risk Engine decides the real size."
    )
    if quantity == 0:
        note = "0 shares: one share exceeds the risk budget or the position cap. " + note
    return quantity, note


def liquidity(ctx: StrategyContext, window: int) -> float | None:
    """Average traded value per candle (close x volume) over `window` candles."""

    recent = ctx.candles[-window:]
    if not recent:
        return None
    return round(mean([c.close * c.volume for c in recent]), 2)


class SignalEngine:
    def __init__(self, config: SignalEngineConfig | None = None) -> None:
        self.config = config or SignalEngineConfig()
        self.scorer = ConfidenceScoringEngine(self.config.scoring, self.config.categories)

    def build_signal(
        self,
        ctx: StrategyContext,
        result: StrategyResult,
        market: MarketContext,
        *,
        now: datetime | None = None,
    ) -> TradingSignal:
        if (
            not result.eligible
            or result.entry_candidate is None
            or result.stop_candidate is None
            or result.target_candidate is None
        ):
            raise ValueError("Only eligible candidates with entry, stop and target become signals")
        breakdown = self.scorer.score(ctx, result, market)
        quantity, note = quantity_candidate(
            result.entry_candidate, result.stop_candidate, self.config.quantity
        )
        a = ctx.analysis
        rv = result.features.get("breakout_relative_volume")
        if not isinstance(rv, int | float):
            rv = a.volume.relative_volume if a.volume else None
        features = dict(result.features)
        features["relative_strength_rank"] = ctx.relative_strength_rank
        features["market_context_regime"] = market.regime.value if market.regime else None
        return TradingSignal(
            symbol=ctx.symbol,
            direction=to_signal_direction(result.direction),
            strategy=result.strategy,
            strategy_version=result.strategy_version,
            timeframe=ctx.timeframe,
            entry=result.entry_candidate,
            stop_loss=result.stop_candidate,
            target=result.target_candidate,
            risk_reward=result.reward_risk or 0.0,
            entry_type=result.entry_type,
            entry_valid_bars=result.entry_valid_bars,
            quantity_candidate=quantity,
            quantity_note=note,
            score=breakdown.total,
            category=breakdown.category,
            executable=breakdown.executable,
            confidence=result.confidence,
            relative_strength=(
                round(a.relative_strength.relative_strength_score, 2)
                if a.relative_strength
                else None
            ),
            trend_quality=round(a.trend_quality.score, 2) if a.trend_quality else None,
            relative_volume=round(float(rv), 2) if rv is not None else None,
            liquidity=liquidity(ctx, self.config.ranking.liquidity_window),
            market_regime=ctx.regime.regime,
            regime_confidence=ctx.regime.confidence,
            breakdown=breakdown,
            reasons=explain(breakdown),
            strategy_reasons=result.reasons,
            strategy_checks=result.checks,
            features=features,
            candle_time=ctx.latest.timestamp,
            timestamp=now or utcnow(),
        )

    def build(
        self,
        evaluations: dict[str, SymbolStrategyEvaluation],
        contexts: dict[str, StrategyContext],
        market: MarketContext,
        *,
        now: datetime | None = None,
    ) -> list[TradingSignal]:
        stamp = now or utcnow()
        signals = []
        for symbol, evaluation in evaluations.items():
            ctx = contexts.get(symbol)
            if ctx is None:
                continue
            for candidate in evaluation.candidates:
                signals.append(self.build_signal(ctx, candidate, market, now=stamp))
        return signals


def market_context_from(
    contexts: dict[str, StrategyContext],
    benchmark_symbol: str,
    change_percent: float | None = None,
) -> MarketContext:
    ctx = contexts.get(benchmark_symbol)
    if ctx is None:
        return MarketContext(benchmark_symbol=benchmark_symbol, available=False)
    mtf = ctx.price_action.multi_timeframe
    regime: MarketRegime = ctx.regime.regime
    return MarketContext(
        benchmark_symbol=benchmark_symbol,
        available=True,
        regime=regime,
        regime_confidence=ctx.regime.confidence,
        bias_score=mtf.bias_score if mtf is not None else None,
        direction=(
            mtf.overall_bias.value.replace("BULLISH", "UP")
            .replace("BEARISH", "DOWN")
            .replace("NEUTRAL", "SIDEWAYS")
            if mtf is not None
            else None
        ),
        price=ctx.price,
        change_percent=change_percent,
    )
