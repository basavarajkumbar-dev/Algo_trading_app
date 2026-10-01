"""Shared schemas for the market intelligence engines.

Every engine in this package takes a chronological list of
`app.market_data.schemas.Candle` (oldest first) and returns a
structured, explainable result rather than a bare number. Engine
outputs describe what the candles show; they are not predictions, and
nothing here should be read as a guarantee of future price movement.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Trend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class Relationship(str, Enum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    AT = "AT"


class VolumeTrend(str, Enum):
    INCREASING = "INCREASING"
    DECREASING = "DECREASING"
    STABLE = "STABLE"


class VolatilityLevel(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class InsufficientDataError(Exception):
    """Raised when an engine has too few candles to say anything
    meaningful. Callers treat it as "not enough data yet" and never
    substitute a fabricated number."""


class EngineResult(BaseModel):
    """Common base: every engine result carries the human-readable
    reasons behind it (the explainability requirement in
    PROJECT_CONTRACT.md)."""

    reasons: list[str] = Field(default_factory=list)
