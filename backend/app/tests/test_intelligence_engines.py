"""Unit tests for the market intelligence engines.

All inputs come from app.tests.candle_factory: closed-form shapes, so
every expectation follows from the shape of the data.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.market_data.schemas import Timeframe
from app.market_intelligence.analyzer import analyze_candles
from app.market_intelligence.factors import build_factors, vwap_score
from app.market_intelligence.momentum import analyze_momentum
from app.market_intelligence.moving_average import (
    MAAlignment,
    analyze_moving_averages,
    compute_ema_series,
    compute_sma_series,
)
from app.market_intelligence.range_analysis import analyze_range
from app.market_intelligence.relative_strength import analyze_relative_strength
from app.market_intelligence.schemas import (
    InsufficientDataError,
    Relationship,
    Trend,
    VolatilityLevel,
    VolumeTrend,
)
from app.market_intelligence.structure import (
    StructureEvent,
    StructureState,
    SwingLabel,
    SwingType,
    analyze_structure,
    classify_swings,
    find_swing_points,
)
from app.market_intelligence.trend_quality import compute_trend_quality
from app.market_intelligence.volatility import (
    analyze_volatility,
    compute_atr_series,
    compute_true_range_series,
)
from app.market_intelligence.volume import analyze_volume, relative_volume_score
from app.market_intelligence.vwap import analyze_vwap, compute_vwap_series
from app.tests.candle_factory import (
    breakout_candles,
    candles_from_closes,
    candles_from_ohlc,
    downtrend_closes,
    flat_closes,
    high_volatility_candles,
    range_closes,
    uptrend_closes,
    zigzag_closes,
)

# --- VWAP ---------------------------------------------------------------------


def test_vwap_series_is_volume_weighted_typical_price() -> None:
    candles = candles_from_ohlc(
        [(10, 12, 8, 10), (10, 16, 14, 15)],  # typical prices 10 and 15
        volumes=[100, 300],
    )
    series = compute_vwap_series(candles)
    assert series[0] == pytest.approx(10.0)
    assert series[1] == pytest.approx((10 * 100 + 15 * 300) / 400)


def test_vwap_with_no_volume_falls_back_to_close() -> None:
    candles = candles_from_ohlc([(10, 11, 9, 10.5), (10.5, 12, 10, 11)], volumes=[0, 0])
    assert compute_vwap_series(candles) == [10.5, 11]


def test_vwap_rising_series_price_above_and_slope_up() -> None:
    result = analyze_vwap(candles_from_closes([100 + i * 0.2 for i in range(40)]))
    assert result.price_vs_vwap == Relationship.ABOVE
    assert result.distance_from_vwap > 0
    assert result.vwap_slope_percent is not None and result.vwap_slope_percent > 0
    assert "Price" in result.reasons[0] and "above VWAP" in result.reasons[0]


def test_vwap_falling_series_price_below() -> None:
    result = analyze_vwap(candles_from_closes([100 - i * 0.2 for i in range(40)]))
    assert result.price_vs_vwap == Relationship.BELOW
    assert result.distance_from_vwap_percent < 0


def test_vwap_counts_crossings_in_choppy_market() -> None:
    result = analyze_vwap(candles_from_closes(zigzag_closes(30)))
    assert result.crossings >= 20
    assert result.recent_crossings >= 15
    assert result.candles_since_last_cross == 0


def test_vwap_needs_two_candles() -> None:
    with pytest.raises(InsufficientDataError):
        analyze_vwap(candles_from_closes([100.0]))


# --- Moving averages -------------------------------------------------------------


def test_sma_and_ema_series_known_values() -> None:
    assert compute_sma_series([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    ema = compute_ema_series([1, 2, 3, 4, 5], 3)
    assert ema[:2] == [None, None]
    assert ema[2] == pytest.approx(2.0)  # seeded with the SMA
    assert ema[3] == pytest.approx(3.0)  # (4 - 2) * 0.5 + 2
    assert ema[4] == pytest.approx(4.0)


def test_moving_averages_uptrend() -> None:
    result = analyze_moving_averages(candles_from_closes(uptrend_closes()))
    assert result.trend == Trend.UP
    assert result.alignment == MAAlignment.BULLISH
    assert result.price_vs_ema_fast == Relationship.ABOVE
    assert result.price_vs_ma == Relationship.ABOVE
    assert result.ema_fast_slope_percent and result.ema_fast_slope_percent > 0
    assert result.ema_slow_slope_percent and result.ema_slow_slope_percent > 0
    assert result.separation_percent and result.separation_percent > 0
    assert result.ma_trend_score > 60
    assert "Positive EMA slope" in result.reasons


def test_moving_averages_downtrend() -> None:
    result = analyze_moving_averages(candles_from_closes(downtrend_closes()))
    assert result.trend == Trend.DOWN
    assert result.alignment == MAAlignment.BEARISH
    assert "Negative EMA slope" in result.reasons


def test_moving_averages_flat_market_is_sideways_with_low_score() -> None:
    result = analyze_moving_averages(candles_from_closes(flat_closes(60)))
    assert result.trend == Trend.SIDEWAYS
    assert result.ma_trend_score < 20


def test_moving_averages_configurable_periods_and_missing_long_ma() -> None:
    result = analyze_moving_averages(
        candles_from_closes(uptrend_closes(60)), ema_fast_period=10, ema_slow_period=30
    )
    assert result.ema_fast_period == 10 and result.ema_slow_period == 30
    assert result.ma is None  # 200-period MA needs 200 candles
    assert result.price_vs_ma is None


def test_moving_averages_needs_fast_period_candles() -> None:
    with pytest.raises(InsufficientDataError):
        analyze_moving_averages(candles_from_closes(uptrend_closes(10)))


# --- Volatility -------------------------------------------------------------------


def test_true_range_includes_gaps_from_previous_close() -> None:
    candles = candles_from_ohlc([(100, 101, 99, 100), (105, 106, 104, 105)])
    assert compute_true_range_series(candles) == [2, 6]  # 106 - previous close 100


def test_atr_is_wilder_smoothed() -> None:
    candles = candles_from_ohlc([(10, 11, 9, 10)] * 3 + [(10, 14, 10, 13)])
    atr = compute_atr_series(candles, period=3)
    assert atr[:2] == [None, None]
    assert atr[2] == pytest.approx(2.0)
    assert atr[3] == pytest.approx((2.0 * 2 + 4.0) / 3)


def test_volatility_steady_market_is_normal() -> None:
    result = analyze_volatility(candles_from_closes(uptrend_closes()))
    assert result.volatility_level in (VolatilityLevel.NORMAL, VolatilityLevel.LOW)
    assert 0.5 < result.atr_ratio < 1.5


def test_volatility_episode_is_detected_relative_to_baseline() -> None:
    result = analyze_volatility(high_volatility_candles())
    assert result.volatility_level in (VolatilityLevel.HIGH, VolatilityLevel.EXTREME)
    assert result.is_expanding
    assert result.average_wick_ratio > 0.55
    assert result.direction_changes >= 8
    assert any("ATR" in reason for reason in result.reasons)


def test_volatility_needs_period_plus_one_candles() -> None:
    with pytest.raises(InsufficientDataError):
        analyze_volatility(candles_from_closes(uptrend_closes(10)))


# --- Momentum ---------------------------------------------------------------------


def test_momentum_up_down_and_flat() -> None:
    up = analyze_momentum(candles_from_closes([100 * 1.002**i for i in range(60)]))
    down = analyze_momentum(candles_from_closes([100 * 0.998**i for i in range(60)]))
    flat = analyze_momentum(candles_from_closes(flat_closes(60)))
    assert up.direction == Trend.UP and up.momentum_score > 50
    assert down.direction == Trend.DOWN and down.momentum_score < 50
    assert flat.direction == Trend.SIDEWAYS and flat.momentum_score == pytest.approx(50)


def test_momentum_score_is_bounded_and_lookbacks_configurable() -> None:
    result = analyze_momentum(
        candles_from_closes([100 * 1.01**i for i in range(60)]), lookbacks=((5, 1.0),)
    )
    assert 0 <= result.momentum_score <= 100
    assert list(result.rate_of_change_percent) == [5]


def test_momentum_skips_lookbacks_longer_than_history() -> None:
    result = analyze_momentum(candles_from_closes([100, 101, 102, 103, 104]))
    assert set(result.rate_of_change_percent) == {3}


# --- Volume -----------------------------------------------------------------------


def test_volume_spike_relative_to_previous_average() -> None:
    candles = candles_from_closes(uptrend_closes(25), volumes=[1000] * 24 + [3000])
    result = analyze_volume(candles)
    assert result.relative_volume == pytest.approx(3.0)
    assert result.is_spike
    assert result.confirms_price_move
    assert result.relative_volume_score == pytest.approx(75.0)


def test_volume_trend_increasing_and_decreasing() -> None:
    rising = analyze_volume(
        candles_from_closes(uptrend_closes(25), volumes=[1000] * 20 + [3000] * 5)
    )
    falling = analyze_volume(
        candles_from_closes(uptrend_closes(25), volumes=[3000] * 20 + [500] * 5)
    )
    assert rising.volume_trend == VolumeTrend.INCREASING
    assert falling.volume_trend == VolumeTrend.DECREASING


def test_relative_volume_score_mapping() -> None:
    assert relative_volume_score(0) == 0
    assert relative_volume_score(1.0) == 50
    assert relative_volume_score(3.0) == 75


# --- Market structure -------------------------------------------------------------


def _uptrend_structure_candles():
    return candles_from_closes(uptrend_closes(120))


def test_swing_points_alternate_and_are_labelled_hh_hl_in_uptrend() -> None:
    swings = classify_swings(find_swing_points(_uptrend_structure_candles(), lookback=3))
    highs = [s for s in swings if s.type == SwingType.HIGH]
    lows = [s for s in swings if s.type == SwingType.LOW]
    assert len(highs) >= 8 and len(lows) >= 8
    assert all(s.label == SwingLabel.HIGHER_HIGH for s in highs[1:])
    assert all(s.label == SwingLabel.HIGHER_LOW for s in lows[1:])
    assert highs[0].label is None and lows[0].label is None


def test_structure_uptrend_and_downtrend() -> None:
    up = analyze_structure(_uptrend_structure_candles())
    down = analyze_structure(candles_from_closes(downtrend_closes(120)))
    assert up.trend == Trend.UP and up.higher_highs > 0 and up.lower_lows == 0
    assert down.trend == Trend.DOWN and down.lower_lows > 0 and down.higher_highs == 0
    assert up.confidence > 50 and down.confidence > 50


def test_structure_break_of_structure_when_close_clears_last_swing_high() -> None:
    candles = _uptrend_structure_candles()
    last = candles[-1]
    breakout = last.model_copy(
        update={
            "timestamp": last.timestamp + timedelta(minutes=5),
            "open": last.close,
            "close": last.close + 3,
            "high": last.close + 3.1,
            "low": last.close - 0.1,
        }
    )
    result = analyze_structure([*candles, breakout])
    assert result.last_event == StructureEvent.BREAK_OF_STRUCTURE
    assert result.structure_state == StructureState.TREND_CONTINUATION


def test_structure_shift_when_uptrend_breaks_last_swing_low() -> None:
    candles = _uptrend_structure_candles()
    last = candles[-1]
    collapse = last.model_copy(
        update={
            "timestamp": last.timestamp + timedelta(minutes=5),
            "open": last.close,
            "close": last.close - 5,
            "high": last.close + 0.1,
            "low": last.close - 5.1,
        }
    )
    result = analyze_structure([*candles, collapse])
    assert result.trend == Trend.UP  # the confirmed swings still read as an uptrend
    assert result.last_event == StructureEvent.STRUCTURE_SHIFT
    assert result.structure_state == StructureState.STRUCTURE_SHIFT


def test_structure_flat_market_is_ranging() -> None:
    result = analyze_structure(candles_from_closes(flat_closes(40)))
    assert result.trend == Trend.SIDEWAYS
    assert result.structure_state == StructureState.RANGING


def test_structure_lookback_is_configurable_and_validated() -> None:
    candles = _uptrend_structure_candles()
    assert len(find_swing_points(candles, lookback=1)) >= len(
        find_swing_points(candles, lookback=4)
    )
    with pytest.raises(ValueError):
        find_swing_points(candles, lookback=0)
    with pytest.raises(InsufficientDataError):
        analyze_structure(candles[:8], lookback=3)


# --- Relative strength -----------------------------------------------------------


def test_relative_strength_matches_brief_example() -> None:
    # Stock +2%, NIFTY +0.5% over the window -> strong relative strength.
    stock = candles_from_closes([100 + 2 * i / 20 for i in range(21)])
    nifty = candles_from_closes([100 + 0.5 * i / 20 for i in range(21)], symbol="NIFTY50")
    result = analyze_relative_strength(stock, nifty, lookback=20)
    assert result.symbol_return_percent == pytest.approx(2.0)
    assert result.benchmark_return_percent == pytest.approx(0.5)
    assert result.relative_return_percent == pytest.approx(1.5)
    assert result.is_outperforming
    assert result.relative_strength_score > 75
    assert "Outperforming" in result.reasons[0]


def test_relative_strength_underperforming_scores_below_50() -> None:
    stock = candles_from_closes([100 - i * 0.05 for i in range(21)])
    nifty = candles_from_closes([100 + i * 0.05 for i in range(21)])
    result = analyze_relative_strength(stock, nifty)
    assert not result.is_outperforming and result.relative_strength_score < 50


def test_relative_strength_aligns_by_timestamp() -> None:
    stock = candles_from_closes([100 + i for i in range(21)])
    # Benchmark is missing its last 5 candles: comparison must use the
    # latest benchmark close at or before each window endpoint.
    nifty = candles_from_closes([200.0] * 16)
    result = analyze_relative_strength(stock, nifty, lookback=20)
    assert result.benchmark_return_percent == 0.0


def test_relative_strength_requires_overlapping_benchmark() -> None:
    stock = candles_from_closes([100 + i for i in range(21)])
    late = candles_from_closes([200.0] * 5, start=stock[-1].timestamp + timedelta(hours=1))
    with pytest.raises(InsufficientDataError):
        analyze_relative_strength(stock, late)


# --- Range / compression -----------------------------------------------------------


def test_range_detects_compression_and_upside_breakout() -> None:
    result = analyze_range(breakout_candles())
    assert result.is_compressed
    assert result.breakout_direction == Trend.UP
    assert result.breakout_distance_atr > 1


def test_range_efficiency_straight_line_vs_chop() -> None:
    straight = analyze_range(candles_from_closes([100 + i for i in range(40)]))
    chop = analyze_range(candles_from_closes(range_closes(80)))
    assert straight.efficiency_ratio == pytest.approx(1.0)
    assert chop.efficiency_ratio < 0.3


# --- Trend quality, analyzer, factors --------------------------------------------


def test_trend_quality_high_for_clean_trend_low_for_flat() -> None:
    up = analyze_candles("T", Timeframe.M5, candles_from_closes(uptrend_closes()))
    flat = analyze_candles("T", Timeframe.M5, candles_from_closes(flat_closes(250)))
    assert up.trend_quality is not None and up.trend_quality.score > 60
    assert up.trend_quality.direction == Trend.UP
    assert flat.trend_quality is not None and flat.trend_quality.score < 15


def test_trend_quality_weights_are_configurable() -> None:
    from app.market_intelligence.trend_quality import TrendQualityWeights

    a = analyze_candles("T", Timeframe.M5, candles_from_closes(uptrend_closes()))
    assert a.moving_averages is not None
    only_direction = compute_trend_quality(
        a.moving_averages,
        a.structure,
        a.momentum,
        a.volume,
        a.volatility,
        weights=TrendQualityWeights(
            slope=0, structure=0, distance=0, momentum=0, volume=0, volatility=0
        ),
    )
    assert only_direction.score == pytest.approx(100.0)


def test_analyzer_records_unavailable_engines_instead_of_guessing() -> None:
    result = analyze_candles("T", Timeframe.M5, candles_from_closes(uptrend_closes(12)))
    assert result.moving_averages is None and "moving_averages" in result.unavailable
    assert result.vwap is not None  # engines with enough data still run


def test_vwap_score_and_factor_set() -> None:
    above = analyze_candles("T", Timeframe.M5, candles_from_closes(uptrend_closes()))
    below = analyze_candles("T", Timeframe.M5, candles_from_closes(downtrend_closes()))
    above_score, below_score = vwap_score(above), vwap_score(below)
    assert above_score is not None and below_score is not None
    assert above_score > 60 > below_score

    keys = {f.key for f in build_factors(above)}
    assert keys == {"trend", "momentum", "vwap", "volume", "volatility", "structure"}
    for factor in build_factors(above):
        assert factor.score is None or 0 <= factor.score <= 100
        assert factor.scale in ("directional", "intensity")
