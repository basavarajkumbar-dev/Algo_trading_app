"""SQLAlchemy declarative base and shared mixins.

Per PROJECT_CONTRACT.md section 4 (Database Design): every important
entity has `id`, `created_at`, and `updated_at` where applicable, using
a safe identifier type (UUID).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key, generated application-side.

    Application-side generation (rather than a Postgres server default
    like gen_random_uuid()) keeps model creation independent of any
    particular Postgres extension being enabled.
    """

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Adds created_at / updated_at columns, maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
