"""Persistence for paper orders, trades and trading events."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.paper_trading import PaperOrderRecord, PaperTradeRecord
from app.database.models.system_event import SystemEvent
from app.execution.models import Order
from app.risk.manager import IST
from app.trading.schemas import EngineEvent, OrderPurpose, PaperTrade, TradeStatus

OPEN_STATUSES = (TradeStatus.PENDING.value, TradeStatus.OPEN.value, TradeStatus.EXITING.value)


def _trade_values(t: PaperTrade) -> dict:
    return {
        "id": t.id,
        "signal_id": t.signal_id,
        "symbol": t.symbol,
        "side": t.side.value,
        "strategy": t.strategy,
        "score": t.score,
        "status": t.status.value,
        "planned_entry": t.planned_entry,
        "stop_loss": t.stop_loss,
        "target": t.target,
        "quantity": t.quantity,
        "filled_quantity": t.filled_quantity,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "initial_risk": t.initial_risk,
        "gross_pnl": t.gross_pnl,
        "charges": t.charges,
        "net_pnl": t.net_pnl,
        "r_multiple": t.r_multiple,
        "exit_reason": t.exit_reason.value if t.exit_reason else None,
        "trade_created_at": t.created_at,
        "opened_at": t.opened_at,
        "closed_at": t.closed_at,
        "payload": t.model_dump(mode="json"),
    }


def _order_values(o: Order, trade_id: uuid.UUID | None, purpose: OrderPurpose | None) -> dict:
    return {
        "id": o.id,
        "trade_id": trade_id,
        "purpose": purpose.value if purpose else o.tag,
        "client_order_id": o.client_order_id,
        "symbol": o.symbol,
        "side": o.side.value,
        "order_type": o.order_type.value,
        "quantity": o.quantity,
        "price": o.price,
        "trigger_price": o.trigger_price,
        "status": o.status.value,
        "filled_quantity": o.filled_quantity,
        "average_price": o.average_price,
        "charges": o.charges.total,
        "rejection_reason": o.rejection_reason,
        "placed_at": o.placed_at,
        "order_updated_at": o.updated_at,
        "payload": o.model_dump(mode="json"),
    }


async def save_changes(
    session: AsyncSession,
    *,
    orders: list[Order],
    trades: list[PaperTrade],
    events: list[EngineEvent],
    trade_of: Callable[[uuid.UUID], uuid.UUID | None],
    purpose_of: Callable[[uuid.UUID], OrderPurpose | None],
) -> None:
    """Upsert trades, then their orders, then append events — one transaction."""

    for t in trades:
        values = _trade_values(t)
        stmt = insert(PaperTradeRecord).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[PaperTradeRecord.id],
                set_={k: stmt.excluded[k] for k in values if k != "id"},
            )
        )
    for o in orders:
        values = _order_values(o, trade_of(o.id), purpose_of(o.id))
        stmt = insert(PaperOrderRecord).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[PaperOrderRecord.id],
                set_={k: stmt.excluded[k] for k in values if k != "id"},
            )
        )
    for e in events:
        session.add(
            SystemEvent(
                created_at=e.at,  # when it happened, not when the batch was saved
                event_type=f"PAPER_{e.event_type}",
                severity=e.severity,
                message=e.message[:1000],
                event_metadata={**e.metadata, "at": e.at.isoformat(), "mode": "PAPER"},
            )
        )
    await session.commit()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=IST)
    return start, start + timedelta(days=1)


def to_trade(row: PaperTradeRecord) -> PaperTrade:
    trade = PaperTrade.model_validate(row.payload)
    # columns win over the snapshot for anything changed after it (INTERRUPTED)
    trade.status = TradeStatus(row.status)
    return trade


@dataclass
class RestoredState:
    realized_before_today: float  # net P&L of every closed trade before today
    today: list[PaperTrade]  # today's trades, oldest first
    interrupted: list[PaperTrade]  # trades that were open when the process stopped


async def restore(session: AsyncSession, *, day: date, now: datetime) -> RestoredState:
    """Read what the engine needs after a restart; mark open trades INTERRUPTED.

    The paper broker's orders live in memory, so a trade that was open
    when the process stopped cannot be resumed. It is kept as
    INTERRUPTED (P&L unknown) and still counts as a trade taken today.
    """

    rows = (
        (
            await session.execute(
                select(PaperTradeRecord).where(PaperTradeRecord.status.in_(OPEN_STATUSES))
            )
        )
        .scalars()
        .all()
    )
    interrupted = [to_trade(r) for r in rows]
    if rows:
        await session.execute(
            update(PaperTradeRecord)
            .where(PaperTradeRecord.status.in_(OPEN_STATUSES))
            .values(status=TradeStatus.INTERRUPTED.value, closed_at=now)
        )
        await session.commit()
        for t in interrupted:
            t.status = TradeStatus.INTERRUPTED

    start, _ = day_bounds(day)
    realized_before = (
        await session.execute(
            select(func.coalesce(func.sum(PaperTradeRecord.net_pnl), 0)).where(
                PaperTradeRecord.status == TradeStatus.CLOSED.value,
                PaperTradeRecord.trade_created_at < start,
            )
        )
    ).scalar_one()
    today_rows = (
        (
            await session.execute(
                select(PaperTradeRecord)
                .where(PaperTradeRecord.trade_created_at >= start)
                .order_by(PaperTradeRecord.trade_created_at)
            )
        )
        .scalars()
        .all()
    )
    return RestoredState(
        realized_before_today=float(realized_before),
        today=[to_trade(r) for r in today_rows],
        interrupted=interrupted,
    )


async def list_trades(
    session: AsyncSession, *, status: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[PaperTrade], int]:
    query = select(PaperTradeRecord)
    if status:
        query = query.where(PaperTradeRecord.status == status)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(PaperTradeRecord.trade_created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [to_trade(r) for r in rows], total


async def get_trade(session: AsyncSession, trade_id: uuid.UUID) -> PaperTrade | None:
    row = await session.get(PaperTradeRecord, trade_id)
    return to_trade(row) if row is not None else None


@dataclass
class OrderRow:
    order: Order
    trade_id: uuid.UUID | None
    purpose: str | None


async def list_orders(
    session: AsyncSession, *, trade_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0
) -> tuple[list[OrderRow], int]:
    query = select(PaperOrderRecord)
    if trade_id:
        query = query.where(PaperOrderRecord.trade_id == trade_id)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(PaperOrderRecord.placed_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [
        OrderRow(order=Order.model_validate(r.payload), trade_id=r.trade_id, purpose=r.purpose)
        for r in rows
    ], total
