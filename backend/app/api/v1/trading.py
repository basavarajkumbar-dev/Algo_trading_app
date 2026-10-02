"""Paper trading and risk endpoints.

Read (unauthenticated, like the other simulated-data endpoints):
GET  /trading/status          state machine, active trade, balance, risk, health
GET  /trading/risk            risk limits, today's counters, last decision
GET  /trading/positions       open paper positions and the balance
GET  /trading/orders          stored paper orders, newest first
GET  /trading/trades          stored paper trades, newest first
GET  /trading/trades/{id}     one paper trade

Act (TRADER or ADMIN; each action is written to the audit log):
POST /trading/signals/{id}/execute   SIGNAL -> RISK -> paper ORDER
POST /trading/auto                   automatic paper trading on / off
POST /trading/exit                   close the active trade at market
POST /trading/emergency-stop         disable new trading, cancel pending entries
                                     (closes the position only if asked)
POST /trading/emergency-stop/reset   allow new trading again

PAPER only: every order goes to the simulated paper broker.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.constants import UserRole
from app.database.models.audit_log import AuditLog
from app.database.models.user import User
from app.database.session import get_db
from app.execution.models import Balance, Order, Position
from app.risk.config import RiskLimits
from app.risk.schemas import RISK_NOTE, RiskDecision, RiskSnapshot
from app.trading.engine import EngineError
from app.trading.repository import get_trade, list_orders, list_trades
from app.trading.schemas import PAPER_NOTE, PaperTrade, TradeStatus
from app.trading.service import SignalNotFound, TradingService, TradingStatus

router = APIRouter(prefix="/trading", tags=["paper trading"])

_operator = require_role(UserRole.TRADER, UserRole.ADMIN)


def _service(request: Request) -> TradingService:
    service: TradingService | None = getattr(request.app.state, "trading_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paper trading service is not running.",
        )
    return service


def _db_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Trade storage unavailable: {type(exc).__name__}",
    )


async def _audit(db: AsyncSession, user: User, action: str, after: dict[str, Any]) -> None:
    try:
        db.add(AuditLog(user_id=user.id, action=action, before=None, after=after))
        await db.commit()
    except SQLAlchemyError:  # the action already happened; do not fail the response
        await db.rollback()


# --- read ------------------------------------------------------------------


@router.get("/status", response_model=TradingStatus)
async def trading_status(request: Request) -> TradingStatus:
    return await _service(request).status()


class RiskResponse(BaseModel):
    limits: RiskLimits
    snapshot: RiskSnapshot
    last_decision: RiskDecision | None
    note: str = RISK_NOTE


@router.get("/risk", response_model=RiskResponse)
async def trading_risk(request: Request) -> RiskResponse:
    st = await _service(request).status()
    return RiskResponse(
        limits=_service(request).engine.risk.limits,
        snapshot=st.risk,
        last_decision=st.last_decision,
    )


class PositionsResponse(BaseModel):
    positions: list[Position]
    balance: Balance
    note: str = PAPER_NOTE


@router.get("/positions", response_model=PositionsResponse)
async def trading_positions(request: Request) -> PositionsResponse:
    broker = _service(request).engine.broker
    return PositionsResponse(
        positions=[p for p in broker.positions() if p.quantity != 0], balance=broker.balance()
    )


class StoredOrder(BaseModel):
    order: Order
    trade_id: uuid.UUID | None
    purpose: str | None


class OrderList(BaseModel):
    items: list[StoredOrder]
    total: int
    limit: int
    offset: int
    note: str = PAPER_NOTE


@router.get("/orders", response_model=OrderList)
async def trading_orders(
    trade_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> OrderList:
    try:
        rows, total = await list_orders(db, trade_id=trade_id, limit=limit, offset=offset)
    except (SQLAlchemyError, OSError) as exc:
        raise _db_unavailable(exc) from exc
    return OrderList(
        items=[StoredOrder(order=r.order, trade_id=r.trade_id, purpose=r.purpose) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


class TradeList(BaseModel):
    items: list[PaperTrade]
    total: int
    limit: int
    offset: int
    note: str = PAPER_NOTE


@router.get("/trades", response_model=TradeList)
async def trading_trades(
    status_filter: TradeStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TradeList:
    try:
        items, total = await list_trades(
            db,
            status=status_filter.value if status_filter else None,
            limit=limit,
            offset=offset,
        )
    except (SQLAlchemyError, OSError) as exc:
        raise _db_unavailable(exc) from exc
    return TradeList(items=items, total=total, limit=limit, offset=offset)


@router.get("/trades/{trade_id}", response_model=PaperTrade)
async def trading_trade(trade_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> PaperTrade:
    try:
        trade = await get_trade(db, trade_id)
    except (SQLAlchemyError, OSError) as exc:
        raise _db_unavailable(exc) from exc
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    return trade


# --- act -------------------------------------------------------------------


class ExecuteResponse(BaseModel):
    decision: RiskDecision
    status: TradingStatus


@router.post("/signals/{signal_id}/execute", response_model=ExecuteResponse)
async def execute_signal(
    signal_id: uuid.UUID,
    request: Request,
    user: User = Depends(_operator),
    db: AsyncSession = Depends(get_db),
) -> ExecuteResponse:
    """Evaluate a stored signal and, if every risk check passes, place a
    PAPER entry order. A rejection is a normal result (approved=false
    with every failed check), not an error."""

    service = _service(request)
    try:
        decision = await service.execute_signal(signal_id)
    except SignalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _audit(
        db,
        user,
        "PAPER_EXECUTE_SIGNAL",
        {
            "signal_id": str(signal_id),
            "approved": decision.approved,
            "reasons": [r.value for r in decision.rejection_reasons],
            "quantity": decision.size.quantity if decision.size else None,
        },
    )
    return ExecuteResponse(decision=decision, status=await service.status())


class AutoRequest(BaseModel):
    enabled: bool


@router.post("/auto", response_model=TradingStatus)
async def set_auto(
    body: AutoRequest,
    request: Request,
    user: User = Depends(_operator),
    db: AsyncSession = Depends(get_db),
) -> TradingStatus:
    service = _service(request)
    try:
        await service.set_auto_trading(body.enabled)
    except EngineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _audit(db, user, "PAPER_AUTO_TRADING", {"enabled": body.enabled})
    return await service.status()


@router.post("/exit", response_model=TradingStatus)
async def exit_position(
    request: Request, user: User = Depends(_operator), db: AsyncSession = Depends(get_db)
) -> TradingStatus:
    service = _service(request)
    try:
        await service.exit_position()
    except EngineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _audit(db, user, "PAPER_MANUAL_EXIT", {})
    return await service.status()


class EmergencyRequest(BaseModel):
    reason: str = Field(default="operator request", max_length=300)
    # Also close the active position at market. Omitted: the configured
    # default (PAPER_EMERGENCY_CLOSE_POSITIONS, off), so positions are not
    # closed unless asked.
    close_position: bool | None = None


@router.post("/emergency-stop", response_model=TradingStatus)
async def emergency_stop(
    request: Request,
    body: EmergencyRequest | None = None,
    user: User = Depends(_operator),
    db: AsyncSession = Depends(get_db),
) -> TradingStatus:
    """Disable new trading and cancel pending entry orders. An open
    position keeps its stop loss and target and is closed only when
    `close_position` is true (or configured)."""

    service = _service(request)
    reason = (body.reason if body else None) or "operator request"
    close = body.close_position if body else None
    await service.emergency_stop(f"{reason} ({user.email})", close_position=close)
    await _audit(db, user, "PAPER_EMERGENCY_STOP", {"reason": reason, "close_position": close})
    return await service.status()


@router.post("/emergency-stop/reset", response_model=TradingStatus)
async def reset_emergency_stop(
    request: Request, user: User = Depends(_operator), db: AsyncSession = Depends(get_db)
) -> TradingStatus:
    service = _service(request)
    try:
        await service.reset_emergency_stop()
    except EngineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _audit(db, user, "PAPER_EMERGENCY_STOP_RESET", {})
    return await service.status()
