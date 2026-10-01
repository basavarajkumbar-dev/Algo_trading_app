"""TradingAccount model.

A structural placeholder linking a user to a broker/paper account. No
broker connectivity or trading logic is implemented in this phase — see
docs/development-roadmap.md Phase 11 (paper trading) and Phase 16
(broker adapters).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import BrokerType
from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TradingAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trading_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    broker: Mapped[BrokerType] = mapped_column(
        SAEnum(BrokerType, name="broker_type"), nullable=False, default=BrokerType.PAPER
    )
    account_label: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
