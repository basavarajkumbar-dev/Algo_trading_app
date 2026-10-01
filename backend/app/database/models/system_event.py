"""SystemEvent model.

Records system-level events (health transitions, provider disconnects,
strategy crashes, etc.) distinct from user-attributable AuditLog
entries. Nothing writes to this table yet in this phase — it is defined
now so the system health engine (Phase 6+) and fail-safe logic have a
home for their events from day one.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SystemEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "system_events"

    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
