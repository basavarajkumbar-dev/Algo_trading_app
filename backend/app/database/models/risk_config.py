"""RiskConfig model.

Stores per-user (or, when user_id is null, global default) risk
parameters. The RiskManager that reads and enforces these values is
implemented in docs/development-roadmap.md Phase 10 — this phase only
defines where the configuration lives.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RiskConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_configs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
        unique=True,
    )
    risk_per_trade: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=1.0)
    max_daily_loss: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=3.0)
    max_trades_per_day: Mapped[int] = mapped_column(nullable=False, default=5)
    max_consecutive_losses: Mapped[int] = mapped_column(nullable=False, default=3)
    max_position_size: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=50_000.0
    )
