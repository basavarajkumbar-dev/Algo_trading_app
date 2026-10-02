"""Backtest storage (Phase 10).

strategy_versions       a strategy's logic version + config hash, with the
                        parameter values that produce it
parameter_sets          every parameter set a run used (hash-identified)
backtest_runs           one run: request, mode, date range, universe, data
                        source, status, headline metrics, robustness and the
                        full result (segments, grid, walk-forward, warnings)
backtest_trades         every closed trade of the reported segments
backtest_equity_points  equity curve (per trade) and daily equity
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StrategyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy", "version_id", name="uq_strategy_version"),)

    strategy: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    semver: Mapped[str] = mapped_column(String(20), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # backtest strategy choice + strategy-scope parameters that produce this version
    strategy_choice: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ParameterSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "parameter_sets"
    __table_args__ = (UniqueConstraint("strategy_choice", "params_hash", name="uq_parameter_set"),)

    strategy_choice: Mapped[str] = mapped_column(String(40), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class BacktestRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (Index("ix_backtest_runs_created", "created_at"),)

    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    strategy_choice: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.id"), nullable=True
    )
    parameter_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parameter_sets.id"), nullable=True
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    universe: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )  # symbols + membership source
    data_source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    capital: Mapped[float] = mapped_column(Float, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    robustness: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BacktestTradeRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "backtest_trades"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True
    )
    segment: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class BacktestEquityPoint(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "backtest_equity_points"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(6), nullable=False)  # TRADE | DAY
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
