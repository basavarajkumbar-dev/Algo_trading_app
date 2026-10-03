"""Live trading safety layer endpoints (Phase 14).

GET  /live/status                      arming, kill switch, health, active trade,
                                       limits, why arming is blocked
POST /live/arm            (ADMIN)      explicit arming: typed confirmation
                                       "ARM LIVE TRADING" + the admin's password
POST /live/disarm         (TRADER/ADMIN)
POST /live/preflight      (TRADER/ADMIN) every pre-trade check for a signal;
                                       never places an order
POST /live/execute        (ADMIN)      run the checks and, only if all pass,
                                       send the live entry for a signal
POST /live/auto           (ADMIN)      live auto trading on / off (needs arming)
POST /live/exit           (TRADER/ADMIN) close the active live trade at market
POST /live/kill-switch    (TRADER/ADMIN) stop live trading now
POST /live/kill-switch/reset (ADMIN)   clear the kill switch (does not re-arm)
POST /live/recovery/acknowledge (ADMIN) a live trade left by a previous run was
                                       handled in Kite
GET  /live/audit          (TRADER/ADMIN) recent live audit entries

Every action is written to audit_logs. Nothing here exists in PAPER mode
beyond status: arming refuses unless EXECUTION_MODE=ZERODHA and
LIVE_TRADING_ENABLED=true.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.constants import UserRole
from app.core.security import verify_password
from app.database.models.audit_log import AuditLog
from app.database.models.user import User
from app.database.session import get_db
from app.live.arming import ARM_CONFIRMATION
from app.live.service import LiveTradingError, LiveTradingService
from app.trading.service import SignalNotFound

router = APIRouter(prefix="/live", tags=["live"])
_admin = require_role(UserRole.ADMIN)
_operator = require_role(UserRole.TRADER, UserRole.ADMIN)


def _svc(request: Request) -> LiveTradingService:
    svc: LiveTradingService | None = getattr(request.app.state, "live_service", None)
    if svc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Live service is not running.")
    return svc


@router.get("/status")
async def live_status(request: Request) -> dict[str, Any]:
    return {**_svc(request).status(), "confirmation_phrase": ARM_CONFIRMATION}


class ArmRequest(BaseModel):
    confirmation: str = Field(max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/arm")
async def arm(body: ArmRequest, request: Request, user: User = Depends(_admin)) -> dict[str, Any]:
    svc = _svc(request)
    if body.confirmation.strip() != ARM_CONFIRMATION:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f'Type "{ARM_CONFIRMATION}" to confirm.'
        )
    if not verify_password(body.password, user.hashed_password):
        await svc._audit(user.id, "LIVE_ARM_REFUSED", {"reason": "wrong password"})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password incorrect.")
    try:
        await svc.arm(user_id=user.id, email=user.email)
    except LiveTradingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return svc.status()


class ReasonBody(BaseModel):
    reason: str = Field(default="manual", max_length=300)


@router.post("/disarm")
async def disarm(
    request: Request, body: ReasonBody | None = None, user: User = Depends(_operator)
) -> dict[str, Any]:
    svc = _svc(request)
    await svc.disarm(user_id=user.id, reason=(body.reason if body else "manual"))
    return svc.status()


class SignalBody(BaseModel):
    signal_id: uuid.UUID


@router.post("/preflight")
async def preflight(
    body: SignalBody, request: Request, _: User = Depends(_operator)
) -> dict[str, Any]:
    try:
        decision, report = await _svc(request).preflight(body.signal_id)
    except SignalNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {
        "report": report.model_dump(mode="json"),
        "risk": decision.model_dump(mode="json"),
        "order_placed": False,
    }


@router.post("/execute")
async def execute(
    body: SignalBody, request: Request, user: User = Depends(_admin)
) -> dict[str, Any]:
    svc = _svc(request)
    try:
        report, trade = await svc.execute_signal(body.signal_id, user_id=user.id)
    except SignalNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {
        "report": report.model_dump(mode="json"),
        "order_placed": trade is not None and trade.entry_order_id is not None,
        "trade": trade.model_dump(mode="json") if trade else None,
    }


class AutoBody(BaseModel):
    enabled: bool


@router.post("/auto")
async def auto(body: AutoBody, request: Request, user: User = Depends(_admin)) -> dict[str, Any]:
    svc = _svc(request)
    try:
        await svc.set_auto(body.enabled, user_id=user.id)
    except LiveTradingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return svc.status()


@router.post("/exit")
async def exit_trade(request: Request, user: User = Depends(_operator)) -> dict[str, Any]:
    svc = _svc(request)
    if not await svc.request_exit(user_id=user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "No active live trade.")
    return svc.status()


class KillBody(BaseModel):
    reason: str = Field(default="manual kill switch", max_length=300)
    close_positions: bool | None = None  # None: LIVE_CLOSE_POSITIONS_ON_KILL


@router.post("/kill-switch")
async def kill_switch(
    request: Request, body: KillBody | None = None, user: User = Depends(_operator)
) -> dict[str, Any]:
    svc = _svc(request)
    b = body or KillBody()
    result = await svc.kill(reason=b.reason, user_id=user.id, close_positions=b.close_positions)
    return {"result": result.model_dump(mode="json"), "status": svc.status()}


@router.post("/kill-switch/reset")
async def reset_kill_switch(request: Request, user: User = Depends(_admin)) -> dict[str, Any]:
    svc = _svc(request)
    await svc.reset_kill(user_id=user.id)
    return svc.status()


class AckBody(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/recovery/acknowledge")
async def acknowledge(
    body: AckBody, request: Request, user: User = Depends(_admin)
) -> dict[str, Any]:
    svc = _svc(request)
    await svc.acknowledge_unresolved(user_id=user.id, note=body.note)
    return svc.status()


@router.get("/audit")
async def audit(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_operator),
) -> dict[str, Any]:
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action.like("LIVE_%"))
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "action": r.action,
                "user_id": str(r.user_id) if r.user_id else None,
                "details": r.after,
                "at": r.created_at,
            }
            for r in rows
        ]
    }
