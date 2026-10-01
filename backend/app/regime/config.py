"""Regime detector configuration.

Every weight and threshold lives here rather than inside the detector
logic, so they can be tuned (and later loaded from the database or
strategy configuration) without code changes. Weights for each regime
are normalised by their total, so they needn't sum to exactly 100.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


def _bullish_weights() -> dict[str, float]:
    return {
        "price_vs_vwap": 15,
        "vwap_slope": 10,
        "ema_slope": 15,
        "ema_alignment": 10,
        "structure": 20,
        "momentum": 15,
        "directional_efficiency": 10,
        "volume": 5,
    }


def _range_weights() -> dict[str, float]:
    return {
        "no_ma_trend": 20,
        "vwap_crossings": 20,
        "low_efficiency": 25,
        "flat_momentum": 15,
        "no_structure_trend": 10,
        "inside_range": 10,
    }


def _breakout_weights() -> dict[str, float]:
    return {
        "prior_compression": 25,
        "range_break": 30,
        "volume_expansion": 20,
        "range_expansion": 15,
        "momentum_agrees": 10,
    }


def _high_volatility_weights() -> dict[str, float]:
    return {
        "atr_elevated": 35,
        "atr_expanding": 15,
        "large_wicks": 20,
        "reversals": 15,
        "large_candles": 15,
    }


class RegimeConfig(BaseModel):
    # Data sufficiency.
    min_candles: int = 60

    # A regime must score at least this to be reported; otherwise UNKNOWN.
    min_regime_score: float = 55.0
    # HIGH_VOLATILITY wins outright at or above this score, even if a
    # trend regime scores higher — the conservative choice, since
    # unstable markets are where trend signals are least reliable.
    high_volatility_override_score: float = 65.0
    # Confidence is reduced when the runner-up regime is within this many points.
    ambiguity_margin: float = 15.0

    # Thresholds.
    min_ema_slope_percent: float = 0.02  # percent per candle
    min_efficiency_for_trend: float = 0.35
    max_efficiency_for_range: float = 0.30
    min_vwap_crossings_for_range: int = 3
    min_relative_volume_for_trend: float = 1.0
    min_relative_volume_for_breakout: float = 1.5
    min_range_expansion_atr: float = 1.5
    min_wick_ratio_for_volatility: float = 0.55
    min_reversal_fraction_for_volatility: float = 0.6
    min_large_candle_atr: float = 1.8

    # Weights per regime (STRONG_BEARISH mirrors STRONG_BULLISH).
    bullish_weights: dict[str, float] = Field(default_factory=_bullish_weights)
    range_weights: dict[str, float] = Field(default_factory=_range_weights)
    breakout_weights: dict[str, float] = Field(default_factory=_breakout_weights)
    high_volatility_weights: dict[str, float] = Field(default_factory=_high_volatility_weights)
