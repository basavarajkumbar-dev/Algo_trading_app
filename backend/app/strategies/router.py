"""StrategyRouter: market regime in, eligible strategies out.

The mapping is configuration (`RouterConfig.routes`), defaulting to the
brief's example: strong trends -> trend continuation (with the trend's
direction only), breakout -> breakout momentum, range -> mean
reversion, high volatility / unknown -> nothing (defensive, no trade).

The router only decides which strategies are *appropriate to run* for
a regime. It does not rank strategies or claim any of them is better
than another, and a routed strategy still has to find its own setup.
Nothing here talks to a broker.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.regime.schemas import MarketRegime
from app.strategies.base import BaseStrategy
from app.strategies.breakout_momentum import BreakoutMomentumStrategy
from app.strategies.breakout_trading import BreakoutTradingStrategy
from app.strategies.config import StrategyEngineConfig
from app.strategies.context import StrategyContext
from app.strategies.ma_crossover import MovingAverageCrossoverStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.range_trading import RangeTradingStrategy
from app.strategies.schemas import (
    CANDIDATE_NOTE,
    STRATEGY_LABELS,
    RouterDecision,
    RoutingEntry,
    StrategyName,
    StrategyResult,
)
from app.strategies.trend_continuation import TrendContinuationStrategy
from app.strategies.volume_trading import VolumeTradingStrategy


class SymbolStrategyEvaluation(BaseModel):
    symbol: str
    decision: RouterDecision
    results: list[StrategyResult]  # one per routed strategy, eligible or not
    candidates: list[StrategyResult] = Field(default_factory=list)  # the eligible ones
    data_as_of: datetime | None
    timestamp: datetime
    note: str = CANDIDATE_NOTE


class StrategyRouter:
    def __init__(self, config: StrategyEngineConfig | None = None) -> None:
        self.config = config or StrategyEngineConfig()
        cfg = self.config
        self.strategies: dict[StrategyName, BaseStrategy] = {
            StrategyName.TREND_CONTINUATION: TrendContinuationStrategy(cfg.trend_continuation),
            StrategyName.BREAKOUT_MOMENTUM: BreakoutMomentumStrategy(cfg.breakout_momentum),
            StrategyName.MEAN_REVERSION: MeanReversionStrategy(cfg.mean_reversion),
            StrategyName.RANGE_TRADING: RangeTradingStrategy(cfg.range_trading),
            StrategyName.BREAKOUT_TRADING: BreakoutTradingStrategy(cfg.breakout_trading),
            StrategyName.VOLUME_TRADING: VolumeTradingStrategy(cfg.volume_trading),
            StrategyName.MA_CROSSOVER: MovingAverageCrossoverStrategy(cfg.ma_crossover),
        }
        self.enabled: dict[StrategyName, bool] = {
            StrategyName.TREND_CONTINUATION: cfg.trend_continuation.enabled,
            StrategyName.BREAKOUT_MOMENTUM: cfg.breakout_momentum.enabled,
            StrategyName.MEAN_REVERSION: cfg.mean_reversion.enabled,
            StrategyName.RANGE_TRADING: cfg.range_trading.enabled,
            StrategyName.BREAKOUT_TRADING: cfg.breakout_trading.enabled,
            StrategyName.VOLUME_TRADING: cfg.volume_trading.enabled,
            StrategyName.MA_CROSSOVER: cfg.ma_crossover.enabled,
        }

    def route(self, regime: MarketRegime, confidence: float) -> RouterDecision:
        rules = {rule.strategy: rule for rule in self.config.router.routes.get(regime, [])}
        minimum = self.config.router.min_regime_confidence
        confident = confidence >= minimum
        entries: list[RoutingEntry] = []
        for name in StrategyName:
            label = STRATEGY_LABELS[name]
            rule = rules.get(name)
            if rule is None:
                entries.append(
                    RoutingEntry(
                        strategy=name,
                        routed=False,
                        reason=f"{label} is not mapped to {regime.value}",
                    )
                )
            elif not self.enabled[name]:
                entries.append(
                    RoutingEntry(strategy=name, routed=False, reason=f"{label} is disabled")
                )
            elif not confident:
                entries.append(
                    RoutingEntry(
                        strategy=name,
                        routed=False,
                        reason=f"regime confidence {confidence:.0f} is below {minimum:g}",
                    )
                )
            else:
                directions = ", ".join(d.value.lower() for d in rule.directions)
                entries.append(
                    RoutingEntry(
                        strategy=name,
                        routed=True,
                        reason=f"{label} runs in {regime.value} ({directions})",
                        allowed_directions=list(rule.directions),
                    )
                )

        # Keep the configured order for routed strategies (evaluation order, not rank).
        order = [rule.strategy for rule in self.config.router.routes.get(regime, [])]
        eligible = [name for name in order if any(e.strategy == name and e.routed for e in entries)]
        reasons = [f"Regime {regime.value} ({confidence:.0f} confidence)"]
        if not rules:
            reasons.append("No strategy is mapped to this regime: defensive, no trade candidates")
        elif not eligible:
            reasons.append("Mapped strategies are disabled or the regime is not confident enough")
        return RouterDecision(
            regime=regime,
            regime_confidence=confidence,
            eligible_strategies=eligible,
            entries=entries,
            reasons=reasons,
        )

    def evaluate(self, ctx: StrategyContext) -> SymbolStrategyEvaluation:
        decision = self.route(ctx.regime.regime, ctx.regime.confidence)
        routed = {e.strategy: e for e in decision.entries if e.routed}
        results = [
            self.strategies[name].evaluate(ctx, routed[name].allowed_directions)
            for name in decision.eligible_strategies
        ]
        return SymbolStrategyEvaluation(
            symbol=ctx.symbol,
            decision=decision,
            results=results,
            candidates=[r for r in results if r.eligible],
            data_as_of=ctx.latest.timestamp,
            timestamp=ctx.now,
        )

    def evaluate_all(self, ctx: StrategyContext) -> list[StrategyResult]:
        """Every enabled strategy regardless of routing, for research and
        transparency (e.g. "what would mean reversion say here?"). The
        router's decision is what the platform acts on."""

        return [s.evaluate(ctx) for name, s in self.strategies.items() if self.enabled[name]]
