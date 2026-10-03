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

Kite login flow (Phase 13):
GET  /broker/zerodha/login          Kite login URL with a one-time state (ADMIN);
                                    ?redirect=true answers with a 307 instead
GET  /broker/zerodha/callback       Kite redirects the browser here with
                                    request_token + state; the token is
                                    exchanged server-side and the browser is
                                    sent to FRONTEND_URL/settings?zerodha=...
                                    (no token ever appears in that URL)
GET  /broker/zerodha/status         safe status: connected, masked user id,
                                    market data and execution mode
POST /broker/zerodha/logout         invalidate the Kite session (ADMIN)

No response contains the API secret, the access token or the request token.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import require_role
from app.brokers.service import BrokerService
from app.brokers.zerodha.auth import KiteLoginError
from app.brokers.zerodha.exceptions import KiteError
from app.brokers.zerodha.runtime import ZerodhaRuntime
from app.core.config import get_settings
from app.core.constants import UserRole
from app.core.logging import get_logger
from app.execution.broker import (
    BrokerAuthError,
    BrokerError,
    BrokerNotConfiguredError,
    BrokerRateLimitError,
    BrokerUnavailableError,
)
from app.execution.models import Balance, BrokerProfile, Order, Position
from app.execution.order_manager import OrderEvent

logger = get_logger(__name__)
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


@router.get("/reconciliation")
async def last_reconciliation(request: Request, _: Any = Depends(_operator)) -> dict[str, Any]:
    last = _service(request).reconciliation.last
    return {"result": last.model_dump(mode="json") if last else None}


@router.post("/reconciliation")
async def run_reconciliation(request: Request, _: Any = Depends(_operator)) -> dict[str, Any]:
    """Compare local orders / positions / trades with the broker's (read-only)."""

    svc = _service(request)
    if not svc.adapter.configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Zerodha is not configured: nothing to reconcile."
        )
    result = await svc.reconciliation.run()
    return {"result": result.model_dump(mode="json")}


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


# --- Kite login flow (Phase 13) ------------------------------------------------------


def _runtime(request: Request) -> ZerodhaRuntime:
    rt: ZerodhaRuntime | None = getattr(request.app.state, "zerodha", None)
    if rt is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Zerodha runtime is not running.")
    return rt


def market_data_label(request: Request) -> str:
    """CONNECTED / STALE / DISCONNECTED for real data, SIMULATED otherwise."""

    md = getattr(request.app.state, "market_data_service", None)
    if md is None:
        return "DISCONNECTED"
    if md.simulated:
        return "SIMULATED"
    return {"LIVE": "CONNECTED", "STALE": "STALE"}.get(md.data_status(), "DISCONNECTED")


def zerodha_safe_status(request: Request) -> dict[str, Any]:
    settings = get_settings()
    return _runtime(request).safe_status(
        market_data=market_data_label(request),
        execution_mode=settings.effective_execution_mode.value,
    )


@router.get("/zerodha/status")
async def zerodha_status(request: Request) -> dict[str, Any]:
    return zerodha_safe_status(request)


@router.get("/zerodha/login", response_model=None)
async def zerodha_login(
    request: Request,
    redirect: bool = Query(default=False),
    _: Any = Depends(_admin),
) -> dict[str, str] | RedirectResponse:
    rt = _runtime(request)
    if not rt.configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Zerodha is not configured (KITE_API_KEY / KITE_API_SECRET on the backend).",
        )
    url = rt.auth.get_login_url()
    if redirect:
        return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    return {"login_url": url}


def _frontend(result: str, reason: str | None = None) -> RedirectResponse:
    query = {"zerodha": result}
    if reason:
        query["reason"] = reason
    base = get_settings().frontend_url.rstrip("/")
    return RedirectResponse(f"{base}/settings?{urlencode(query)}", status_code=303)


@router.get("/zerodha/callback", include_in_schema=False)
async def zerodha_callback(
    request: Request,
    request_token: str | None = Query(default=None, max_length=200),
    state: str | None = Query(default=None, max_length=200),
    status_: str | None = Query(default=None, alias="status", max_length=40),
) -> RedirectResponse:
    """Kite's redirect target (KITE_REDIRECT_URL). No JWT: the browser comes
    here straight from Zerodha; the one-time `state` issued by /login is the
    proof that an admin started this login."""

    rt = _runtime(request)
    if not rt.configured:
        return _frontend("error", "not_configured")
    try:
        await rt.auth.handle_request_token(request_token, status=status_, state=state)
    except KiteLoginError as exc:
        reason = (
            "state" if "state" in str(exc).lower() or "expired" in str(exc).lower() else "login"
        )
        return _frontend("error", reason)
    except KiteError:
        return _frontend("error", "token_exchange")
    return _frontend("connected")


@router.post("/zerodha/logout")
async def zerodha_logout(request: Request, _: Any = Depends(_admin)) -> dict[str, Any]:
    rt = _runtime(request)
    if not rt.configured:
        raise HTTPException(status.HTTP_409_CONFLICT, "Zerodha is not configured.")
    await rt.auth.logout()
    return zerodha_safe_status(request)
