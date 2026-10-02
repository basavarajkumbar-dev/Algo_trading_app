"""Shared pytest fixtures.

Database-dependent tests (auth, settings) need a real, reachable
Postgres to exercise real SQL — that is the whole point of testing the
migration-backed models rather than mocking them away. If no database
is reachable (e.g. running `pytest` outside Docker Compose with no
local Postgres), those tests are skipped rather than failing noisily,
matching the same "don't fabricate a passing state" principle used by
the Phase 1 health-endpoint tests.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.session import async_session_factory, engine

#: Test modules that need a reachable Postgres; skipped (not failed) without one.
DATABASE_TEST_MODULES = (
    "test_auth.py",
    "test_signal_persistence.py",
    "test_paper_trading_persistence.py",
)


async def _database_reachable() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest_asyncio.fixture
async def db_available() -> bool:
    return await _database_reachable()


@pytest_asyncio.fixture(autouse=True)
async def _skip_if_no_database(request: pytest.FixtureRequest, db_available: bool) -> None:
    # Only test_auth.py (and any future test module that actually talks
    # to the database) requires a reachable Postgres. test_health.py's
    # entire purpose is verifying correct behavior when the database is
    # NOT reachable, so it must never be skipped here.
    if not any(name in str(request.node.fspath) for name in DATABASE_TEST_MODULES):
        return
    if not db_available:
        pytest.skip(
            "No reachable Postgres for this test run — set DATABASE_URL to a "
            "real database (e.g. via `docker compose up postgres` or a local "
            "Postgres) to run auth/settings tests."
        )


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncGenerator[None, None]:
    """Dispose the async engine's connection pool after every test.

    Each pytest-asyncio test function runs in its own event loop by
    default. asyncpg connections are bound to the loop that created
    them, so a pooled connection left open from one test's loop causes
    spurious failures (surfacing as "database unreachable") when reused
    from the next test's loop. Disposing after each test avoids that
    entirely at a small, acceptable performance cost for this test
    suite's size.
    """

    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_available: bool) -> AsyncGenerator[None, None]:
    """Truncate auth-relevant tables before each test so tests don't leak
    state (e.g. duplicate-email collisions) into each other."""

    if db_available:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE sessions, trading_settings, risk_configs, "
                    "audit_logs, users, signals, paper_orders, paper_trades, system_events "
                    "RESTART IDENTITY CASCADE"
                )
            )
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


@pytest.fixture
def settings():
    return get_settings()
