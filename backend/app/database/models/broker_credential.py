"""Broker API credentials entered by a user in the app (encrypted at rest).

One row per (user, broker). The API key, API secret and the day's access
token are stored only as AES-GCM ciphertexts bound to this user (see
app.security.credential_vault); the encryption key is not in the database.
`api_key_hint` holds the last four characters of the API key so the app can
say which key is configured without revealing it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BrokerCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "broker_credentials"
    __table_args__ = (UniqueConstraint("user_id", "broker", name="uq_broker_credentials_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    broker: Mapped[str] = mapped_column(String(20), nullable=False, default="ZERODHA")
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    api_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # this user's credentials drive the server's Kite connection
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CREDENTIALS_SAVED"
    )
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
