"""Paper trading tables (Phase 8).

paper_orders  every order the paper broker accepted or rejected, with
              its fills and charges
paper_trades  one row per trade attempt: entry, exits, P&L, the risk
              decision that approved it and why it closed

Simulated records only: nothing here was ever sent to a broker.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PaperTradeRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "paper_trades"
    __table_args__ = (Index("ix_paper_trades_status_created", "status", "trade_created_at"),)

    signal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # LONG / SHORT
    strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    planned_entry: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    target: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    initial_risk: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    gross_pnl: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    charges: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    net_pnl: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    r_multiple: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trade_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # full PaperTrade as JSON (order ids, risk checks, last price ...)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PaperOrderRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "paper_orders"

    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_trades.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    purpose: Mapped[str | None] = mapped_column(String(12), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    charges: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # fills etc.
