"""Price action pattern detectors: COG, NR, NRB, WRB, BT, TT.

Each fixture is built candle by candle, so the expected detection
follows from the shape. Every detector gets at least one positive case,
one negative case (with the reason), and a context case.
"""

from __future__ import annotations

import pytest

from app.market_intelligence.schemas import Trend
from app.price_action.anatomy import PatternInputs, apply_context, candle_anatomy
from app.price_action.candle_patterns import DETECTORS, detect_patterns, scan_patterns
from app.price_action.changing_guard import detect_changing_guard
from app.price_action.config import PatternConfig
from app.price_action.narrow_range import detect_narrow_range, detect_narrow_range_body
from app.price_action.schemas import (
    CandleColor,
    LevelLocation,
    PatternDirection,
    PatternType,
    ReferenceLevel,
)
from app.price_action.tails import detect_bottoming_tail, detect_topping_tail
from app.price_action.wide_range import detect_wide_range_body
from app.tests.candle_factory import candles_from_ohlc

Row = tuple[float, float, float, float]


def base_rows(n: int = 25) -> list[Row]:
    """Alternating up/down candles around 100: every range is 1.0 and
    every body 0.5, so averages are exact. No trend."""

    rows: list[Row] = []
    for i in range(n):
        if i % 2 == 0:
            rows.append((100.0, 100.75, 99.75, 100.5))
        else:
            rows.append((100.5, 100.75, 99.75, 100.0))
    return rows


def declining_rows(n: int = 25, start: float = 110.0, step: float = 0.3) -> list[Row]:
    rows: list[Row] = []
    price = start
    for _ in range(n):
        close = price - step
        rows.append((price, price + 0.1, close - 0.1, close))
        price = close
    return rows


def rising_rows(n: int = 25, start: float = 90.0, step: float = 0.3) -> list[Row]:
    rows: list[Row] = []
    price = start
    for _ in range(n):
        close = price + step
        rows.append((price, close + 0.1, price - 0.1, close))
        price = close
    return rows


def last(detector, rows: list[Row], *, volumes=None, levels=None, config=None):
    candles = candles_from_ohlc(rows, volumes=volumes)
    return detector(PatternInputs(candles, config, levels), len(candles) - 1)


# --- anatomy ------------------------------------------------------------------------


def test_candle_anatomy_parts() -> None:
    candle = candles_from_ohlc([(100.0, 103.0, 99.0, 102.0)])[0]
    anatomy = candle_anatomy(candle)
    assert anatomy.range == 4.0
    assert anatomy.body == 2.0
    assert anatomy.upper_wick == 1.0
    assert anatomy.lower_wick == 1.0
    assert anatomy.close_position == 0.75
    assert anatomy.color == CandleColor.BULLISH


def test_small_body_is_a_doji() -> None:
    candle = candles_from_ohlc([(100.0, 101.0, 99.0, 100.05)])[0]
    assert candle_anatomy(candle).color == CandleColor.DOJI


# --- NR -------------------------------------------------------------------------------


def test_narrow_range_detected() -> None:
    result = last(detect_narrow_range, [*base_rows(), (100.1, 100.3, 99.9, 100.2)])
    assert result.detected
    assert result.pattern == PatternType.NR
    assert result.direction == PatternDirection.NEUTRAL
    assert result.strength == pytest.approx(60.0)  # range 0.4 of an average 1.0
    # base 50 + range <= 50% of average 15 + inside bar 15
    assert result.confidence == pytest.approx(80.0)
    assert result.metrics["inside_bar"] == 1.0
    assert any("narrowest" in r.lower() for r in result.reasons)


def test_narrow_range_not_detected_for_ordinary_candle() -> None:
    result = last(detect_narrow_range, [*base_rows(), (100.0, 100.75, 99.75, 100.5)])
    assert not result.detected
    assert result.confidence == 0.0 and result.strength == 0.0
    assert "not the narrowest" in result.reasons[0]


def test_nr4_without_nr7_is_reported_but_not_detected() -> None:
    rows = base_rows(22)
    rows += [(100.0, 100.15, 99.85, 100.1)]  # range 0.3, five candles back
    rows += base_rows(3)
    rows += [(100.0, 100.25, 99.75, 100.2)]  # range 0.5: narrowest of 4, not of 7
    result = last(detect_narrow_range, rows)
    assert not result.detected
    assert "NR4" in result.reasons[0]
    assert result.metrics["nr_short"] == 1.0


def test_detectors_need_history() -> None:
    result = last(detect_narrow_range, base_rows(5))
    assert not result.detected
    assert result.reasons[0].startswith("Needs")


# --- NRB ------------------------------------------------------------------------------


def test_narrow_range_body_detected() -> None:
    result = last(detect_narrow_range_body, [*base_rows(), (100.2, 100.6, 99.8, 100.25)])
    assert result.detected
    assert result.pattern == PatternType.NRB
    assert result.direction == PatternDirection.NEUTRAL
    assert result.strength == pytest.approx(90.0)  # body 0.05 vs average 0.5
    # base 50 + range below average 15 + balanced wicks 5
    assert result.confidence == pytest.approx(70.0)


