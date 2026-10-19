"""LiveOrderManager: one live trade's orders, through the OrderManager.

    entry (LIMIT at the signal entry, MIS)
      -> filled (fully or partly): protective SL-M stop + LIMIT target for
         the filled quantity, kept sized to the open quantity
      -> one exit leg fills: the other is reduced / cancelled
      -> square-off time, manual exit or a kill switch that asks for it:
         both legs cancelled, MARKET exit for the open quantity
      -> flat: CLOSED with average entry / exit and gross P&L

Rules
  * Every order goes through OrderManager (gate, idempotency, reconciliation);
    this class never touches the broker adapter.
  * Client order ids are derived from the correlation id
    ("<correlation>-ENTRY", "-SL", "-TGT", "-EXIT1"), and the correlation id
    from the signal, so the same signal can never produce a second entry.
  * An order whose outcome is unknown is never re-sent; it is reconciled.
  * Exits are reduce-only: they may go out under the kill switch, after a
    disarm or outside the entry window, but never for more than the open
    quantity.
  * A protective stop is never cancelled while a position is open, except
    to replace it with a market exit.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.execution.models import (
    Order,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Product,
    Side,
)
from app.execution.order_manager import OrderManager
from app.market_data.base import utcnow
from app.risk.schemas import TradeSide

AuditFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class LiveTradeStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"  # entry never filled
    FAILED = "FAILED"  # entry refused / rejected

    @property
    def is_final(self) -> bool:
        return self in (LiveTradeStatus.CLOSED, LiveTradeStatus.CANCELLED, LiveTradeStatus.FAILED)


class LiveExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TARGET = "TARGET"
    SQUARE_OFF = "SQUARE_OFF"
    MANUAL = "MANUAL"
    KILL_SWITCH = "KILL_SWITCH"
    ENTRY_TIMEOUT = "ENTRY_TIMEOUT"
    ENTRY_REJECTED = "ENTRY_REJECTED"


class LiveTrade(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: str
    signal_id: uuid.UUID | None = None
    symbol: str
    exchange: str = "NSE"
    side: TradeSide
    quantity: int
    planned_entry: float
    stop_loss: float
    target: float
    tick_size: float = 0.05
    # "LIMIT": a limit order at `entry_limit` (or the planned entry).
    # "SL_M": a stop-market entry that waits for price to trade through
    # `trigger_price` (STOP-entry strategies, before the trigger is hit).
    entry_order_type: str = "LIMIT"
    entry_limit: float | None = None
    trigger_price: float | None = None
    entry_timeout_seconds: float | None = None  # overrides the manager's default
    status: LiveTradeStatus = LiveTradeStatus.PENDING_ENTRY
    entry_order_id: uuid.UUID | None = None
    stop_order_id: uuid.UUID | None = None
    target_order_id: uuid.UUID | None = None
    exit_order_ids: list[uuid.UUID] = Field(default_factory=list)
    filled_quantity: int = 0  # entry fills
    exited_quantity: int = 0
    average_entry: float | None = None
    average_exit: float | None = None
    gross_pnl: float | None = None
    exit_reason: LiveExitReason | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    armed_by: uuid.UUID | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def open_quantity(self) -> int:
        return max(self.filled_quantity - self.exited_quantity, 0)

    @property
    def entry_side(self) -> Side:
        return Side.BUY if self.side == TradeSide.LONG else Side.SELL

    @property
    def exit_side(self) -> Side:
        return Side.SELL if self.side == TradeSide.LONG else Side.BUY


def correlation_for(signal_id: uuid.UUID | None) -> str:
    return f"L{(signal_id or uuid.uuid4()).hex[:16]}"


def to_tick(price: float, tick: float, *, up: bool | None = None) -> float:
    steps = price / tick
    n = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9) if up is False else round(steps)
    return round(n * tick, 2)


class LiveOrderManager:
    def __init__(
        self,
        orders: OrderManager,
        *,
        entry_timeout: timedelta,
        square_off_due: Callable[[datetime], bool],
        audit: AuditFn | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.orders = orders
        self.entry_timeout = entry_timeout
        self._square_off_due = square_off_due
        self._audit = audit
        self._clock = clock
        self.active: LiveTrade | None = None
        self.closed: list[LiveTrade] = []
        self._pending_exit: LiveExitReason | None = None
        self._changed = False

    # --- helpers ------------------------------------------------------------

    async def audit(self, action: str, **details: Any) -> None:
        if self._audit is not None:
            await self._audit(action, details)

    def _order(self, order_id: uuid.UUID | None) -> Order | None:
        if order_id is None:
            return None
        try:
            return self.orders.order(order_id)
        except KeyError:
            return None

    def _request(
        self, trade: LiveTrade, suffix: str, side: Side, qty: int, otype: OrderType, **kw: Any
    ) -> OrderRequest:
        return OrderRequest(
            symbol=trade.symbol,
            side=side,
            quantity=qty,
            order_type=otype,
            client_order_id=f"{trade.correlation_id}-{suffix}",
            tag=suffix.rstrip("0123456789"),
            strategy_signal_id=str(trade.signal_id) if trade.signal_id else None,
            correlation_id=trade.correlation_id,
            exchange=trade.exchange,
            product=Product.MIS,
            **kw,
        )

    def take_changed(self) -> bool:
        changed, self._changed = self._changed, False
        return changed

    # --- entry ------------------------------------------------------------------

    async def open_trade(self, trade: LiveTrade) -> LiveTrade:
        if self.active is not None and not self.active.status.is_final:
            raise RuntimeError("a live trade is already active")
        if trade.entry_order_type == "SL_M" and trade.trigger_price:
            price = to_tick(trade.trigger_price, trade.tick_size)
            request = self._request(
                trade,
                "ENTRY",
                trade.entry_side,
                trade.quantity,
                OrderType.SL_M,
                trigger_price=price,
            )
        else:
            price = to_tick(trade.entry_limit or trade.planned_entry, trade.tick_size)
            request = self._request(
                trade, "ENTRY", trade.entry_side, trade.quantity, OrderType.LIMIT, price=price
            )
        order = await self.orders.submit(request, risk_approved=True)
        trade.entry_order_id = order.id
        self.active = trade
        self._changed = True
        await self.audit(
            "LIVE_ORDER_PLACED",
            purpose="ENTRY",
            correlation_id=trade.correlation_id,
            client_order_id=request.client_order_id,
            symbol=trade.symbol,
            side=request.side.value,
            quantity=request.quantity,
            price=price,
            status=order.status.value,
            broker_order_id=order.broker_order_id,
            outcome_unknown=order.needs_reconciliation,
            rejection=order.rejection_reason,
        )
        if order.status is OrderStatus.REJECTED:
            trade.status = LiveTradeStatus.FAILED
            trade.exit_reason = LiveExitReason.ENTRY_REJECTED
            trade.notes.append(f"entry rejected: {order.rejection_reason}")
            self._finish(trade)
        return trade

    # --- lifecycle ------------------------------------------------------------------

    async def sync(self) -> None:
        """Reconcile with the broker, then advance the active trade."""

        await self.orders.reconcile()
        trade = self.active
        if trade is None or trade.status.is_final:
            return
        now = self._clock()
        entry = self._order(trade.entry_order_id)
        if entry is None:
            return
        if entry.needs_reconciliation:
            return  # outcome unknown: wait for reconciliation, never re-send
        if entry.filled_quantity != trade.filled_quantity:
            trade.filled_quantity = entry.filled_quantity
            trade.average_entry = entry.average_price
            self._changed = True
            await self.audit(
                "LIVE_ENTRY_FILL",
                correlation_id=trade.correlation_id,
                filled=entry.filled_quantity,
                average=entry.average_price,
            )
        if trade.status == LiveTradeStatus.PENDING_ENTRY and trade.filled_quantity > 0:
            trade.status = LiveTradeStatus.OPEN
            self._changed = True

        # entry not (fully) filled in time: cancel the remainder
        timeout = (
            timedelta(seconds=trade.entry_timeout_seconds)
            if trade.entry_timeout_seconds
            else self.entry_timeout
        )
        if entry.status.is_working and now - entry.placed_at >= timeout:
            await self._cancel(entry, "entry timeout")
            entry = self._order(trade.entry_order_id) or entry
        if not entry.status.is_working and trade.filled_quantity == 0:
            trade.status = (
                LiveTradeStatus.FAILED
                if entry.status is OrderStatus.REJECTED
                else LiveTradeStatus.CANCELLED
            )
            trade.exit_reason = (
                LiveExitReason.ENTRY_REJECTED
                if entry.status is OrderStatus.REJECTED
                else LiveExitReason.ENTRY_TIMEOUT
            )
            trade.notes.append(
                f"entry {entry.status.value}: {entry.rejection_reason or entry.status_message or ''}"
            )
            self._finish(trade)
            return

        await self._account_exits(trade)
        if trade.open_quantity == 0 and trade.filled_quantity > 0 and not entry.status.is_working:
            await self._close(trade)
            return

        if self._pending_exit is None and self._square_off_due(now) and trade.open_quantity > 0:
            self._pending_exit = LiveExitReason.SQUARE_OFF
        if self._pending_exit is not None:
            await self._exit_at_market(trade, self._pending_exit)
            return
        await self._ensure_protection(trade)

    async def _account_exits(self, trade: LiveTrade) -> None:
        """Exit fills from the stop, target and market exits."""

        legs = [
            self._order(i)
            for i in (trade.stop_order_id, trade.target_order_id, *trade.exit_order_ids)
        ]
        filled = [o for o in legs if o is not None and o.filled_quantity > 0]
        exited = sum(o.filled_quantity for o in filled)
        if exited != trade.exited_quantity:
            value = sum((o.average_price or 0.0) * o.filled_quantity for o in filled)
            trade.exited_quantity = exited
            trade.average_exit = round(value / exited, 4) if exited else None
            self._changed = True
            await self.audit(
                "LIVE_EXIT_FILL",
                correlation_id=trade.correlation_id,
                exited=exited,
                average=trade.average_exit,
            )
        stop = self._order(trade.stop_order_id)
        target = self._order(trade.target_order_id)
        if trade.exit_reason is None:
            if stop is not None and stop.filled_quantity > 0:
                trade.exit_reason = LiveExitReason.STOP_LOSS
            elif target is not None and target.filled_quantity > 0:
                trade.exit_reason = LiveExitReason.TARGET

    async def _ensure_protection(self, trade: LiveTrade) -> None:
        """Stop and target sized to the open quantity (one-cancels-other)."""

        open_qty = trade.open_quantity
        if open_qty <= 0:
            return
        stop = self._order(trade.stop_order_id)
        target = self._order(trade.target_order_id)
        # place the protective stop first, then the target
        if stop is None or (not stop.status.is_working and stop.filled_quantity == 0):
            if stop is not None:
                trade.notes.append(f"stop {stop.status.value}: re-placing")
            await self._place_leg(trade, "SL", open_qty)
        elif stop.status.is_working and stop.remaining_quantity != open_qty:
            await self._resize(stop, open_qty, trade, "SL")
        if target is None or (not target.status.is_working and target.filled_quantity == 0):
            await self._place_leg(trade, "TGT", open_qty)
        elif target.status.is_working and target.remaining_quantity != open_qty:
            await self._resize(target, open_qty, trade, "TGT")

    async def _place_leg(self, trade: LiveTrade, leg: str, qty: int) -> None:
        attempt = sum(1 for n in trade.notes if n.startswith(f"placed {leg}")) + 1
        suffix = leg if attempt == 1 else f"{leg}{attempt}"
        long = trade.side == TradeSide.LONG
        if leg == "SL":
            trigger = to_tick(trade.stop_loss, trade.tick_size, up=not long)
            request = self._request(
                trade, suffix, trade.exit_side, qty, OrderType.SL_M, trigger_price=trigger
            )
        else:
            price = to_tick(trade.target, trade.tick_size, up=long)
            request = self._request(
                trade, suffix, trade.exit_side, qty, OrderType.LIMIT, price=price
            )
        order = await self.orders.submit(request, risk_approved=True, reduce_only=True)
        if leg == "SL":
            trade.stop_order_id = order.id
        else:
            trade.target_order_id = order.id
        trade.notes.append(f"placed {leg} {order.status.value}")
        self._changed = True
        await self.audit(
            "LIVE_ORDER_PLACED",
            purpose="STOP_LOSS" if leg == "SL" else "TARGET",
            correlation_id=trade.correlation_id,
            client_order_id=request.client_order_id,
            quantity=qty,
            price=request.price,
            trigger=request.trigger_price,
            status=order.status.value,
            broker_order_id=order.broker_order_id,
            rejection=order.rejection_reason,
        )

    async def _resize(self, order: Order, open_qty: int, trade: LiveTrade, leg: str) -> None:
        new_total = order.filled_quantity + open_qty
        updated = await self.orders.modify(order.id, OrderModification(quantity=new_total))
        self._changed = True
        await self.audit(
            "LIVE_ORDER_MODIFIED",
            correlation_id=trade.correlation_id,
            leg=leg,
            quantity=new_total,
            status=updated.status.value,
        )

    async def _cancel(self, order: Order, why: str) -> None:
        updated = await self.orders.cancel(order.id, why)
        self._changed = True
        await self.audit(
            "LIVE_ORDER_CANCELLED",
            client_order_id=order.client_order_id,
            broker_order_id=order.broker_order_id,
            reason=why,
            status=updated.status.value,
        )

    async def _exit_at_market(self, trade: LiveTrade, reason: LiveExitReason) -> None:
        """Cancel the exit legs, then close what is open at market."""

        trade.status = LiveTradeStatus.EXITING
        trade.exit_reason = trade.exit_reason or reason
        self._changed = True
        for oid in (trade.target_order_id, trade.stop_order_id):
            leg = self._order(oid)
            if leg is not None and leg.status.is_working:
                await self._cancel(leg, f"{reason.value.lower()} exit")
        # wait until both legs are no longer working before sending the exit
        if any(
            (o := self._order(i)) is not None and o.status.is_working
            for i in (trade.target_order_id, trade.stop_order_id)
        ):
            return
        await self._account_exits(trade)
        if trade.open_quantity == 0:
            await self._close(trade)
            return
        working = [
            o for o in (self._order(i) for i in trade.exit_order_ids) if o and o.status.is_working
        ]
        if working or any(
            (o := self._order(i)) is not None and o.needs_reconciliation
            for i in trade.exit_order_ids
        ):
            return  # an exit is already out (or its outcome is being reconciled)
        suffix = f"EXIT{len(trade.exit_order_ids) + 1}"
        request = self._request(
            trade, suffix, trade.exit_side, trade.open_quantity, OrderType.MARKET
        )
        order = await self.orders.submit(request, risk_approved=True, reduce_only=True)
        trade.exit_order_ids.append(order.id)
        await self.audit(
            "LIVE_ORDER_PLACED",
            purpose="EXIT",
            reason=reason.value,
            correlation_id=trade.correlation_id,
            client_order_id=request.client_order_id,
            quantity=request.quantity,
            status=order.status.value,
            broker_order_id=order.broker_order_id,
            rejection=order.rejection_reason,
        )

    async def _close(self, trade: LiveTrade) -> None:
        for oid in (trade.stop_order_id, trade.target_order_id):
            leg = self._order(oid)
            if leg is not None and leg.status.is_working:
                await self._cancel(leg, "position closed (one-cancels-other)")
        if trade.average_entry is not None and trade.average_exit is not None:
            sign = 1 if trade.side == TradeSide.LONG else -1
            trade.gross_pnl = round(
                (trade.average_exit - trade.average_entry) * trade.exited_quantity * sign, 2
            )
        trade.status = LiveTradeStatus.CLOSED
        await self.audit(
            "LIVE_TRADE_CLOSED",
            correlation_id=trade.correlation_id,
            symbol=trade.symbol,
            reason=trade.exit_reason.value if trade.exit_reason else None,
            quantity=trade.exited_quantity,
            average_entry=trade.average_entry,
            average_exit=trade.average_exit,
            gross_pnl=trade.gross_pnl,
        )
        self._finish(trade)

    def _finish(self, trade: LiveTrade) -> None:
        trade.closed_at = self._clock()
        self._pending_exit = None
        self.closed.append(trade)
        self.closed = self.closed[-100:]
        self.active = None
        self._changed = True

    # --- operator actions ----------------------------------------------------------

    def request_exit(self, reason: LiveExitReason) -> bool:
        trade = self.active
        if trade is None or trade.status.is_final:
            return False
        self._pending_exit = reason
        return True

    async def cancel_pending_entry(self, why: str) -> bool:
        """Kill switch: cancel an unfilled (or partly filled) entry. Exit legs
        protecting an open position are left in place."""

        trade = self.active
        if trade is None:
            return False
        entry = self._order(trade.entry_order_id)
        if entry is not None and entry.status.is_working:
            await self._cancel(entry, why)
            return True
        return False


__all__ = [
    "LiveExitReason",
    "LiveOrderManager",
    "LiveTrade",
    "LiveTradeStatus",
    "correlation_for",
    "to_tick",
]
