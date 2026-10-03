"""Broker endpoints (Phase 11). No endpoint places, modifies or cancels a
live order: Zerodha is read-only in this phase.

GET  /broker/status                 execution broker, Zerodha connection, live
                                    blockers, order-manager reconciliation state
GET  /broker/events                 recent broker / order events
POST /broker/reconcile              reconcile tracked orders now (TRADER / ADMIN)
POST /broker/connect                (re)connect and re-check health (ADMIN)
POST /broker/disconnect             stop using the session (ADMIN)
GET  /broker/zerodha/login-url      Kite login URL (ADMIN)
POST /broker/zerodha/session        exchange a request_token for a session;
                                    the token is kept in memory only (ADMIN)
GET  /broker/zerodha/profile        masked profile (TRADER / ADMIN)
GET  /broker/zerodha/balance        equity margins (TRADER / ADMIN)
GET  /broker/zerodha/positions      net positions (TRADER / ADMIN)
GET  /broker/zerodha/orders         the day's order book (TRADER / ADMIN)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import require_role
from app.brokers.service import BrokerService
from app.core.constants import UserRole
from app.execution.broker import (
    BrokerAuthError,
    BrokerError,
    BrokerNotConfiguredError,
    BrokerRateLimitError,
    BrokerUnavailableError,
)
from app.execution.models import Balance, BrokerProfile, Order, Position
from app.execution.order_manager import OrderEvent

router = APIRouter(prefix="/broker", tags=["broker"])
_admin = require_role(UserRole.ADMIN)
_operator = require_role(UserRole.TRADER, UserRole.ADMIN)

NOTE = (
    "PAPER mode: orders execute on the simulated paper broker. Zerodha, when "
    "configured, is connected read-only; live orders are not enabled."
)


def _service(request: Request) -> BrokerService:
    svc: BrokerService | None = getattr(request.app.state, "broker_service", None)
    if svc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Broker service is not running.")
    return svc


def _http_error(exc: BrokerError) -> HTTPException:
    if isinstance(exc, BrokerNotConfiguredError):
        return HTTPException(status.HTTP_409_CONFLICT, f"Zerodha is not configured: {exc}")
    if isinstance(exc, BrokerAuthError):
        return HTTPException(status.HTTP_409_CONFLICT, f"Zerodha login required: {exc}")
    if isinstance(exc, BrokerRateLimitError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    if isinstance(exc, BrokerUnavailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Zerodha unreachable: {exc}")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Zerodha refused: {exc}")


@router.get("/status")
async def broker_status(request: Request) -> dict[str, Any]:
    return {**_service(request).status(), "note": NOTE}


class EventList(BaseModel):
    items: list[OrderEvent]


@router.get("/events", response_model=EventList)
async def events(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> EventList:
    return EventList(items=_service(request).orders.events(limit))


@router.post("/reconcile")
async def reconcile(request: Request, _: Any = Depends(_operator)) -> dict[str, Any]:
    svc = _service(request)
    if not svc.adapter.configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Zerodha is not configured: nothing to reconcile."
        )
    report = await svc.orders.reconcile()
    return {
        "ok": report.ok,
        "at": report.at,
        "checked": report.checked,
        "changed": report.changed,
        "recovered": report.recovered,
        "lost": report.lost,
        "external": report.external,
        "error": report.error,
    }


@router.post("/connect")
async def connect(request: Request, _: Any = Depends(_admin)) -> dict[str, Any]:
    svc = _service(request)
    if not svc.adapter.configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Zerodha is not configured (ZERODHA_API_KEY / ZERODHA_API_SECRET).",
        )
    await svc.adapter.connect()
    await svc.refresh()
    return svc.status()


@router.post("/disconnect")
async def disconnect(request: Request, _: Any = Depends(_admin)) -> dict[str, Any]:
    svc = _service(request)
    await svc.disconnect()
    return svc.status()


@router.get("/zerodha/login-url")
async def login_url(request: Request, _: Any = Depends(_admin)) -> dict[str, str]:
    try:
        return {"login_url": _service(request).adapter.login_url()}
    except BrokerError as exc:
        raise _http_error(exc) from exc


class SessionRequest(BaseModel):
    request_token: str = Field(min_length=6, max_length=200)


@router.post("/zerodha/session")
async def create_session(
    body: SessionRequest, request: Request, _: Any = Depends(_admin)
) -> dict[str, Any]:
    svc = _service(request)
    try:
        profile = await svc.login(body.request_token)
    except BrokerError as exc:
        raise _http_error(exc) from exc
    return {"profile": profile, "status": svc.status()}


async def _read(request: Request, op: str) -> Any:
    adapter = _service(request).adapter
    try:
        if op == "profile":
            return await adapter.get_profile()
        if op == "balance":
            return await adapter.get_balance()
        if op == "positions":
            return await adapter.get_positions()
        return await adapter.get_orders()
    except BrokerError as exc:
        raise _http_error(exc) from exc


@router.get("/zerodha/profile", response_model=BrokerProfile)
async def profile(request: Request, _: Any = Depends(_operator)) -> BrokerProfile:
    return await _read(request, "profile")


@router.get("/zerodha/balance", response_model=Balance)
async def balance(request: Request, _: Any = Depends(_operator)) -> Balance:
    return await _read(request, "balance")


@router.get("/zerodha/positions", response_model=list[Position])
async def positions(request: Request, _: Any = Depends(_operator)) -> list[Position]:
    return await _read(request, "positions")


@router.get("/zerodha/orders", response_model=list[Order])
async def orders(request: Request, _: Any = Depends(_operator)) -> list[Order]:
    return await _read(request, "orders")