def test_narrow_range_body_rejects_full_body() -> None:
    result = last(detect_narrow_range_body, [*base_rows(), (100.0, 100.75, 99.75, 100.5)])
    assert not result.detected
    assert any("of the range" in r for r in result.reasons)


# --- WRB ------------------------------------------------------------------------------


def test_bullish_wide_range_body() -> None:
    result = last(detect_wide_range_body, [*base_rows(), (100.0, 101.7, 99.95, 101.6)])
    assert result.detected
    assert result.direction == PatternDirection.BULLISH
    assert result.metrics["body_vs_average"] == pytest.approx(3.2)
    assert result.strength == pytest.approx(88.0)  # (3.2 - 1) / (3.5 - 1)
    # base 55 + closed near the high 15 + closed above the 10-candle high 10
    assert result.confidence == pytest.approx(80.0)


def test_bearish_wide_range_body() -> None:
    result = last(detect_wide_range_body, [*base_rows(), (100.5, 100.55, 98.8, 98.9)])
    assert result.detected
    assert result.direction == PatternDirection.BEARISH
    assert any("below the low" in r for r in result.reasons)


def test_wide_range_body_volume_adds_confidence() -> None:
    rows = [*base_rows(), (100.0, 101.7, 99.95, 101.6)]
    quiet = last(detect_wide_range_body, rows)
    loud = last(detect_wide_range_body, rows, volumes=[1000] * 25 + [3000])
    assert loud.confidence == pytest.approx(quiet.confidence + 10)
    assert loud.context.relative_volume == pytest.approx(3.0)


def test_big_body_with_big_wicks_is_not_wrb() -> None:
    result = last(detect_wide_range_body, [*base_rows(), (100.0, 103.5, 97.5, 101.2)])
    assert not result.detected
    assert any("of the range" in r for r in result.reasons)


# --- BT / TT --------------------------------------------------------------------------


def test_bottoming_tail_detected() -> None:
    result = last(detect_bottoming_tail, [*base_rows(), (100.3, 100.5, 98.9, 100.45)])
    assert result.detected
    assert result.pattern == PatternType.BT
    assert result.direction == PatternDirection.BULLISH
    assert result.metrics["tail_ratio"] == pytest.approx(0.875)
    # base 50 + undercut previous low 5 + closed in the upper quarter 5
    assert result.confidence == pytest.approx(60.0)
    assert result.context.prior_trend == Trend.SIDEWAYS


def test_bottoming_tail_after_decline_gains_reversal_context() -> None:
    rows = [*declining_rows(), (102.4, 102.6, 101.0, 102.55)]
    result = last(detect_bottoming_tail, rows)
    assert result.detected
    assert result.context.prior_trend == Trend.DOWN
    assert any("Follows a decline" in r for r in result.reasons)
    assert result.confidence == pytest.approx(70.0)


def test_bottoming_tail_at_support() -> None:
    rows = [*base_rows(), (100.3, 100.5, 98.9, 100.45)]
    support = [ReferenceLevel(label="Prev day low", price=98.95)]
    result = last(detect_bottoming_tail, rows, levels=support)
    assert result.context.location == LevelLocation.AT_SUPPORT
    assert result.context.nearest_level is not None
    assert result.context.nearest_level.label == "Prev day low"
    assert result.confidence == pytest.approx(75.0)  # 60 + support 15


def test_bottoming_tail_rejects_topping_shape_and_tiny_candles() -> None:
    topping = last(detect_bottoming_tail, [*base_rows(), (99.7, 101.1, 99.5, 99.55)])
    assert not topping.detected
    tiny = last(detect_bottoming_tail, [*base_rows(), (100.15, 100.2, 100.0, 100.18)])
    assert not tiny.detected
    assert any("too small" in r for r in tiny.reasons)


def test_topping_tail_detected_after_advance() -> None:
    rows = [*rising_rows(), (97.6, 99.0, 97.4, 97.45)]
    result = last(detect_topping_tail, rows)
    assert result.detected
    assert result.pattern == PatternType.TT
    assert result.direction == PatternDirection.BEARISH
    assert result.context.prior_trend == Trend.UP
    assert any("Follows a advance" in r or "advance" in r for r in result.reasons)


def test_topping_tail_rejects_bottoming_shape() -> None:
    result = last(detect_topping_tail, [*base_rows(), (100.3, 100.5, 98.9, 100.45)])
    assert not result.detected


# --- COG ------------------------------------------------------------------------------

BEARISH_RUN: list[Row] = [
    (100.5, 100.6, 99.9, 100.0),
    (100.0, 100.1, 99.4, 99.5),
    (99.5, 99.6, 98.9, 99.0),
    (99.0, 99.1, 98.4, 98.5),
]
BULLISH_RUN: list[Row] = [
    (99.5, 100.1, 99.4, 100.0),
    (100.0, 100.6, 99.9, 100.5),
    (100.5, 101.1, 100.4, 101.0),
    (101.0, 101.6, 100.9, 101.5),
]


