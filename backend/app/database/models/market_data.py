"""Market-data storage (Phase 13).

broker_instruments      the Kite instrument master (one row per instrument
                        token); indexes on token, exchange + tradingsymbol,
                        name, expiry and instrument type
instrument_sync_runs    every master download: counts, status, error
market_candles          closed candles built from live ticks (and warm-up
                        history), one row per symbol / timeframe / start
market_ticks            raw normalized ticks, only when PERSIST_RAW_TICKS=true

Data-quality and connection events go to system_events (MARKET_DATA_*,
WEBSOCKET_*, INSTRUMENT_SYNC_*). Nothing here stores a credential.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BrokerInstrument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "broker_instruments"
    __table_args__ = (
        UniqueConstraint("broker", "instrument_token", name="uq_broker_instrument_token"),
        Index("ix_broker_instruments_exchange_symbol", "exchange", "tradingsymbol"),
        Index("ix_broker_instruments_token", "instrument_token"),
        Index("ix_broker_instruments_name", "name"),
        Index("ix_broker_instruments_expiry", "expiry"),
        Index("ix_broker_instruments_type", "instrument_type"),
    )

    broker: Mapped[str] = mapped_column(String(16), nullable=False, default="ZERODHA")
    instrument_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exchange_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tradingsymbol: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(8), nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    tick_size: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class InstrumentSyncRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "instrument_sync_runs"

    broker: Mapped[str] = mapped_column(String(16), nullable=False, default="ZERODHA")
    status: Mapped[str] = mapped_column(
        String(12), index=True, nullable=False
    )  # RUNNING/SUCCESS/FAILED
    exchanges: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class MarketCandle(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("source", "symbol", "timeframe", "timestamp", name="uq_market_candle"),
        Index("ix_market_candles_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False)  # ZERODHA / MOCK
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    origin: Mapped[str] = mapped_column(
        String(10), nullable=False, default="LIVE"
    )  # LIVE/HISTORICAL


class MarketTick(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "market_ticks"
    __table_args__ = (Index("ix_market_ticks_token_ts", "instrument_token", "timestamp"),)

    instrument_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exchange_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_price: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    last_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
