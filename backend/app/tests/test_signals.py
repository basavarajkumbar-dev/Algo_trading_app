"""ConfidenceScoringEngine, SignalEngine, CandidateRankingEngine and the
Nifty50Scanner (no database needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.market_intelligence.schemas import Trend
from app.regime.schemas import MarketRegime
from app.scanner.nifty50 import Nifty50Scanner, ScanStatus
from app.signals import scoring
from app.signals.config import (
    CategoryConfig,
    QuantityConfig,
    RankingConfig,
    ScoringConfig,
    SignalEngineConfig,
    default_weights,
)
from app.signals.engine import SignalEngine, market_context_from, quantity_candidate
from app.signals.ranking import CandidateRankingEngine, status_for
from app.signals.schemas import (
    MarketContext,
    ScoreBreakdown,
    SignalCategory,
    SignalDirection,
    TradingSignal,
)
from app.signals.scoring import ConfidenceScoringEngine, categorize, explain
from app.strategies.breakout_momentum import BreakoutMomentumStrategy
from app.strategies.config import TrendContinuationConfig
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.router import StrategyRouter
from app.strategies.schemas import StrategyName
from app.strategies.trend_continuation import TrendContinuationStrategy
from app.tests.strategy_scenarios import (
    breakout_long,
    mean_reversion_short,
    mtf,
    regime,
    trend_long,
    trend_short,
)

BULL_MARKET = MarketContext(
    benchmark_symbol="NIFTY50",
    available=True,
    regime=MarketRegime.STRONG_BULLISH,
    regime_confidence=80,
    bias_score=60,
    direction="UP",
)
BEAR_MARKET = BULL_MARKET.model_copy(
    update={"regime": MarketRegime.STRONG_BEARISH, "bias_score": -60, "direction": "DOWN"}
)
STRONG = {
    "relative_strength": 90.0,
    "relative_volume": 2.2,
    "momentum": 82.0,
    "trend_quality": 92.0,
}


def strong_trend_long(**analysis: Any):
    return trend_long(analysis={**STRONG, **analysis}, mtf=mtf(alignment=100.0, context_bias=70.0))


def signal_for(
    ctx, strategy=None, market: MarketContext = BULL_MARKET, engine: SignalEngine | None = None
) -> TradingSignal:
    result = (strategy or TrendContinuationStrategy()).evaluate(ctx)
    assert result.eligible, result.blockers
    return (engine or SignalEngine()).build_signal(
        ctx, result, market, now=datetime(2026, 1, 6, 9, 0, tzinfo=UTC)
    )


# --- configuration ----------------------------------------------------------------------


def test_default_weights_are_the_initial_model() -> None:
    weights = ScoringConfig().weights
    assert weights == {
        "market_alignment": 15.0,
        "relative_strength": 15.0,
        "trend_quality": 15.0,
        "vwap": 10.0,
        "market_structure": 10.0,
        "retracement": 10.0,
        "volume": 10.0,
        "momentum": 5.0,
        "price_action": 5.0,
    }
    # As specified, these add up to 95; scores are scaled to 100 regardless.
    assert sum(weights.values()) == 95.0


def test_weights_are_validated_and_partial_overrides_keep_defaults() -> None:
    merged = ScoringConfig(weights={"volume": 20}).weights
    assert merged["volume"] == 20 and merged["trend_quality"] == 15
    with pytest.raises(ValidationError):
        ScoringConfig(weights={"astrology": 5})
    with pytest.raises(ValidationError):
        ScoringConfig(weights={"volume": -1})


def test_category_thresholds_are_validated() -> None:
    with pytest.raises(ValidationError):
        CategoryConfig(watch=85, strong=80)
    assert CategoryConfig(executable_threshold=75).executable_threshold == 75


@pytest.mark.parametrize(
    ("score", "category"),
    [
        (100, "ELITE"),
        (90, "ELITE"),
        (89.9, "STRONG"),
        (80, "STRONG"),
        (79.9, "WATCH"),
        (70, "WATCH"),
        (69.9, "IGNORE"),
        (0, "IGNORE"),
    ],
)
def test_categories(score: float, category: str) -> None:
    assert categorize(score, CategoryConfig()) == SignalCategory(category)


# --- scoring ------------------------------------------------------------------------------


def test_strong_trend_setup_scores_elite_and_executable() -> None:
    signal = signal_for(strong_trend_long())
    assert signal.score >= 90
    assert signal.category == SignalCategory.ELITE
    assert signal.executable
    assert all(c.passed for c in signal.breakdown.components)


def test_score_is_the_weighted_sum_of_applicable_components() -> None:
    breakdown = signal_for(trend_long()).breakdown
    applicable = [c for c in breakdown.components if c.applicable]
    assert sum(c.effective_weight for c in applicable) == pytest.approx(100.0, abs=0.05)
    assert sum(c.points for c in breakdown.components) == pytest.approx(breakdown.total, abs=0.1)
    for c in applicable:
        assert c.points == pytest.approx(c.effective_weight * (c.score or 0), abs=0.02)
    assert {c.key for c in breakdown.components} == set(default_weights())


def test_not_applicable_components_are_excluded_not_zeroed() -> None:
    breakdown = signal_for(breakout_long(), BreakoutMomentumStrategy()).breakdown
    retracement = next(c for c in breakdown.components if c.key == "retracement")
    assert not retracement.applicable and retracement.points == 0 and retracement.score is None
    assert sum(c.effective_weight for c in breakdown.components) == pytest.approx(100.0, abs=0.05)


def test_scoring_errors_count_as_zero_not_as_missing(monkeypatch) -> None:
    ctx = strong_trend_long()
    before = signal_for(ctx).breakdown

    def broken(_inputs):
        raise RuntimeError("bad data")

    monkeypatch.setitem(scoring.SCORERS, "relative_strength", broken)
    after = signal_for(ctx).breakdown
    rs = next(c for c in after.components if c.key == "relative_strength")
    assert rs.applicable and rs.score == 0 and "counted as 0" in rs.detail
    assert after.total < before.total  # weight was not moved onto the other components


def test_better_evidence_scores_higher() -> None:
    weak = signal_for(trend_long(analysis={"relative_strength": 55.0})).score
    strong = signal_for(trend_long(analysis={"relative_strength": 90.0})).score
    assert strong > weak


def test_market_against_the_trade_lowers_the_score() -> None:
    with_market = signal_for(trend_long(), market=BULL_MARKET).score
    against = signal_for(trend_long(), market=BEAR_MARKET).score
    assert with_market > against


def test_short_scoring_mirrors_long() -> None:
    long_signal = signal_for(trend_long(), market=BULL_MARKET)
    short_signal = signal_for(trend_short(), market=BEAR_MARKET)
    assert short_signal.direction == SignalDirection.SELL
    long_parts = {c.key: c.score for c in long_signal.breakdown.components}
    short_parts = {c.key: c.score for c in short_signal.breakdown.components}
    for key in ("market_alignment", "vwap", "retracement", "momentum"):
        assert short_parts[key] == pytest.approx(long_parts[key], abs=0.15), key


def test_mean_reversion_rewards_the_absence_of_a_trend() -> None:
    calm = signal_for(mean_reversion_short(), MeanReversionStrategy())
    trend = next(c for c in calm.breakdown.components if c.key == "trend_quality")
    assert trend.score == pytest.approx(0.7)  # trend quality 30 -> 1 - 0.3
    vwap = next(c for c in calm.breakdown.components if c.key == "vwap")
    assert vwap.score == pytest.approx(0.8)  # 1.6 ATR stretch / 2


def test_weights_change_the_score() -> None:
    ctx = trend_long()
    default = signal_for(ctx).score
    volume_only = SignalEngine(
        SignalEngineConfig(
            scoring=ScoringConfig(weights={k: 0.0 for k in default_weights()} | {"volume": 10.0})
        )
    )
    only_volume = signal_for(ctx, engine=volume_only)
    assert only_volume.score != default
    volume = next(c for c in only_volume.breakdown.components if c.key == "volume")
    assert only_volume.score == pytest.approx(100 * (volume.score or 0), abs=0.1)


def test_explanation_lists_every_component_and_the_final_score() -> None:
    lines = explain(signal_for(strong_trend_long()).breakdown)
    assert len(lines) == 10
    assert lines[0].startswith("✓ Market alignment")
    assert lines[-1].startswith("Final score") and "ELITE" in lines[-1]
    breakout = explain(signal_for(breakout_long(), BreakoutMomentumStrategy()).breakdown)
    assert any(line.startswith("– Retracement") for line in breakout)


def test_only_directional_candidates_are_scored() -> None:
    result = TrendContinuationStrategy().evaluate(
        trend_long(regime=MarketRegime.RANGE, mtf=mtf(context=Trend.SIDEWAYS, context_bias=5))
    )
    with pytest.raises(ValueError):
        ConfidenceScoringEngine().score(trend_long(), result, BULL_MARKET)


# --- signal object ------------------------------------------------------------------------


def test_signal_carries_every_required_field() -> None:
    signal = signal_for(trend_long())
    dumped = signal.model_dump()
    for field in (
        "symbol",
        "direction",
        "strategy",
        "entry",
        "stop_loss",
        "target",
        "quantity_candidate",
        "score",
        "market_regime",
        "reasons",
        "timestamp",
        "strategy_version",
        "confidence",
        "risk_reward",
        "status",
    ):
        assert field in dumped, field
    assert signal.direction == SignalDirection.BUY
    assert (signal.entry, signal.stop_loss, signal.target) == (102.4, 99.8, 110.0)
    assert signal.strategy_version == TrendContinuationStrategy().version_id
    assert signal.market_regime == MarketRegime.STRONG_BULLISH
    assert "not orders" in signal.note


def test_strategy_version_tracks_configuration() -> None:
    default = TrendContinuationStrategy().version_id
    tuned = TrendContinuationStrategy(TrendContinuationConfig(min_pullback_percent=25)).version_id
    assert default != tuned
    assert default.startswith("1.0.0+") and len(default.split("+")[1]) == 8


def test_quantity_candidate() -> None:
    config = QuantityConfig(capital=100_000, risk_per_trade_percent=1.0, max_position_value=50_000)
    # risk budget 1,000 / 2.6 per share = 384 shares; value cap 50,000 / 102.4 = 488
    assert quantity_candidate(102.4, 99.8, config)[0] == 384
    # tight stop: risk allows 10,000 shares, the value cap allows 488
    qty, note = quantity_candidate(102.4, 102.3, config)
    assert qty == 488 and "position value cap" in note
    assert quantity_candidate(100.0, 100.0, config)[0] == 0
    assert quantity_candidate(60_000.0, 59_000.0, config)[0] == 0  # one share exceeds the cap
    assert "Candidate only" in quantity_candidate(102.4, 99.8, config)[1]


def test_ineligible_results_do_not_become_signals() -> None:
    result = TrendContinuationStrategy().evaluate(
        trend_long(patterns=[], mtf=mtf(momentum_returning=False))
    )
    with pytest.raises(ValueError):
        SignalEngine().build_signal(trend_long(), result, BULL_MARKET)


# --- ranking -------------------------------------------------------------------------------


def make(
    symbol: str,
    score: float,
    rr: float = 2.0,
    rs: float | None = 60.0,
    direction: SignalDirection = SignalDirection.BUY,
    **extra: Any,
) -> TradingSignal:
    breakdown = ScoreBreakdown(
        total=score,
        category=categorize(score, CategoryConfig()),
        executable=score >= 85,
        executable_threshold=85,
        components=[],
    )
    base = signal_for(trend_long())
    return base.model_copy(
        update={
            "symbol": symbol,
            "score": score,
            "category": breakdown.category,
            "executable": breakdown.executable,
            "breakdown": breakdown,
            "risk_reward": rr,
            "relative_strength": rs,
            "direction": direction,
            **extra,
        }
    )


def test_ranking_orders_by_score_first() -> None:
    ranked = CandidateRankingEngine().rank([make("A", 72), make("B", 91), make("C", 84)])
    assert [c.symbol for c in ranked] == ["B", "C", "A"]
    assert [c.rank for c in ranked] == [1, 2, 3]
    assert [c.status for c in ranked] == ["READY", "WATCH", "WATCH"]


def test_ranking_tie_breaks_follow_the_brief() -> None:
    # equal (rounded) scores: risk:reward decides, then relative strength on the signal's side
    by_rr = CandidateRankingEngine().rank([make("A", 80.2, rr=1.8), make("B", 79.9, rr=2.5)])
    assert [c.symbol for c in by_rr] == ["B", "A"]
    by_rs = CandidateRankingEngine().rank([make("A", 80, rs=55), make("B", 80, rs=75)])
    assert [c.symbol for c in by_rs] == ["B", "A"]
    shorts = CandidateRankingEngine().rank(
        [
            make("A", 80, rs=30, direction=SignalDirection.SELL),
            make("B", 80, rs=45, direction=SignalDirection.SELL),
        ]
    )
    assert [c.symbol for c in shorts] == ["A", "B"]  # weaker RS ranks higher for a sell
    by_liquidity = CandidateRankingEngine().rank(
        [make("A", 80, liquidity=1e5), make("B", 80, liquidity=9e6)]
    )
    assert [c.symbol for c in by_liquidity] == ["B", "A"]


def test_top_candidates_exclude_ignore_and_respect_top_n() -> None:
    engine = CandidateRankingEngine(RankingConfig(top_n=2))
    ranked = engine.rank([make("A", 95), make("B", 60), make("C", 88), make("D", 75)])
    assert [c.symbol for c in engine.top(ranked)] == ["A", "C"]
    everything = CandidateRankingEngine(RankingConfig(top_n=10, min_category_for_top="IGNORE"))
    assert len(everything.top(everything.rank([make("A", 95), make("B", 60)]))) == 2


def test_status_for() -> None:
    assert status_for(make("A", 86)) == "READY"
    assert status_for(make("A", 84)) == "WATCH"
    assert status_for(make("A", 50)) == "IGNORE"


# --- scanner -------------------------------------------------------------------------------


def test_scanner_rows_cover_every_dimension() -> None:
    contexts = {
        "NIFTY50": trend_long(),
        "AAA": strong_trend_long(),
        "BBB": mean_reversion_short(),
    }
    contexts["NIFTY50"].symbol = "NIFTY50"
    contexts["AAA"].symbol = "AAA"
    contexts["BBB"].symbol = "BBB"
    contexts["BBB"].regime = regime(MarketRegime.HIGH_VOLATILITY)
    router = StrategyRouter()
    evaluations = {s: router.evaluate(c) for s, c in contexts.items()}
    market = market_context_from(contexts, "NIFTY50", change_percent=0.4)
    engine = SignalEngine()
    signals = [s for s in engine.build(evaluations, contexts, market) if s.symbol != "NIFTY50"]
    ranking = CandidateRankingEngine()
    ranked = ranking.rank(signals)
    result = Nifty50Scanner("NIFTY50").scan(
        watchlist=["NIFTY50", "AAA", "BBB", "CCC"],
        contexts=contexts,
        evaluations=evaluations,
        ranked=ranked,
        top=ranking.top(ranked),
        market=market,
        snapshots={},
        names={"AAA": "Alpha Ltd."},
        now=datetime(2026, 1, 6, 9, 0, tzinfo=UTC),
        signals=signals,
    )
    assert result.filtered_out["NIFTY50"].startswith("index")
    assert result.market_context.available and result.market_context.change_percent == 0.4
    rows = {r.symbol: r for r in result.rows}
    assert set(rows) == {"AAA", "BBB", "CCC"}
    assert result.rows[0].symbol == "AAA"  # ranked rows first
    aaa = rows["AAA"]
    assert aaa.rank == 1 and aaa.status == ScanStatus.READY and aaa.name == "Alpha Ltd."
    assert aaa.signal_direction == "BUY" and aaa.signal_strategy == "TREND_CONTINUATION"
    for field in (
        "regime",
        "context_direction",
        "setup_direction",
        "entry_direction",
        "structure_trend",
        "price_vs_vwap",
        "trend_quality",
        "retracement_zone",
        "nearest_support",
        "relative_volume",
        "momentum_score",
        "relative_strength",
        "strategies",
    ):
        assert getattr(aaa, field) not in (None, []), field
    assert aaa.patterns and aaa.patterns[0].pattern == "BT"
    assert rows["BBB"].status == ScanStatus.NO_ROUTE  # high volatility routes nothing
    assert rows["CCC"].status == ScanStatus.NO_DATA
    assert result.status_counts["READY"] == 1 and result.symbols_scanned == 2
    assert result.top_candidates[0].symbol == "AAA"
    assert [s.symbol for s in result.signals] == ["AAA"]
    assert result.signals[0].breakdown.components  # full breakdown available without storage
    assert "not proof of profitability" in result.ranking_note


def test_market_context_unavailable_without_the_index() -> None:
    market = market_context_from({}, "NIFTY50")
    assert not market.available
    signal = signal_for(trend_long(), market=market)
    alignment = next(c for c in signal.breakdown.components if c.key == "market_alignment")
    assert alignment.applicable  # the symbol's own timeframe alignment still counts
    assert "NIFTY" not in alignment.detail


def test_signal_ids_flow_into_candidates() -> None:
    signal = signal_for(strong_trend_long())
    assert signal.id is None and signal.key == ("TEST", "TREND_CONTINUATION", "BUY")
    assert signal.strategy == StrategyName.TREND_CONTINUATION
