"""Regime detector tests.

Handcrafted series check each regime end to end; config overrides and
edited analyses check the decision rules (minimum score, high-volatility
precedence, ambiguity penalty) directly.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest

from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.schemas import SimulatedRegime, Timeframe
from app.market_intelligence.analyzer import analyze_candles
from app.market_intelligence.schemas import VolatilityLevel
from app.regime.config import RegimeConfig
from app.regime.detector import classify_regime, detect_regime
from app.regime.schemas import REGIME_DISCLAIMER, MarketRegime
from app.tests.candle_factory import (
    breakout_candles,
    candles_from_closes,
    downtrend_closes,
    high_volatility_candles,
    range_closes,
    uptrend_closes,
)

ALL_SCORED = {
    MarketRegime.STRONG_BULLISH,
    MarketRegime.STRONG_BEARISH,
    MarketRegime.RANGE,
    MarketRegime.BREAKOUT,
    MarketRegime.HIGH_VOLATILITY,
}


def _detect(candles):
    result, _ = detect_regime("TEST", Timeframe.M5, candles)
    return result


def test_strong_bullish() -> None:
    result = _detect(candles_from_closes(uptrend_closes()))
    assert result.regime == MarketRegime.STRONG_BULLISH
    assert result.confidence >= 80
    reasons = " | ".join(result.reasons)
    assert "above VWAP" in reasons
    assert "Positive EMA slope" in reasons
    assert "Higher-high" in reasons


def test_strong_bearish() -> None:
    result = _detect(candles_from_closes(downtrend_closes()))
    assert result.regime == MarketRegime.STRONG_BEARISH
    assert result.confidence >= 80
    assert any("below VWAP" in r for r in result.reasons)
    assert any("Lower-high" in r for r in result.reasons)


def test_range() -> None:
    result = _detect(candles_from_closes(range_closes()))
    assert result.regime == MarketRegime.RANGE
    assert any("VWAP crossings" in r for r in result.reasons)
    assert any("directional movement" in r for r in result.reasons)


def test_breakout() -> None:
    result = _detect(breakout_candles())
    assert result.regime == MarketRegime.BREAKOUT
    reasons = " | ".join(result.reasons)
    assert "Compression" in reasons
    assert "broke above" in reasons
    assert "Volume expansion" in reasons


def test_high_volatility() -> None:
    result = _detect(high_volatility_candles())
    assert result.regime == MarketRegime.HIGH_VOLATILITY
    assert any("ATR elevated" in r for r in result.reasons)
    assert any("wicks" in r for r in result.reasons)


def test_unknown_when_not_enough_candles() -> None:
    result = _detect(candles_from_closes(uptrend_closes(30)))
    assert result.regime == MarketRegime.UNKNOWN
    assert result.confidence == 0
    assert "Not enough data" in result.reasons[0]
    assert result.scores == {}


def test_unknown_when_no_regime_reaches_minimum_score() -> None:
    analysis = analyze_candles("TEST", Timeframe.M5, candles_from_closes(uptrend_closes()))
    result = classify_regime(analysis, RegimeConfig(min_regime_score=101))
    assert result.regime == MarketRegime.UNKNOWN
    assert result.confidence == 0
    assert "minimum score" in result.reasons[0]
    assert set(result.scores) == ALL_SCORED  # scores are still reported for transparency


def test_result_shape_matches_contract() -> None:
    result = _detect(candles_from_closes(uptrend_closes()))
    payload = result.model_dump(mode="json")
    for key in ("regime", "confidence", "features", "reasons", "timestamp"):
        assert key in payload
    assert set(result.scores) == ALL_SCORED
    assert 0 <= result.confidence <= 100
    assert result.features["candles_analyzed"] == 250
    for feature in (
        "price_vs_vwap",
        "ema_fast_slope_percent",
        "structure_trend",
        "momentum_score",
        "atr_ratio",
    ):
        assert feature in result.features
    assert result.data_as_of is not None


def test_no_regime_claims_to_predict_the_future() -> None:
    result = _detect(candles_from_closes(uptrend_closes()))
    assert result.note == REGIME_DISCLAIMER
    assert "not a forecast" in result.note
    text = " ".join(result.reasons + result.counter_reasons).lower()
    for forbidden in ("guarantee", "will rise", "will fall", "certain"):
        assert forbidden not in text


def test_high_volatility_takes_precedence_over_a_trend() -> None:
    analysis = analyze_candles("TEST", Timeframe.M5, candles_from_closes(uptrend_closes()))
    assert analysis.volatility is not None
    stressed = analysis.model_copy(
        update={
            "volatility": analysis.volatility.model_copy(
                update={
                    "volatility_level": VolatilityLevel.EXTREME,
                    "atr_ratio": 2.6,
                    "is_expanding": True,
                    "average_wick_ratio": 0.7,
                }
            )
        }
    )
    result = classify_regime(stressed)
    assert result.scores[MarketRegime.STRONG_BULLISH] > result.scores[MarketRegime.HIGH_VOLATILITY]
    assert result.regime == MarketRegime.HIGH_VOLATILITY
    assert "High volatility takes precedence over other regimes" in result.reasons


def test_confidence_is_reduced_when_runner_up_is_close() -> None:
    analysis = analyze_candles("TEST", Timeframe.M5, high_volatility_candles())
    clear = classify_regime(analysis, RegimeConfig(ambiguity_margin=0))
    ambiguous = classify_regime(analysis, RegimeConfig(ambiguity_margin=30))
    assert clear.regime == ambiguous.regime == MarketRegime.HIGH_VOLATILITY
    assert ambiguous.confidence < clear.confidence
    assert any("Also resembles" in r for r in ambiguous.counter_reasons)


def test_weights_are_configurable() -> None:
    analysis = analyze_candles("TEST", Timeframe.M5, candles_from_closes(uptrend_closes()))
    weights = RegimeConfig().bullish_weights | {"structure": 0}
    result = classify_regime(analysis, RegimeConfig(bullish_weights=weights))
    structure_conditions = [r for r in result.reasons if "Higher-high" in r]
    assert result.regime == MarketRegime.STRONG_BULLISH
    assert structure_conditions  # condition still evaluated and explained
    assert result.scores[MarketRegime.STRONG_BULLISH] == pytest.approx(100.0)


def test_timestamp_can_be_injected_for_reproducibility() -> None:
    analysis = analyze_candles("TEST", Timeframe.M5, candles_from_closes(uptrend_closes()))
    fixed = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    assert classify_regime(analysis, now=fixed).timestamp == fixed


@pytest.mark.parametrize(
    ("simulated", "expected", "minimum_share"),
    [
        (SimulatedRegime.TRENDING_UP, MarketRegime.STRONG_BULLISH, 0.75),
        (SimulatedRegime.TRENDING_DOWN, MarketRegime.STRONG_BEARISH, 0.75),
        (SimulatedRegime.RANGE, MarketRegime.RANGE, 0.75),
        (SimulatedRegime.BREAKOUT, MarketRegime.BREAKOUT, 0.75),
        (SimulatedRegime.HIGH_VOLATILITY, MarketRegime.HIGH_VOLATILITY, 0.4),
    ],
)
async def test_detector_recognises_simulated_regimes(
    simulated: SimulatedRegime, expected: MarketRegime, minimum_share: float
) -> None:
    """Consistency check between the simulator and the detector across
    20 fixed seeds. Not every seed must match: random noise can
    genuinely make a "trending" simulation look like a range for a
    while, and the detector should say so rather than echo the label.
    High volatility is matched least often because volatile markets
    are often also directional."""

    counts: Counter[MarketRegime] = Counter()
    for seed in range(20):
        provider = MockMarketDataProvider(rng_seed=seed, regime_rotation_seconds=None)
        provider.set_regime("RELIANCE", simulated)
        candles = await provider.get_historical_data("RELIANCE", Timeframe.M5, 250)
        counts[_detect(candles).regime] += 1
    assert counts[expected] / 20 >= minimum_share, counts
