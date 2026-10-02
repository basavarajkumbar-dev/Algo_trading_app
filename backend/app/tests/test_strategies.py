"""Trend continuation, breakout momentum and mean reversion strategies.

Each strategy: a clean baseline is eligible with exact candidates; every
required check is then broken on its own and must block the setup with
a reason naming it; the short side mirrors the long side.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from app.market_data.schemas import Timeframe
from app.market_intelligence.schemas import Relationship, Trend
from app.price_action.retracement import RetracementResult
from app.price_action.schemas import LevelType, PatternDirection, PatternType, RetracementZone
from app.regime.schemas import MarketRegime
from app.strategies.breakout_momentum import BreakoutMomentumStrategy
from app.strategies.config import BreakoutMomentumConfig, TrendContinuationConfig
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.opening_range import compute_opening_range
from app.strategies.schemas import StrategyName, StrategyResult, TradeDirection
from app.strategies.trend_continuation import TrendContinuationStrategy
from app.tests.candle_factory import candles_from_ohlc
from app.tests.strategy_scenarios import (
    breakout_long,
    level,
    mean_reversion_long,
    mean_reversion_short,
    mtf,
    pattern,
    quiet_rows,
    trend_long,
    trend_short,
)

REQUIRED_FIELDS = {
    "eligible",
    "direction",
    "entry_candidate",
    "stop_candidate",
    "target_candidate",
    "confidence",
    "reasons",
    "features",
}


def base_retracement() -> RetracementResult:
    retracement = trend_long().price_action.retracement
    assert retracement is not None
    return retracement


def blocked_by(result: StrategyResult, key: str) -> bool:
    return any(c.key == key and c.required and not c.passed for c in result.checks)


def assert_ineligible(result: StrategyResult) -> None:
    assert not result.eligible
    assert result.blockers
    assert result.entry_candidate is None and result.stop_candidate is None
    assert result.target_candidate is None
    assert result.confidence == 0.0


def assert_candidate_geometry(result: StrategyResult) -> None:
    """Long: stop < entry < target. Short: target < entry < stop."""

    assert result.eligible
    entry, stop, target = result.entry_candidate, result.stop_candidate, result.target_candidate
    assert entry is not None and stop is not None and target is not None
    if result.direction == TradeDirection.LONG:
        assert stop < entry < target
    else:
        assert target < entry < stop
    assert result.reward_risk == pytest.approx(abs(target - entry) / abs(entry - stop), abs=0.01)


# --- result contract ------------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy, ctx",
    [
        (TrendContinuationStrategy(), trend_long()),
        (BreakoutMomentumStrategy(), breakout_long()),
        (MeanReversionStrategy(), mean_reversion_short()),
    ],
    ids=["trend", "breakout", "mean_reversion"],
)
def test_every_strategy_returns_the_required_fields(strategy, ctx) -> None:
    result = strategy.evaluate(ctx)
    assert REQUIRED_FIELDS <= set(result.model_dump())
    assert result.eligible and result.reasons and result.features
    assert 0 < result.confidence <= 100
    assert_candidate_geometry(result)
    assert "not orders" in result.note
    assert result.stage == "ENTRY_CANDIDATE"


# --- trend continuation -----------------------------------------------------------------


def test_trend_long_candidates() -> None:
    result = TrendContinuationStrategy().evaluate(trend_long())
    assert result.direction == TradeDirection.LONG
    assert result.entry_candidate == 102.4  # above the confirmation candle's high
    assert result.stop_candidate == 99.8  # pullback low 100 minus 0.2 ATR
    assert result.target_candidate == 110.0  # impulse high, the nearest structure >= 1.5R
    assert result.reward_risk == pytest.approx(2.92, abs=0.01)
    assert result.target_basis == "Swing high (structure)"
    assert result.confidence == 100.0
    assert result.features["pullback_extreme"] == 100.0


def test_trend_short_mirrors_long() -> None:
    result = TrendContinuationStrategy().evaluate(trend_short())
    assert result.direction == TradeDirection.SHORT
    assert (result.entry_candidate, result.stop_candidate, result.target_candidate) == (
        97.6,
        100.2,
        90.0,
    )
    assert_candidate_geometry(result)


def test_trend_needs_a_trend() -> None:
    ctx = trend_long(regime=MarketRegime.RANGE, mtf=mtf(context=Trend.SIDEWAYS, context_bias=5))
    result = TrendContinuationStrategy().evaluate(ctx)
    assert_ineligible(result)
    assert result.direction == TradeDirection.NONE
    assert blocked_by(result, "trend")
    assert result.stage is None


def test_trend_falls_back_to_higher_timeframe_direction() -> None:
    result = TrendContinuationStrategy().evaluate(trend_long(regime=MarketRegime.UNKNOWN))
    assert result.eligible
    assert result.features["trend_source"] == "higher timeframe"
    assert not next(c for c in result.checks if c.key == "regime").passed  # weighted, not required
    assert result.confidence < 100


def test_trend_impulse_must_be_with_the_trend_and_fresh() -> None:
    against = trend_long(
        retracement=RetracementResult(
            detected=True,
            direction=Trend.DOWN,
            impulse_end=95.0,
            impulse_size=10.0,
            retracement_percentage=40.0,
            max_retracement_percentage=50.0,
            candles_since_extreme=5,
        )
    )
    assert blocked_by(TrendContinuationStrategy().evaluate(against), "impulse")

    stale_retracement = base_retracement().model_copy(update={"candles_since_extreme": 80})
    stale = TrendContinuationStrategy().evaluate(trend_long(retracement=stale_retracement))
    assert_ineligible(stale)
    assert any("candles old" in b for b in stale.blockers)


@pytest.mark.parametrize("deepest", [15.0, 80.0])
def test_trend_pullback_must_be_healthy(deepest: float) -> None:
    retracement = base_retracement().model_copy(
        update={"max_retracement_percentage": deepest, "retracement_percentage": min(deepest, 10.0)}
    )
    result = TrendContinuationStrategy().evaluate(trend_long(retracement=retracement))
    assert_ineligible(result)
    assert blocked_by(result, "pullback")
    assert result.stage == "IMPULSE"


def test_trend_needs_confirmation() -> None:
    ctx = trend_long(patterns=[], mtf=mtf(momentum_returning=False))
    result = TrendContinuationStrategy().evaluate(ctx)
    assert_ineligible(result)
    assert blocked_by(result, "confirmation")


def test_trend_momentum_turn_can_confirm_without_a_pattern() -> None:
    result = TrendContinuationStrategy().evaluate(trend_long(patterns=[]))
    assert result.eligible
    assert not next(c for c in result.checks if c.key == "pattern").passed


def test_trend_opposite_pattern_does_not_confirm() -> None:
    ctx = trend_long(
        patterns=[pattern(PatternType.TT, PatternDirection.BEARISH)],
        mtf=mtf(momentum_returning=False),
    )
    assert blocked_by(TrendContinuationStrategy().evaluate(ctx), "confirmation")


def test_support_ignores_levels_made_by_the_pullback_itself() -> None:
    close = 102.2
    own_levels = [
        level("Swing low", LevelType.SWING_LOW, 100.0, close),
        level("Retracement 50%", LevelType.RETRACEMENT, 100.0, close),
        level("Swing high", LevelType.SWING_HIGH, 110.0, close),
    ]
    result = TrendContinuationStrategy().evaluate(trend_long(levels=own_levels))
    support = next(c for c in result.checks if c.key == "support")
    assert not support.passed
    assert result.eligible  # support adds confidence; it is not required
    assert result.confidence < 100


def test_trend_stop_too_wide_blocks() -> None:
    retracement = base_retracement().model_copy(update={"max_retracement_percentage": 64.0})
    # deepest pullback 110 - 0.64 * 20 = 97.2, stop 97.0: 5.4 ATR below the 102.4 entry
    result = TrendContinuationStrategy().evaluate(trend_long(retracement=retracement))
    assert_ineligible(result)
    assert any("Stop is" in b and "max 3 ATR" in b for b in result.blockers)


def test_trend_target_falls_back_to_multiple_of_risk() -> None:
    close = 102.2
    result = TrendContinuationStrategy().evaluate(
        trend_long(levels=[level("EMA 20", LevelType.EMA, 100.3, close)])
    )
    risk = 102.4 - 99.8
    assert result.target_candidate == pytest.approx(102.4 + 2 * risk, abs=0.01)
    assert result.target_basis is not None and "2R" in result.target_basis


def test_trend_rejects_structure_too_close_and_uses_next_level() -> None:
    close = 102.2
    levels = [
        level("Day high", LevelType.INTRADAY_HIGH, 104.0, close),  # 0.6R: too close
        level("Prev day high", LevelType.PREVIOUS_DAY_HIGH, 107.0, close),  # 1.77R
        level("EMA 20", LevelType.EMA, 100.3, close),
    ]
    result = TrendContinuationStrategy().evaluate(trend_long(levels=levels))
    assert result.target_candidate == 107.0
    assert result.features["first_obstacle"] == "Day high"
    assert result.features["first_obstacle_rr"] == pytest.approx(0.62, abs=0.01)


def test_trend_thresholds_are_configurable() -> None:
    retracement = base_retracement().model_copy(
        update={"max_retracement_percentage": 20.0, "retracement_percentage": 10.0}
    )
    strict = TrendContinuationStrategy().evaluate(trend_long(retracement=retracement))
    relaxed = TrendContinuationStrategy(
        TrendContinuationConfig(min_pullback_percent=15.0)
    ).evaluate(trend_long(retracement=retracement))
    assert not strict.eligible and relaxed.eligible


# --- breakout momentum --------------------------------------------------------------------


def test_breakout_long_candidates() -> None:
    result = BreakoutMomentumStrategy().evaluate(breakout_long())
    assert result.direction == TradeDirection.LONG
    assert result.features["reference_range"] == "consolidation"
    assert result.features["breakout_level"] == 101.0
    assert result.entry_candidate == 101.4  # breakout close
    assert result.stop_candidate == 100.7  # below the breakout candle low 100.9, minus 0.2
    assert result.target_candidate == 103.0  # measured move: level 101 + range height 2
    assert result.target_basis is not None and "measured move" in result.target_basis
    assert result.features["compression_patterns"] == "NR"


def test_breakout_short_mirrors_long() -> None:
    result = BreakoutMomentumStrategy().evaluate(
        breakout_long(
            breakout=(99.2, 99.1, 98.5, 98.6),
            analysis={"momentum": 34.0, "price_vs_vwap": Relationship.BELOW, "vwap": 100.5},
            mtf=mtf(context=Trend.DOWN, context_bias=-40.0),
        )
    )
    assert result.direction == TradeDirection.SHORT
    assert result.features["breakout_level"] == 99.0
    assert_candidate_geometry(result)


def test_breakout_needs_a_close_beyond_the_range() -> None:
    result = BreakoutMomentumStrategy().evaluate(
        breakout_long(breakout=(100.6, 100.95, 100.4, 100.9))
    )
    assert_ineligible(result)
    assert blocked_by(result, "range") and blocked_by(result, "breakout")


def test_breakout_rejects_chasing() -> None:
    result = BreakoutMomentumStrategy().evaluate(
        breakout_long(breakout=(101.6, 103.2, 101.5, 103.0))
    )
    assert_ineligible(result)
    assert blocked_by(result, "not_extended")
    assert result.features["extension_atr"] == 2.0


def test_breakout_needs_volume() -> None:
    result = BreakoutMomentumStrategy().evaluate(breakout_long(volume=1100))
    assert_ineligible(result)
    assert blocked_by(result, "volume")


def test_wide_range_is_not_a_consolidation() -> None:
    wide = quiet_rows(40, 100.0, half_range=3.0)  # 6 ATR wide
    result = BreakoutMomentumStrategy().evaluate(
        breakout_long(rows=wide, breakout=(102.6, 103.4, 102.5, 103.3))
    )
    assert_ineligible(result)
    assert result.features["consolidation_width_atr"] == 6.0
    assert blocked_by(result, "range")


def test_breakout_from_the_opening_range() -> None:
    """A range too wide for a consolidation, but a 15m opening range
    (09:15-09:30 IST) whose high is broken at 10:00."""

    session_open = datetime(2026, 1, 6, 3, 45, tzinfo=UTC)  # 09:15 IST
    rows = quiet_rows(3, 100.0, half_range=0.4)  # ORB 99.6 - 100.4
    rows += quiet_rows(3, 100.0, half_range=0.3)
    wide_history = quiet_rows(40, 100.0, half_range=3.0)
    result = BreakoutMomentumStrategy().evaluate(
        breakout_long(
            rows=[*wide_history, *rows],
            breakout=(100.3, 100.9, 100.2, 100.8),
            start=session_open - timedelta(minutes=5 * 40),
        )
    )
    assert result.features["opening_range_available"] is True
    assert (result.features["opening_range_low"], result.features["opening_range_high"]) == (
        99.6,
        100.4,
    )
    assert result.features["reference_range"] == "opening range"
    assert result.features["breakout_level"] == 100.4
    assert next(c for c in result.checks if c.key == "opening_range").passed
    assert result.eligible


@pytest.mark.parametrize(("minutes", "expected_high"), [(5, 100.4), (15, 100.6), (30, 100.9)])
def test_opening_range_minutes_are_configurable(minutes: int, expected_high: float) -> None:
    session_open = datetime(2026, 1, 6, 3, 45, tzinfo=UTC)
    highs = [100.4, 100.5, 100.6, 100.7, 100.8, 100.9, 101.5, 101.6]
    rows = [(100.0, h, 99.5, 100.1) for h in highs]
    orb = compute_opening_range(candles_from_ohlc(rows, start=session_open), minutes=minutes)
    assert orb.available
    assert orb.high == expected_high and orb.low == 99.5
    assert orb.candles == minutes // 5


def test_opening_range_not_available_before_it_closes() -> None:
    session_open = datetime(2026, 1, 6, 3, 45, tzinfo=UTC)
    candles = candles_from_ohlc(quiet_rows(2), start=session_open)  # 09:15, 09:20
    orb = compute_opening_range(candles, minutes=15)
    assert not orb.available and "has not closed" in orb.reason
    coarse = candles_from_ohlc(quiet_rows(4), start=session_open, timeframe=Timeframe.M15)
    assert "cannot be built" in compute_opening_range(coarse, minutes=5).reason


def test_opening_range_session_start_is_configurable() -> None:
    start = datetime(2026, 1, 6, 4, 0, tzinfo=UTC)  # 09:30 IST
    candles = candles_from_ohlc(quiet_rows(6), start=start)
    default = compute_opening_range(candles, minutes=15)  # 09:15 open: no candles in its window
    assert not default.available and "Only 0 of 3" in default.reason
    orb = compute_opening_range(candles, minutes=15, session_start=time(9, 30))
    assert (
        orb.available and orb.start is not None and orb.start.hour == 9 and orb.start.minute == 30
    )


def test_opening_range_minutes_must_be_a_multiple_of_five() -> None:
    with pytest.raises(ValidationError):
        BreakoutMomentumConfig(opening_range_minutes=7)
    for minutes in (5, 15, 30):
        assert (
            BreakoutMomentumConfig(opening_range_minutes=minutes).opening_range_minutes == minutes
        )


# --- mean reversion -----------------------------------------------------------------------


def test_mean_reversion_short_candidates() -> None:
    result = MeanReversionStrategy().evaluate(mean_reversion_short())
    assert result.direction == TradeDirection.SHORT
    assert result.entry_candidate == 101.0  # below the reversal candle's low
    assert result.stop_candidate == 102.3  # above the range high / candle high 102.1 + 0.2
    assert result.target_candidate == 99.5  # VWAP
    assert result.target_basis == "VWAP (the mean)"
    assert result.reward_risk == pytest.approx(1.15, abs=0.01)


def test_mean_reversion_long_mirrors_short() -> None:
    result = MeanReversionStrategy().evaluate(mean_reversion_long())
    assert result.direction == TradeDirection.LONG
    assert result.target_candidate == 100.5
    assert_candidate_geometry(result)


@pytest.mark.parametrize(
    "analysis, detail",
    [
        ({"trend_quality": 75.0}, "trend quality"),
        ({"efficiency": 0.7}, "efficiency"),
    ],
)
def test_mean_reversion_refuses_strong_trends(analysis: dict, detail: str) -> None:
    result = MeanReversionStrategy().evaluate(mean_reversion_short(analysis=analysis))
    assert_ineligible(result)
    assert blocked_by(result, "not_trending")
    assert any(detail in b and "not faded" in b for b in result.blockers)


def test_mean_reversion_refuses_strong_higher_timeframe_trend() -> None:
    result = MeanReversionStrategy().evaluate(
        mean_reversion_short(mtf=mtf(context=Trend.UP, context_bias=70.0))
    )
    assert_ineligible(result)
    assert blocked_by(result, "not_trending")


def test_mean_reversion_refuses_a_broken_range() -> None:
    close = 101.1
    levels = [
        level("Breakout level", LevelType.BREAKOUT_LEVEL, 100.8, close),
        level("Range low", LevelType.RANGE_LOW, 98.0, close),
    ]
    result = MeanReversionStrategy().evaluate(mean_reversion_short(levels=levels))
    assert_ineligible(result)
    assert blocked_by(result, "range_intact")


def test_mean_reversion_needs_price_at_a_boundary() -> None:
    result = MeanReversionStrategy().evaluate(
        mean_reversion_short(latest=(100.2, 100.6, 99.8, 100.1))
    )
    assert_ineligible(result)
    assert blocked_by(result, "at_boundary")
    assert result.direction == TradeDirection.NONE


def test_mean_reversion_needs_a_reversal() -> None:
    result = MeanReversionStrategy().evaluate(mean_reversion_short(patterns=[]))
    assert_ineligible(result)
    assert blocked_by(result, "reversal")
    assert result.stage == "EXTREME"


def test_mean_reversion_needs_stretch_from_vwap() -> None:
    result = MeanReversionStrategy().evaluate(mean_reversion_short(analysis={"vwap": 100.9}))
    assert_ineligible(result)
    assert blocked_by(result, "vwap_deviation")


def test_mean_reversion_needs_room_to_the_mean() -> None:
    # Stretched enough (0.55 ATR) but VWAP only 0.45 below the entry: 0.35R.
    result = MeanReversionStrategy().evaluate(mean_reversion_short(analysis={"vwap": 100.55}))
    assert_ineligible(result)
    assert any("Target offers" in b for b in result.blockers)


# --- robustness --------------------------------------------------------------------------


def test_a_failing_strategy_returns_an_ineligible_result(monkeypatch) -> None:
    strategy = TrendContinuationStrategy()

    def boom(ctx):
        raise RuntimeError("bad input")

    monkeypatch.setattr(strategy, "analyze", boom)
    result = strategy.evaluate(trend_long())
    assert_ineligible(result)
    assert result.strategy == StrategyName.TREND_CONTINUATION
    assert "RuntimeError: bad input" in result.blockers[0]


def test_missing_engine_outputs_do_not_crash() -> None:
    ctx = trend_long()
    ctx.analysis = ctx.analysis.model_copy(
        update={"vwap": None, "momentum": None, "trend_quality": None, "relative_strength": None}
    )
    ctx.price_action = ctx.price_action.model_copy(
        update={"multi_timeframe": None, "support_resistance": None}
    )
    for strategy in (
        TrendContinuationStrategy(),
        BreakoutMomentumStrategy(),
        MeanReversionStrategy(),
    ):
        result = strategy.evaluate(ctx)
        assert not any(b.startswith("Strategy error") for b in result.blockers)


def test_zone_input_does_not_drive_eligibility_alone() -> None:
    """Eligibility is decided by the measured pullback depth, not by a
    zone label alone."""

    retracement = base_retracement().model_copy(update={"zone": RetracementZone.SHALLOW})
    assert TrendContinuationStrategy().evaluate(trend_long(retracement=retracement)).eligible
