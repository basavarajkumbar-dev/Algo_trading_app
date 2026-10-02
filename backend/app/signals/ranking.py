"""CandidateRankingEngine.

Orders signals by, in turn (from the brief): signal score, risk:reward,
relative strength (on the signal's side), liquidity, trend quality,
volume and strategy confidence. Scores are compared rounded to whole
points so that the later keys decide between near-identical scores
instead of noise in the decimals.

A rank orders the evidence the engines found; it is not a forecast
and not proof that a higher-ranked signal will do better.
"""

from __future__ import annotations

from app.signals.config import RankingConfig, SignalEngineConfig
from app.signals.schemas import (
    CATEGORY_ORDER,
    SignalCategory,
    SignalDirection,
    TradeCandidate,
    TradingSignal,
)

RANKING_NOTE = "Ranking orders the current evidence; it is not proof of profitability."


def status_for(signal: TradingSignal) -> str:
    if signal.executable:
        return "READY"
    return "WATCH" if signal.category != SignalCategory.IGNORE else "IGNORE"


def sort_key(signal: TradingSignal) -> tuple[float | str, ...]:
    """Ascending sort key: best first."""

    rs = signal.relative_strength
    side_rs = (
        (rs if signal.direction == SignalDirection.BUY else 100.0 - rs) if rs is not None else 0.0
    )
    return (
        -round(signal.score),
        -signal.risk_reward,
        -side_rs,
        -(signal.liquidity or 0.0),
        -(signal.trend_quality or 0.0),
        -(signal.relative_volume or 0.0),
        -signal.confidence,
        signal.symbol,  # deterministic final tie-break
    )


class CandidateRankingEngine:
    def __init__(self, config: RankingConfig | None = None) -> None:
        self.config = config or SignalEngineConfig().ranking

    def rank(self, signals: list[TradingSignal]) -> list[TradeCandidate]:
        ordered = sorted(signals, key=sort_key)
        return [
            TradeCandidate(
                rank=position,
                signal_id=s.id,
                symbol=s.symbol,
                direction=s.direction,
                strategy=s.strategy,
                score=s.score,
                category=s.category,
                status=status_for(s),
                entry=s.entry,
                stop_loss=s.stop_loss,
                target=s.target,
                risk_reward=s.risk_reward,
                quantity_candidate=s.quantity_candidate,
                relative_strength=s.relative_strength,
                liquidity=s.liquidity,
                trend_quality=s.trend_quality,
                relative_volume=s.relative_volume,
                strategy_confidence=s.confidence,
                market_regime=s.market_regime,
            )
            for position, s in enumerate(ordered, start=1)
        ]

    def top(self, ranked: list[TradeCandidate]) -> list[TradeCandidate]:
        minimum = CATEGORY_ORDER.index(SignalCategory(self.config.min_category_for_top))
        return [c for c in ranked if CATEGORY_ORDER.index(c.category) >= minimum][
            : self.config.top_n
        ]