def test_bullish_changing_of_the_guard() -> None:
    rows = [*base_rows(21), *BEARISH_RUN, (98.5, 99.35, 98.45, 99.3)]
    result = last(detect_changing_guard, rows)
    assert result.detected
    assert result.pattern == PatternType.COG
    assert result.direction == PatternDirection.BULLISH
    assert result.metrics["run_length"] == 4
    assert result.metrics["run_extremes_taken"] == 1  # only the last run high
    assert result.context.prior_trend == Trend.DOWN
    # base 55 + long run 10 + strong close 10 + reversal context 10
    assert result.confidence == pytest.approx(85.0)


def test_bearish_changing_of_the_guard() -> None:
    rows = [*base_rows(21), *BULLISH_RUN, (101.5, 101.55, 100.6, 100.65)]
    result = last(detect_changing_guard, rows)
    assert result.detected
    assert result.direction == PatternDirection.BEARISH


def test_cog_needs_a_long_enough_run() -> None:
    rows = [*base_rows(21), *BEARISH_RUN[-2:], (98.5, 99.35, 98.45, 99.3)]
    result = last(detect_changing_guard, rows)
    assert not result.detected
    assert any("Only" in r for r in result.reasons)


def test_cog_needs_close_beyond_previous_high() -> None:
    rows = [*base_rows(21), *BEARISH_RUN, (98.5, 99.0, 98.45, 98.95)]
    result = last(detect_changing_guard, rows)
    assert not result.detected
    assert any("previous candle's high" in r for r in result.reasons)


def test_cog_run_must_make_lower_highs_when_required() -> None:
    flat_run = [(100.5, 100.6, 99.9, 100.0)] * 4  # bearish, but equal highs
    rows = [*base_rows(21), *flat_run, (100.0, 100.9, 99.95, 100.8)]
    assert not last(detect_changing_guard, rows).detected
    relaxed = PatternConfig()
    relaxed.changing_guard.require_progressive_run = False
    assert last(detect_changing_guard, rows, config=relaxed).detected


# --- context ----------------------------------------------------------------------------


def test_bullish_pattern_into_resistance_loses_confidence() -> None:
    rows = [*base_rows(), (100.0, 101.7, 99.95, 101.6)]
    free = last(detect_wide_range_body, rows)
    capped = last(
        detect_wide_range_body, rows, levels=[ReferenceLevel(label="Day high", price=101.9)]
    )
    assert capped.context.location == LevelLocation.AT_RESISTANCE
    assert capped.confidence == pytest.approx(free.confidence - 10)


def test_apply_context_neutral_pattern_at_level_gets_half_bonus() -> None:
    candles = candles_from_ohlc([*base_rows(), (100.1, 100.3, 99.9, 100.2)])
    inputs = PatternInputs(candles, levels=[ReferenceLevel(label="VWAP", price=100.1)])
    context = inputs.context(len(candles) - 1, PatternDirection.NEUTRAL)
    reasons: list[str] = []
    value = apply_context(50.0, PatternDirection.NEUTRAL, context, PatternConfig(), reasons)
    assert value == pytest.approx(57.5)
    assert reasons and "VWAP" in reasons[0]


def test_no_levels_means_no_location() -> None:
    result = last(detect_bottoming_tail, [*base_rows(), (100.3, 100.5, 98.9, 100.45)])
    assert result.context.location == LevelLocation.NO_LEVELS
    assert not result.context.levels_considered


# --- orchestration ----------------------------------------------------------------------


def test_detect_patterns_runs_every_detector_with_reasons() -> None:
    candles = candles_from_ohlc([*base_rows(), (100.0, 101.7, 99.95, 101.6)])
    results = detect_patterns(candles)
    assert [r.pattern for r in results] == list(DETECTORS)
    assert all(r.reasons for r in results)
    detected = {r.pattern for r in results if r.detected}
    assert PatternType.WRB in detected
    for r in results:
        assert 0 <= r.confidence <= 100 and 0 <= r.strength <= 100


def test_detection_does_not_use_later_candles() -> None:
    rows = [*base_rows(), (100.0, 101.7, 99.95, 101.6), *base_rows(10)]
    full = candles_from_ohlc(rows)
    index = 25
    with_future = detect_patterns(full, index=index)
    without_future = detect_patterns(full[: index + 1])
    assert [r.model_dump() for r in with_future] == [r.model_dump() for r in without_future]


def test_scan_patterns_marks_past_candles_without_levels() -> None:
    rows = [*base_rows(), (100.0, 101.7, 99.95, 101.6), *base_rows(4)]
    candles = candles_from_ohlc(rows)
    markers = scan_patterns(candles, lookback=10)
    wrb = [m for m in markers if m.pattern == PatternType.WRB]
    assert len(wrb) == 1
    assert wrb[0].timestamp == candles[25].timestamp
    assert wrb[0].direction == PatternDirection.BULLISH
    assert wrb[0].price == candles[25].low  # bullish markers sit under the candle


def test_not_detected_results_are_neutral_and_zero() -> None:
    candles = candles_from_ohlc(base_rows(30))
    for result in detect_patterns(candles):
        if not result.detected:
            assert result.direction == PatternDirection.NEUTRAL
            assert result.confidence == 0.0 and result.strength == 0.0
