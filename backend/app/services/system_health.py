"""System health service.

Phase 1 scope: report whether the API process is up, and whether it can
reach PostgreSQL and Redis. This is a lightweight connectivity probe, not
the full SystemHealthEngine described in PROJECT_CONTRACT.md (which will
also monitor the market data feed, strategy engine, risk engine, broker,
websocket layer, and workers once those exist).

Business logic intentionally lives here, not in the API route — the
route handler stays thin per PROJECT_CONTRACT.md section 2.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.constants import ComponentStatus, SystemHealthStatus, TradingMode
from app.core.logging import get_logger
from app.schemas.system import ComponentHealth, SystemHealthResponse

logger = get_logger(__name__)

_CONNECT_TIMEOUT_SECONDS = 2.0


async def _check_database(database_url: str) -> ComponentHealth:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return ComponentHealth(name="database", status=ComponentStatus.CONNECTED)
    except Exception as exc:  # noqa: BLE001 - health checks must not crash the app
        logger.warning("Database health check failed: %s", type(exc).__name__)
        return ComponentHealth(
            name="database",
            status=ComponentStatus.DISCONNECTED,
            detail="Unable to reach PostgreSQL. This is expected if the "
            "database container is still starting.",
        )
    finally:
        await engine.dispose()


async def _check_redis(redis_url: str) -> ComponentHealth:
    client = redis_asyncio.from_url(redis_url, socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS)
    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
            await client.ping()
        return ComponentHealth(name="redis", status=ComponentStatus.CONNECTED)
    except Exception as exc:  # noqa: BLE001 - health checks must not crash the app
        logger.warning("Redis health check failed: %s", type(exc).__name__)
        return ComponentHealth(
            name="redis",
            status=ComponentStatus.DISCONNECTED,
            detail="Unable to reach Redis. This is expected if the redis "
            "container is still starting.",
        )
    finally:
        await client.aclose()


def _overall_status(components: list[ComponentHealth]) -> SystemHealthStatus:
    if all(c.status == ComponentStatus.CONNECTED for c in components):
        return SystemHealthStatus.HEALTHY
    if any(c.status == ComponentStatus.CONNECTED for c in components):
        return SystemHealthStatus.WARNING
    return SystemHealthStatus.CRITICAL


async def get_system_health(settings: Settings) -> SystemHealthResponse:
    """Run all Phase-1 health checks and assemble the response payload."""

    database_health, redis_health = await asyncio.gather(
        _check_database(settings.database_url),
        _check_redis(settings.redis_url),
    )
    components = [database_health, redis_health]

    notes = [
        "Market data is simulated (mock provider) — no live market feed is "
        "connected in this phase.",
        "No broker is connected and no real orders can be placed.",
    ]
    if settings.trading_mode != TradingMode.PAPER:
        # Should be unreachable given the Settings default, but Phase 1
        # surfaces it loudly rather than silently trusting configuration.
        notes.append(
            "WARNING: configured trading mode is not PAPER. No execution "
            "engine exists yet in this phase regardless of this setting."
        )

    return SystemHealthResponse(
        status=_overall_status(components),
        app_name=settings.app_name,
        app_env=settings.app_env,
        demo_mode=settings.demo_mode,
        trading_mode=settings.trading_mode,
        live_trading_active=False,
        components=components,
        notes=notes,
    )
