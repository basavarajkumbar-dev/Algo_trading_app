"""StrategyConfig model.

Stores strategy enable/disable state, score threshold, and a free-form
parameter set. The strategies themselves (TrendContinuation,
BreakoutMomentum, MeanReversion) are implemented in
docs/development-roadmap.md Phase 8 — this phase only defines where
their configuration lives, per PROJECT_CONTRACT.md's requirement that
strategy weights/parameters never be hardcoded.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StrategyConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_configs"

    strategy_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1.0")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score_threshold: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=70.0)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
