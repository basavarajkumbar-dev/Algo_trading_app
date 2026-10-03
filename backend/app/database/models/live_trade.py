"""Live trades (Phase 14): one row per live trade, with the orders it sent.

Credentials never appear here. Every live action is also written to
audit_logs; this table is the trade's current state for restart recovery
and the dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LiveTradeRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "live_trades"
    __table_args__ = (Index("ix_live_trades_status_opened", "status", "opened_at"),)

    correlation_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    planned_entry: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    target: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    average_entry: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    average_exit: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    armed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
