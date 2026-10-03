"""Storage for approval requests. Status changes are conditional updates
(`WHERE status = <expected>`), so two people acting on one request at the
same time cannot both succeed."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.schemas import (
    ApprovalRequest,
    ApprovalSource,
    ApprovalStatus,
    check_transition,
)
from app.database.models.audit_log import AuditLog
from app.database.models.trade_approval import TradeApproval

_FLOATS = (
    "entry",
    "stop_loss",
    "target",
    "risk_amount",
    "risk_per_share",
    "position_value",
    "reward_risk",
    "score",
    "regime_confidence",
)


def to_model(row: TradeApproval) -> ApprovalRequest:
    data: dict[str, Any] = {
        c: getattr(row, c) for c in ApprovalRequest.model_fields if hasattr(row, c)
    }
    for k in _FLOATS:
        if data.get(k) is not None:
            data[k] = float(data[k])
    data["status"] = ApprovalStatus(row.status)
    data["source"] = ApprovalSource(row.source)
    return ApprovalRequest(**data)


async def create(session: AsyncSession, **values: Any) -> ApprovalRequest:
    row = TradeApproval(id=uuid.uuid4(), status=ApprovalStatus.PENDING.value, **values)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return to_model(row)


async def get(session: AsyncSession, approval_id: uuid.UUID) -> ApprovalRequest | None:
    row = await session.get(TradeApproval, approval_id)
    return to_model(row) if row else None


async def open_for_signal(session: AsyncSession, signal_id: uuid.UUID) -> ApprovalRequest | None:
    row = (
        await session.execute(
            select(TradeApproval).where(
                TradeApproval.signal_id == signal_id,
                TradeApproval.status.in_(
                    [ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]
                ),
            )
        )
    ).scalar_one_or_none()
    return to_model(row) if row else None


async def transition(
    session: AsyncSession,
    approval_id: uuid.UUID,
    current: ApprovalStatus,
    new: ApprovalStatus,
    **fields: Any,
) -> ApprovalRequest | None:
    """Move PENDING -> APPROVED etc. Returns None if the row was not in
    `current` any more (someone else acted first)."""

    check_transition(current, new)
    result = await session.execute(
        update(TradeApproval)
        .where(TradeApproval.id == approval_id, TradeApproval.status == current.value)
        .values(status=new.value, **fields)
        .returning(TradeApproval.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        return None
    await session.commit()
    return await get(session, approval_id)


async def audit(
    session: AsyncSession,
    action: str,
    approval: ApprovalRequest,
    *,
    user_id: uuid.UUID | None,
    before: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    after = {
        "approval_id": str(approval.id),
        "status": approval.status.value,
        "signal_id": str(approval.signal_id) if approval.signal_id else None,
        "symbol": approval.symbol,
        "direction": approval.direction,
        "entry": approval.entry,
        "stop_loss": approval.stop_loss,
        "target": approval.target,
        "quantity": approval.quantity,
        "risk_amount": approval.risk_amount,
        "reason": approval.reason,
        "actor": "system" if user_id is None else "user",
        **(extra or {}),
    }
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            before={"status": before} if before else None,
            after=after,
        )
    )
    await session.commit()


async def list_requests(
    session: AsyncSession,
    *,
    statuses: list[ApprovalStatus] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ApprovalRequest], int]:
    q = select(TradeApproval)
    if statuses:
        q = q.where(TradeApproval.status.in_([s.value for s in statuses]))
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                q.order_by(TradeApproval.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [to_model(r) for r in rows], total


async def pending(session: AsyncSession) -> list[ApprovalRequest]:
    rows = (
        (
            await session.execute(
                select(TradeApproval)
                .where(TradeApproval.status == ApprovalStatus.PENDING.value)
                .order_by(TradeApproval.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [to_model(r) for r in rows]


async def counts(session: AsyncSession, since: datetime | None = None) -> dict[str, int]:
    q = select(TradeApproval.status, func.count()).group_by(TradeApproval.status)
    if since is not None:
        q = q.where(TradeApproval.created_at >= since)
    return {status: n for status, n in (await session.execute(q)).all()}
