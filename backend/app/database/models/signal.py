"""Signal model.

One row per *setup episode*: created the first time a scan produces a
scored candidate for (symbol, strategy, direction), kept ACTIVE while
later scans keep producing it, and marked EXPIRED once a scan no longer
does. The values captured at creation (entry, stop, target, score, the
explanation) are never rewritten, so a stored signal stays an honest
record of what the engines said at the time; only `last_seen_at`,
`last_score`, `last_rank` and the status change afterwards.

A partial unique index guarantees at most one ACTIVE signal per
(symbol, strategy, direction).

Signals are not orders. Nothing reads this table to trade.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Signal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index(
            "uq_signals_active_setup",
            "symbol",
            "strategy",
            "direction",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_signals_status_generated", "status", "generated_at"),
    )

    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY / SELL
    strategy: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)

    entry: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    target: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    risk_reward: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    quantity_candidate: Mapped[int] = mapped_column(Integer, nullable=False)

    score: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    category: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    executable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    regime_confidence: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)

    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ACTIVE / EXPIRED
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_score: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    last_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Full explanation as generated: score breakdown, reasons, strategy
    # checks, features, quantity note.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
