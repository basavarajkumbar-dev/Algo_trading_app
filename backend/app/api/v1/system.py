"""System endpoints.

GET /system/health       API, database and Redis connectivity
GET /system/components   per-component HEALTHY / WARNING / CRITICAL (Phase 9)
GET /system/events       recent system events, newest first (notifications)
GET /system/market-data  the market data feed: provider, mode, WebSocket, REST,
                         last tick, latency, subscriptions, staleness (Phase 13)
GET /system/broker-health  Zerodha integration verdict, check by check (Phase 13)

Route handlers stay thin — all logic lives in
app.services.system_health per PROJECT_CONTRACT.md section 2.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.health import BrokerHealthReport, broker_health, market_data_status
from app.core.config import Settings, get_settings
from app.database.models.system_event import SystemEvent
from app.database.session import get_db
from app.schemas.system import SystemHealthResponse
from app.services.component_health import ComponentHealthResponse, get_component_health
from app.services.system_health import get_system_health

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=SystemHealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> SystemHealthResponse:
    """Report API, database, and Redis connectivity.

    This does NOT indicate that any market data, strategy, or trading
    subsystem is healthy — those are introduced in later phases.
    """

    return await get_system_health(settings)


@router.get("/market-data")
async def market_data(request: Request) -> dict[str, Any]:
    if getattr(request.app.state, "market_data_service", None) is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Market data is not running.")
    return market_data_status(request.app.state)


@router.get("/broker-health", response_model=BrokerHealthReport)
async def broker_health_report(
    request: Request, settings: Settings = Depends(get_settings)
) -> BrokerHealthReport:
    return await broker_health(request.app.state, settings)


@router.get("/components", response_model=ComponentHealthResponse)
async def components(
    request: Request, settings: Settings = Depends(get_settings)
) -> ComponentHealthResponse:
    """Market data, backend, database, Redis, WebSocket, strategy engine,
    risk engine and paper broker, each HEALTHY / WARNING / CRITICAL."""

    return await get_component_health(request.app.state, settings)


class SystemEventItem(BaseModel):
    id: str
    event_type: str
    severity: str
    message: str
    metadata: dict[str, Any] | None
    created_at: datetime


class SystemEventList(BaseModel):
    items: list[SystemEventItem]
    total: int


@router.get("/events", response_model=SystemEventList)
async def events(
    limit: int = Query(default=30, ge=1, le=200),
    severity: str | None = Query(default=None, pattern="^(INFO|WARNING|CRITICAL)$"),
    db: AsyncSession = Depends(get_db),
) -> SystemEventList:
    """Recent system events (paper trading events, risk rejections,
    emergency stops), newest first — the dashboard's notifications."""

    query = select(SystemEvent)
    if severity:
        query = query.where(SystemEvent.severity == severity)
    try:
        total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        rows = (
            (await db.execute(query.order_by(SystemEvent.created_at.desc()).limit(limit)))
            .scalars()
            .all()
        )
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Event storage unavailable: {type(exc).__name__}",
        ) from exc
    return SystemEventList(
        items=[
            SystemEventItem(
                id=str(r.id),
                event_type=r.event_type,
                severity=r.severity,
                message=r.message,
                metadata=r.event_metadata,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
    )
