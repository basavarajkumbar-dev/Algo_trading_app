"""Async SQLAlchemy engine/session setup.

Kept intentionally small: a module-level engine + sessionmaker, and a
FastAPI dependency (`get_db`) that request handlers/services use to get
a scoped `AsyncSession`. No model imports here — that would risk
circular imports between database/session.py and database/models/.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(_settings.database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped database session per request."""

    async with async_session_factory() as session:
        yield session
