"""Approval endpoints (Phase 12). Approving sends a PAPER order only.

GET  /approvals                     requests (filter by status), newest first,
                                    with counts, the mode and the expiry window
GET  /approvals/{id}                one request
POST /approvals                     ask for approval of a stored signal
                                    (TRADER / ADMIN; APPROVAL mode)
POST /approvals/{id}/approve        APPROVE TRADE (TRADER / ADMIN)
POST /approvals/{id}/reject         REJECT TRADE with a reason (TRADER / ADMIN)

Every decision is audited (audit_logs) and stored on the request:
approved_by / approved_at, rejected_by / rejected_at, reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import require_role
from app.approvals.schemas import APPROVAL_NOTE, ApprovalRequest, ApprovalSource, ApprovalStatus
from app.approvals.service import Actor, ApprovalError, ApprovalRefused, ApprovalService
from app.core.constants import UserRole
from app.database.models.user import User
from app.trading.service import SignalNotFound

router = APIRouter(prefix="/approvals", tags=["approvals"])
_operator = require_role(UserRole.TRADER, UserRole.ADMIN)


def _service(request: Request) -> ApprovalService:
    svc: ApprovalService | None = getattr(request.app.state, "approval_service", None)
    if svc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Approval service is not running.")
    return svc


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ApprovalRefused):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, {"message": str(exc), "reasons": exc.reasons}
        )
    if isinstance(exc, ApprovalError):
        return HTTPException(exc.status_code, str(exc))
    if isinstance(exc, SignalNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise exc


def _actor(user: User) -> Actor:
    return Actor(user.id, user.email)


class ApprovalView(ApprovalRequest):
    seconds_remaining: float


def _view(svc: ApprovalService, a: ApprovalRequest) -> ApprovalView:
    left = a.seconds_left(svc.now()) if a.status is ApprovalStatus.PENDING else 0.0
    return ApprovalView(**a.model_dump(), seconds_remaining=round(left, 1))


class ApprovalList(BaseModel):
    items: list[ApprovalView]
    total: int
    counts: dict[str, int]
    mode: str
    ttl_seconds: int
    server_time: datetime
    note: str = APPROVAL_NOTE


@router.get("", response_model=ApprovalList)
async def list_approvals(
    request: Request,
    status_: list[ApprovalStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ApprovalList:
    svc = _service(request)
    items, total = await svc.list(status_, limit=limit, offset=offset)
    return ApprovalList(
        items=[_view(svc, a) for a in items],
        total=total,
        counts=await svc.counts(),
        mode=svc.trading.mode,
        ttl_seconds=svc.ttl_seconds,
        server_time=svc.now(),
    )


@router.get("/{approval_id}", response_model=ApprovalView)
async def get_approval(approval_id: uuid.UUID, request: Request) -> ApprovalView:
    svc = _service(request)
    try:
        return _view(svc, await svc.get(approval_id))
    except ApprovalError as exc:
        raise _error(exc) from exc


class ProposeBody(BaseModel):
    signal_id: uuid.UUID


@router.post("", response_model=ApprovalView, status_code=status.HTTP_201_CREATED)
async def propose(
    body: ProposeBody, request: Request, user: User = Depends(_operator)
) -> ApprovalView:
    svc = _service(request)
    try:
        approval = await svc.propose(
            body.signal_id, source=ApprovalSource.MANUAL, actor=_actor(user)
        )
    except (ApprovalError, SignalNotFound) as exc:
        raise _error(exc) from exc
    return _view(svc, approval)


class ApproveBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/{approval_id}/approve", response_model=ApprovalView)
async def approve(
    approval_id: uuid.UUID,
    request: Request,
    body: ApproveBody | None = None,
    user: User = Depends(_operator),
) -> ApprovalView:
    svc = _service(request)
    try:
        approval = await svc.approve(approval_id, _actor(user), note=body.note if body else None)
    except ApprovalError as exc:
        raise _error(exc) from exc
    return _view(svc, approval)


class RejectBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{approval_id}/reject", response_model=ApprovalView)
async def reject(
    approval_id: uuid.UUID, body: RejectBody, request: Request, user: User = Depends(_operator)
) -> ApprovalView:
    svc = _service(request)
    try:
        approval = await svc.reject(approval_id, _actor(user), body.reason.strip())
    except ApprovalError as exc:
        raise _error(exc) from exc
    return _view(svc, approval)
