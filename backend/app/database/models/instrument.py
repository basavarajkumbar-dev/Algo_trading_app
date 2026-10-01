"""Instrument model.

Static reference data for tradable instruments (e.g. NIFTY 50 stocks).
No market data or pricing logic is attached in this phase — see
docs/development-roadmap.md Phase 5 (market data layer).
"""

from __future__ import annotations

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Instrument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="NSE")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(32), nullable=False, default="EQUITY")
    tick_size: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0.05)
    lot_size: Mapped[int] = mapped_column(nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
