"""TradingSettings model.

Per-user settings singleton (one row per user, created with safe
defaults at registration). Mirrors the "Settings System" section of
PROJECT_CONTRACT.md. No trading loop reads this yet — that begins in
docs/development-roadmap.md Phase 11.
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import Boolean, ForeignKey, Numeric, Time
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import TradingMode
from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TradingSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trading_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        unique=True,
    )
    trading_mode: Mapped[TradingMode] = mapped_column(
        SAEnum(TradingMode, name="trading_mode"), nullable=False, default=TradingMode.PAPER
    )
    paper_capital: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=100_000.0)
    auto_trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signal_score_threshold: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=85.0
    )
    trading_start_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(9, 15))
    trading_end_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(15, 15))
    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
