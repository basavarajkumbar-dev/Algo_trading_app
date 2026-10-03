"""TradeApproval model (Phase 12): one row per approval request.

The proposal (stock, direction, entry, stop, target, quantity, risk,
reward:risk, strategy, score, regime, reasons) is written once and never
changed. Afterwards only the status and the decision fields change:
approved_by / approved_at, rejected_by / rejected_at, expired_at,
executed_at, reason, trade_id. Every change is also written to
audit_logs.

A partial unique index allows at most one open (PENDING / APPROVED)
request per signal, so one signal cannot be approved twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TradeApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trade_approvals"
    __table_args__ = (
        Index(
            "uq_trade_approvals_open_signal",
            "signal_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'APPROVED')"),
        ),
        Index("ix_trade_approvals_status_expires", "status", "expires_at"),
    )

    status: Mapped[str] = mapped_column(String(12), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )

    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    entry: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    target: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    risk_per_share: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    position_value: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    reward_risk: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    category: Mapped[str | None] = mapped_column(String(10), nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regime_confidence: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    risk_checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    signal_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    trade_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    execution_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
