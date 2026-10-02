"""Signal engine configuration: scoring weights, categories, thresholds,
quantity candidate defaults and ranking.

The scoring weights are the brief's *initial* model. They are not
assumed to be optimal: they are configuration, overridable through
environment variables (see app.core.config) and to be validated by
backtesting in a later phase.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

SCORE_COMPONENTS: tuple[str, ...] = (
    "market_alignment",
    "relative_strength",
    "trend_quality",
    "vwap",
    "market_structure",
    "retracement",
    "volume",
    "momentum",
    "price_action",
)


def default_weights() -> dict[str, float]:
    return {
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


class ScoringConfig(BaseModel):
    """Weights are points. Note the brief's initial weights add up to 95,
    not 100 (15+15+15+10+10+10+10+5+5); they are kept exactly as given
    and the score is scaled to 100 regardless. A component that does not apply to a strategy (retracement for a
    breakout, for example) is left out, and the applicable weights are
    scaled to sum to 100, so a score always means "share of the
    applicable, weighted evidence present"."""

    weights: dict[str, float] = Field(default_factory=default_weights)
    pass_threshold: float = 0.5  # a component scoring this or more shows as a tick

    @field_validator("weights")
    @classmethod
    def _valid_weights(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(SCORE_COMPONENTS)
        if unknown:
            raise ValueError(f"Unknown score components: {sorted(unknown)}")
        if any(w < 0 for w in value.values()):
            raise ValueError("Weights cannot be negative")
        if sum(value.values()) <= 0:
            raise ValueError("At least one weight must be positive")
        merged = default_weights()  # a partial override keeps the other defaults
        merged.update(value)
        return merged


class CategoryConfig(BaseModel):
    elite: float = 90.0
    strong: float = 80.0
    watch: float = 70.0
    executable_threshold: float = 85.0  # "ready": eligible for a future risk check

    @model_validator(mode="after")
    def _ordered(self) -> CategoryConfig:
        if not (0 <= self.watch <= self.strong <= self.elite <= 100):
            raise ValueError(
                "Category thresholds must satisfy 0 <= watch <= strong <= elite <= 100"
            )
        if not 0 <= self.executable_threshold <= 100:
            raise ValueError("executable_threshold must be between 0 and 100")
        return self


class QuantityConfig(BaseModel):
    """Defaults mirror the per-user database defaults (trading_settings
    paper_capital, risk_configs risk_per_trade and max_position_size).
    The quantity is a candidate only: the Risk Engine (a later phase)
    decides the real size per user."""

    capital: float = 100_000.0
    risk_per_trade_percent: float = 1.0
    max_position_value: float = 50_000.0


class RankingConfig(BaseModel):
    top_n: int = Field(default=10, ge=1, le=50)
    min_category_for_top: str = "WATCH"  # top candidates exclude IGNORE by default
    liquidity_window: int = 20  # candles averaged for turnover (price x volume)


class SignalEngineConfig(BaseModel):
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    categories: CategoryConfig = Field(default_factory=CategoryConfig)
    quantity: QuantityConfig = Field(default_factory=QuantityConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    store_min_score: float = 70.0  # candidates below this are shown, not stored as signals


def signal_engine_config(settings: object) -> SignalEngineConfig:
    """Build the engine config from environment settings (app.core.config)."""

    weights = getattr(settings, "signal_score_weights", None)
    return SignalEngineConfig(
        scoring=ScoringConfig(weights=weights) if weights else ScoringConfig(),
        categories=CategoryConfig(
            executable_threshold=getattr(settings, "signal_executable_threshold", 85.0)
        ),
        ranking=RankingConfig(top_n=getattr(settings, "scanner_top_n", 10)),
        store_min_score=getattr(settings, "signal_store_min_score", 70.0),
    )
