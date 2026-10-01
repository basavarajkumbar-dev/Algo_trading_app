"""Swing structure, retracement and support/resistance engines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.market_data.candle_builder import aggregate_candles
from app.market_data.schemas import Candle, Timeframe
from app.market_intelligence.schemas import InsufficientDataError, Trend
from app.price_action.config import LevelConfig, RetracementConfig
from app.price_action.retracement import RetracementQuality, analyze_retracement, zone_for
from app.price_action.schemas import LevelType, RetracementZone
from app.price_action.support_resistance import (
    LevelRole,
    analyze_support_resistance,
    reference_levels,
    score_level,
)
from app.price_action.swing_structure import (
    PivotType,
    analyze_swing_structure,
    legs_from_pivots,
    zigzag_pivots,
)
from app.tests.candle_factory import candles_from_ohlc


def path_candles(closes: list[float], **kwargs) -> list[Candle]:
    """No wicks: each candle spans exactly open..close, so swing prices
    are the closes themselves."""

    rows = []
    previous = closes[0]
    for close in closes[1:]:
        rows.append((previous, max(previous, close), min(previous, close), close))
        previous = close
    return candles_from_ohlc(rows, **kwargs)


def walk(start: float, steps: list[tuple[float, int]]) -> list[float]:
    """Closes from `start`, moving by each (step, count)."""

    closes = [start]
    for step, count in steps:
        for _ in range(count):
            closes.append(round(closes[-1] + step, 6))
    return closes


QUIET = [(0.5, 1), (-0.5, 1)] * 8  # 16 candles of +/-0.5 around the start


# --- zig-zag swings -----------------------------------------------------------------


def test_zigzag_confirms_swings_beyond_threshold() -> None:
    candles = path_candles(walk(100.0, [(1.0, 10), (-1.0, 4), (1.0, 6)]))
    pivots = zigzag_pivots(candles, threshold=2.0)
    assert [p.type for p in pivots] == [
        PivotType.LOW,
        PivotType.HIGH,
        PivotType.LOW,
        PivotType.HIGH,
    ]
    assert [p.price for p in pivots] == [100.0, 110.0, 106.0, 112.0]
    assert [p.confirmed for p in pivots] == [True, True, True, False]  # last one is provisional


def test_zigzag_ignores_moves_below_threshold() -> None:
    candles = path_candles(walk(100.0, [(1.0, 10), (-1.0, 1), (1.0, 2)]))
    pivots = zigzag_pivots(candles, threshold=2.0)
    assert [p.price for p in pivots] == [100.0, 111.0]  # the 1-point dip never confirms


def test_legs_between_pivots() -> None:
    candles = path_candles(walk(100.0, [(1.0, 10), (-1.0, 4), (1.0, 6)]))
    legs = legs_from_pivots(zigzag_pivots(candles, threshold=2.0), atr=1.0)
    assert [(leg.direction, leg.size) for leg in legs] == [
        (Trend.UP, 10.0),
        (Trend.DOWN, 4.0),
        (Trend.UP, 6.0),
    ]
    assert [leg.complete for leg in legs] == [True, True, False]


def test_swing_structure_reuses_phase4_structure() -> None:
    candles = path_candles(walk(100.0, [*QUIET, (0.5, 20), (-0.5, 6), (0.5, 10)]))
    result = analyze_swing_structure(candles)
    assert result.atr == pytest.approx(0.5)
    assert result.reversal_threshold == pytest.approx(0.75)
    assert result.latest_swing_high is not None
    assert result.structure is not None
    assert result.current_leg is not None and result.current_leg.direction == Trend.UP


def test_swing_structure_needs_data() -> None:
    with pytest.raises(InsufficientDataError):
        analyze_swing_structure(path_candles(walk(100.0, [(1.0, 5)])))


# --- retracement --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percentage", "zone"),
    [
        (0.0, RetracementZone.SHALLOW),
        (39.9, RetracementZone.SHALLOW),
        (40.0, RetracementZone.HEALTHY_PULLBACK),
        (49.9, RetracementZone.HEALTHY_PULLBACK),
        (50.0, RetracementZone.DEEP_PULLBACK),
        (60.0, RetracementZone.WEAKENING),
        (79.9, RetracementZone.WEAKENING),
        (80.0, RetracementZone.MAJOR_WEAKNESS),
        (99.9, RetracementZone.MAJOR_WEAKNESS),
        (100.0, RetracementZone.INVALIDATION_CONTEXT),
        (140.0, RetracementZone.INVALIDATION_CONTEXT),
    ],
)
def test_retracement_zones(percentage: float, zone: RetracementZone) -> None:
    assert zone_for(percentage, RetracementConfig().zone_bounds) == zone


def up_impulse_then(pullback_steps: int, bounce_steps: int = 0) -> list[Candle]:
    """Quiet trade at 100, an impulse +10 to 110 in 0.5 steps, a pullback
    of `pullback_steps` x 0.5, then an optional bounce."""

    return path_candles(
        walk(100.0, [*QUIET, (0.5, 20), (-0.5, pullback_steps), (0.5, bounce_steps)])
    )


def test_healthy_pullback_in_up_impulse() -> None:
    result = analyze_retracement(up_impulse_then(9))  # back to 105.5
    assert result.detected
    assert result.direction == Trend.UP
    assert result.swing_low == 100.0 and result.swing_high == 110.0
    assert result.retracement_percentage == pytest.approx(45.0)
    assert result.zone == RetracementZone.HEALTHY_PULLBACK
    assert result.quality == RetracementQuality.HEALTHY
    assert {lvl.percent: lvl.price for lvl in result.levels} == {
        40.0: 106.0,
        50.0: 105.0,
        60.0: 104.0,
        80.0: 102.0,
        100.0: 100.0,
    }
    assert result.impulse_size_atr == pytest.approx(20.0)
    assert 0 <= result.strength <= 100 and 0 <= result.confidence <= 100
    assert "not guaranteed" in result.note


def test_largest_leg_is_the_impulse_not_the_pullback() -> None:
    # The 4.5-point pullback is itself 9 ATR, but the 10-point move is larger.
    result = analyze_retracement(up_impulse_then(9))
    assert result.impulse_size == pytest.approx(10.0)


def test_deepest_pullback_is_tracked_separately() -> None:
    result = analyze_retracement(up_impulse_then(10, bounce_steps=4))  # 105 then 107
    assert result.retracement_percentage == pytest.approx(30.0)
    assert result.max_retracement_percentage == pytest.approx(50.0)
    assert result.zone == RetracementZone.SHALLOW
    assert result.max_zone == RetracementZone.DEEP_PULLBACK


def full_retracement() -> list[Candle]:
    """100 -> 110, then down to 104, a bounce to 106 and down to 99.
    Each down leg is smaller than the impulse, so the impulse stays the
    reference and price is now beyond its start."""

    return path_candles(walk(100.0, [*QUIET, (0.5, 20), (-0.5, 12), (0.5, 4), (-0.5, 14)]))


def test_largest_leg_becomes_the_impulse_when_a_move_is_reversed_in_one_leg() -> None:
    # 100 -> 110 -> 99.5 in one leg: the 10.5-point decline is now the
    # largest move, so it is the impulse and price sits at its extreme.
    result = analyze_retracement(up_impulse_then(21))
    assert result.direction == Trend.DOWN
    assert result.retracement_percentage == pytest.approx(0.0)


def test_full_retracement_is_invalidation_context() -> None:
    result = analyze_retracement(full_retracement())
    assert result.direction == Trend.UP
    assert result.retracement_percentage == pytest.approx(110.0)
    assert result.zone == RetracementZone.INVALIDATION_CONTEXT
    assert result.quality == RetracementQuality.INVALIDATED


def test_down_impulse_retraces_upward() -> None:
    quiet_below = [(-0.5, 1), (0.5, 1)] * 8  # oscillates 109.5..110
    candles = path_candles(walk(110.0, [*quiet_below, (-0.5, 20), (0.5, 12)]))  # 110 -> 100 -> 106
    result = analyze_retracement(candles)
    assert result.direction == Trend.DOWN
    assert result.swing_high == 110.0 and result.swing_low == 100.0
    assert result.retracement_percentage == pytest.approx(60.0)
    assert result.zone == RetracementZone.WEAKENING
    assert {lvl.percent: lvl.price for lvl in result.levels}[50.0] == 105.0


def test_no_impulse_in_quiet_market() -> None:
    result = analyze_retracement(path_candles(walk(100.0, QUIET * 3)))
    assert not result.detected
    assert "No impulse" in result.reasons[0]


def test_healthy_pullback_is_more_confident_than_invalidation() -> None:
    healthy = analyze_retracement(up_impulse_then(9))
    invalid = analyze_retracement(full_retracement())
    assert healthy.confidence > invalid.confidence


# --- support / resistance -------------------------------------------------------------

IST_MIDNIGHT_JAN5 = datetime(2026, 1, 4, 18, 30, tzinfo=UTC)  # 2026-01-05 00:00 IST


def two_day_candles() -> list[Candle]:
    """60 candles on 4 Jan (IST) then 60 on 5 Jan, quiet around 100,
    with known extremes: 4 Jan high 103 / low 97 / close 100.25, and
    5 Jan high 101.5 / low 98.6. The last candle closes at 100.0."""

    start = IST_MIDNIGHT_JAN5 - timedelta(minutes=5 * 60)
    rows = []
    for i in range(120):
        rows.append((100.0, 100.5, 99.75, 100.25) if i % 2 == 0 else (100.25, 100.5, 99.75, 100.0))
    rows[20] = (100.0, 103.0, 99.75, 100.25)  # 4 Jan high
    rows[40] = (100.0, 100.5, 97.0, 100.25)  # 4 Jan low
    rows[59] = (100.0, 100.5, 99.75, 100.25)  # 4 Jan close
    rows[80] = (100.0, 101.5, 99.75, 100.25)  # 5 Jan high
    rows[100] = (100.0, 100.5, 98.6, 100.25)  # 5 Jan low
    rows[119] = (100.25, 100.5, 99.75, 100.0)
    return candles_from_ohlc(rows, start=start)


def by_type(result, level_type: LevelType):
    return [lvl for lvl in result.levels if lvl.type == level_type]


def test_day_levels_use_ist_calendar_days() -> None:
    candles = two_day_candles()
    assert candles[59].timestamp == IST_MIDNIGHT_JAN5 - timedelta(minutes=5)  # 23:55 IST
    result = analyze_support_resistance(candles)
    assert str(result.session_date) == "2026-01-05"
    assert str(result.previous_session_date) == "2026-01-04"
    assert by_type(result, LevelType.PREVIOUS_DAY_HIGH)[0].price == 103.0
    assert by_type(result, LevelType.PREVIOUS_DAY_LOW)[0].price == 97.0
    assert by_type(result, LevelType.PREVIOUS_CLOSE)[0].price == 100.25
    assert by_type(result, LevelType.INTRADAY_HIGH)[0].price == 101.5
    assert by_type(result, LevelType.INTRADAY_LOW)[0].price == 98.6
    assert by_type(result, LevelType.PREVIOUS_DAY_HIGH)[0].timeframe == "1d"


def test_previous_day_missing_is_reported() -> None:
    candles = two_day_candles()[60:]
    result = analyze_support_resistance(candles)
    assert "previous_day" in result.unavailable
    assert not by_type(result, LevelType.PREVIOUS_DAY_HIGH)


def test_level_roles_and_nearest_levels() -> None:
    result = analyze_support_resistance(two_day_candles())
    for level in result.levels:
        expected = LevelRole.SUPPORT if level.price <= result.price else LevelRole.RESISTANCE
        assert level.role == expected
    assert result.nearest_support is not None and result.nearest_resistance is not None
    supports = [lvl.price for lvl in result.levels if lvl.role == LevelRole.SUPPORT]
    assert result.nearest_support.price == max(supports)
    assert [lvl.price for lvl in result.levels] == sorted(
        (lvl.price for lvl in result.levels), reverse=True
    )
    assert all(0 <= lvl.strength <= 100 for lvl in result.levels)


def test_dynamic_levels_are_flagged() -> None:
    result = analyze_support_resistance(two_day_candles())
    vwap = by_type(result, LevelType.VWAP)
    ema = [lvl for lvl in result.levels if lvl.label == "EMA 20"]
    assert vwap and vwap[0].dynamic
    assert ema and ema[0].dynamic
    assert "ma_200" in result.unavailable  # only 120 candles


def test_touch_count_counts_episodes() -> None:
    rows = [(100.0, 100.4, 99.6, 100.2)] * 30
    for i in (5, 6, 7, 15, 25):  # one 3-candle touch, then two single touches
        rows[i] = (100.0, 102.0, 99.6, 100.2)
    rows[-1] = (100.0, 100.4, 99.6, 100.0)
    config = LevelConfig(ema_periods=(), ma_periods=(), swing_levels=0, range_window=3)
    result = analyze_support_resistance(candles_from_ohlc(rows), config=config)
    day_high = by_type(result, LevelType.INTRADAY_HIGH)[0]
    assert day_high.price == 102.0
    assert day_high.touch_count == 3
    assert day_high.recency == 4  # last touch at index 25 of 30


def test_breakout_level_replaces_range_high() -> None:
    rows = [(100.0, 100.5, 99.5, 100.2), (100.2, 100.5, 99.5, 100.0)] * 15
    rows.append((100.0, 101.6, 99.9, 101.5))  # closes above the 20-candle high
    result = analyze_support_resistance(candles_from_ohlc(rows))
    breakout = by_type(result, LevelType.BREAKOUT_LEVEL)
    assert breakout and breakout[0].price == 100.5
    assert breakout[0].role == LevelRole.SUPPORT
    assert not by_type(result, LevelType.RANGE_HIGH)
    assert by_type(result, LevelType.RANGE_LOW)


def test_confluence_lists_nearby_levels() -> None:
    rows = [(100.0, 100.5, 99.5, 100.2), (100.2, 100.5, 99.5, 100.0)] * 15
    rows.append((100.0, 100.4, 99.6, 100.1))
    result = analyze_support_resistance(candles_from_ohlc(rows))
    range_high = by_type(result, LevelType.RANGE_HIGH)[0]
    day_high = by_type(result, LevelType.INTRADAY_HIGH)[0]
    assert range_high.price == day_high.price == 100.5
    assert "Day high" in range_high.confluence
    assert "Range high" in day_high.confluence


def test_retracement_levels_are_included_below_100() -> None:
    candles = path_candles(walk(100.0, [*QUIET, (0.5, 20), (-0.5, 9)]))
    retracement = analyze_retracement(candles)
    result = analyze_support_resistance(candles, retracement=retracement)
    labels = {lvl.label for lvl in result.levels if lvl.type == LevelType.RETRACEMENT}
    assert labels == {"Retracement 40%", "Retracement 50%", "Retracement 60%", "Retracement 80%"}


def test_support_resistance_needs_data() -> None:
    with pytest.raises(InsufficientDataError):
        analyze_support_resistance(path_candles(walk(100.0, [(1.0, 5)])))


def test_reference_levels_for_pattern_context() -> None:
    result = analyze_support_resistance(two_day_candles())
    refs = reference_levels(result, min_strength=0)
    assert len(refs) == len(result.levels)
    assert refs[0].label == result.levels[0].label


# --- level strength score -----------------------------------------------------------


def test_strength_components_sum_to_score() -> None:
    score, points = score_level(LevelType.PREVIOUS_DAY_HIGH, "1d", 2, 10, 1, LevelConfig())
    assert score == pytest.approx(sum(points.values()), abs=0.2)
    assert set(points) == {"type", "touches", "confluence", "recency", "timeframe"}


def test_strength_rises_with_each_component() -> None:
    cfg = LevelConfig()
    base = score_level(LevelType.SWING_HIGH, "5m", 1, 30, 0, cfg)[0]
    assert score_level(LevelType.SWING_HIGH, "5m", 3, 30, 0, cfg)[0] > base  # touches
    assert score_level(LevelType.SWING_HIGH, "5m", 1, 2, 0, cfg)[0] > base  # recency
    assert score_level(LevelType.SWING_HIGH, "5m", 1, 30, 2, cfg)[0] > base  # confluence
    assert score_level(LevelType.SWING_HIGH, "60m", 1, 30, 0, cfg)[0] > base  # timeframe
    assert score_level(LevelType.PREVIOUS_DAY_HIGH, "5m", 1, 30, 0, cfg)[0] > base  # type


def test_touches_and_confluence_are_capped() -> None:
    cfg = LevelConfig()
    assert score_level(LevelType.VWAP, "5m", 4, 0, 3, cfg) == score_level(
        LevelType.VWAP, "5m", 40, 0, 30, cfg
    )


def test_strength_weights_are_configurable_and_normalised() -> None:
    default = LevelConfig()
    doubled = LevelConfig()
    for field in ("type", "touches", "confluence", "recency", "timeframe"):
        setattr(doubled.weights, field, getattr(default.weights, field) * 2)
    args = (LevelType.EMA, "15m", 2, 5, 1)
    assert score_level(*args, default)[0] == score_level(*args, doubled)[0]

    touches_only = LevelConfig()
    touches_only.weights.type = 0
    touches_only.weights.confluence = 0
    touches_only.weights.recency = 0
    touches_only.weights.timeframe = 0
    assert score_level(LevelType.EMA, "5m", 2, None, 0, touches_only)[0] == pytest.approx(50.0)


# --- aggregation used by the warm-up -------------------------------------------------


def test_aggregate_candles_complete_buckets_only() -> None:
    start = datetime(2026, 1, 5, 3, 50, tzinfo=UTC)  # mid-bucket for 15m
    rows = [(100.0 + i, 100.5 + i, 99.5 + i, 100.2 + i) for i in range(8)]
    candles = candles_from_ohlc(rows, volumes=[10 * (i + 1) for i in range(8)], start=start)
    fifteen = aggregate_candles(candles, Timeframe.M15)
    # 03:50, 03:55 belong to a partial 03:45 bucket; 04:00-04:10 and
    # 04:15-04:25 are complete; 04:30 alone is partial.
    assert [c.timestamp.strftime("%H:%M") for c in fifteen] == ["04:00", "04:15"]
    first = fifteen[0]
    assert first.open == 102.0 and first.close == 104.2
    assert first.high == 104.5 and first.low == 101.5
    assert first.volume == 30 + 40 + 50
    assert first.timeframe == Timeframe.M15 and first.is_closed


def test_aggregate_candles_rejects_impossible_timeframes() -> None:
    candles = candles_from_ohlc([(1.0, 1.0, 1.0, 1.0)], timeframe=Timeframe.M15)
    with pytest.raises(ValueError):
        aggregate_candles(candles, Timeframe.M5)
