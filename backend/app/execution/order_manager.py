"""OrderManager: the only component that sends orders to a BrokerAdapter.

    STRATEGY -> SIGNAL -> RISK -> ORDER MANAGER -> BrokerAdapter -> broker

Responsibilities

  * Execution gate (fail closed): an order is only sent when the gate
    allows it. For a live broker that means every condition of
    PROJECT_CONTRACT.md section 9: TRADING_MODE=LIVE and
    LIVE_MODE_ENABLED=true, an explicit user arming action, risk approval
    of this order, a CONNECTED broker that has been reconciled, overall
    system health HEALTHY and an open trading session. Simulated brokers
    (paper, mock) need risk approval and a connected broker.
  * Duplicate protection: a client_order_id seen before returns the order
    already tracked (no second request); concurrent submissions of one id
    are serialised; an identical working order (same symbol, side,
    quantity, type and purpose) within `duplicate_window_seconds` is
    refused; the adapter rejects a repeated id as a last line.
  * Order reconciliation: `reconcile()` compares tracked orders with the
    broker's order book and records status changes, partial and complete
    fills (by filled-quantity deltas), rejections and cancellations;
    adopts orders whose placement outcome was unknown; marks them failed
    if the broker never shows them; reports orders placed outside this
    app.
  * Failed orders: a rejection or refusal is recorded as an event with
    its reason; nothing is retried automatically.
  * Network failure / disconnect: an unknown placement outcome is never
    re-sent, only reconciled. When the broker is not CONNECTED, new orders
    are blocked; after a reconnect, a successful reconciliation is
    required before orders are accepted again.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.execution.broker import BrokerAdapter, BrokerError
from app.execution.models import (
    BrokerHealth,
    ConnectionState,
    Order,
    OrderModification,
    OrderRequest,
    OrderStatus,
)
from app.market_data.base import utcnow

logger = get_logger(__name__)


class OrderEvent(BaseModel):
    kind: str  # ORDER_SENT, ORDER_ACCEPTED, ORDER_PARTIAL_FILL, ORDER_FILLED, ...
    severity: str = "INFO"  # INFO | WARNING | CRITICAL
    message: str
    at: datetime
    order_id: uuid.UUID | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    symbol: str | None = None
    status: OrderStatus | None = None
    filled_delta: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GateDecision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


@dataclass
class ExecutionGate:
    """Every condition that must hold before an order is sent.

    The callables are evaluated per order, so a condition that stops
    holding (health drops, session closes) blocks the next order."""

    live_mode_configured: Callable[[], bool] = (
        lambda: False
    )  # TRADING_MODE=LIVE + LIVE_MODE_ENABLED
    user_armed: Callable[[], bool] = lambda: False  # explicit user action; never a default
    system_healthy: Callable[[], bool] = lambda: True
    session_open: Callable[[], bool] = lambda: True
    emergency_stop: Callable[[], bool] = lambda: False

    def evaluate(
        self, broker: BrokerAdapter, *, risk_approved: bool, resync_pending: bool
    ) -> GateDecision:
        reasons: list[str] = []
        if not risk_approved:
            reasons.append("not approved by the risk engine")
        if self.emergency_stop():
            reasons.append("emergency stop is active")
        state = broker.connection_state
        if state is not ConnectionState.CONNECTED:
            reasons.append(f"broker is {state.value}")
        elif resync_pending:
            reasons.append("broker reconnected; waiting for order reconciliation")
        if not broker.is_paper:
            if not self.live_mode_configured():
                reasons.append(
                    "live trading is not enabled (TRADING_MODE=LIVE and LIVE_MODE_ENABLED)"
                )
            if not self.user_armed():
                reasons.append("live orders have not been armed by the user")
            if not self.system_healthy():
                reasons.append("system health is not HEALTHY")
            if not self.session_open():
                reasons.append("trading session is closed")
        return GateDecision(allowed=not reasons, reasons=reasons)


@dataclass
class _Tracked:
    order: Order
    request: OrderRequest
    unknown_checks: int = 0
    lost: bool = False
    missing_reported: bool = False


@dataclass
class ReconcileReport:
    ok: bool
    at: datetime
    checked: int = 0
    changed: int = 0
    recovered: int = 0
    lost: int = 0
    external: int = 0
    error: str | None = None
    events: list[OrderEvent] = field(default_factory=list)


_FINAL = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


class OrderManager:
    def __init__(
        self,
        broker: BrokerAdapter,
        gate: ExecutionGate | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
        unknown_max_checks: int = 3,
        unknown_grace_seconds: float = 30.0,
        duplicate_window_seconds: float = 5.0,
        on_events: Callable[[list[OrderEvent]], Awaitable[None]] | None = None,
        history: int = 500,
    ) -> None:
        self.broker = broker
        self.gate = gate or ExecutionGate()
        self._clock = clock
        self._unknown_max_checks = unknown_max_checks
        self._unknown_grace = unknown_grace_seconds
        self._duplicate_window = duplicate_window_seconds
        self._on_events = on_events
        self._tracked: dict[uuid.UUID, _Tracked] = {}
        self._by_client_id: dict[str, uuid.UUID] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._external_seen: set[str] = set()
        self._events: deque[OrderEvent] = deque(maxlen=history)
        self._pending: list[OrderEvent] = []
        self.resync_pending = False
        self._was_connected = broker.connection_state is ConnectionState.CONNECTED
        self.last_reconcile: ReconcileReport | None = None

    # --- views ----------------------------------------------------------------

    def orders(self) -> list[Order]:
        return [t.order.model_copy(deep=True) for t in self._tracked.values()]

    def order(self, order_id: uuid.UUID) -> Order:
        return self._tracked[order_id].order.model_copy(deep=True)

    def working(self) -> list[Order]:
        return [o for o in self.orders() if o.status.is_working]

    def unresolved(self) -> list[Order]:
        return [o for o in self.orders() if o.needs_reconciliation]

    def events(self, limit: int = 100) -> list[OrderEvent]:
        return list(self._events)[-limit:][::-1]

    @property
    def external_orders(self) -> int:
        return len(self._external_seen)

    # --- events ----------------------------------------------------------------

    def _emit(self, kind: str, message: str, order: Order | None = None, **kw: Any) -> OrderEvent:
        event = OrderEvent(
            kind=kind,
            message=message,
            at=self._clock(),
            order_id=order.id if order else None,
            client_order_id=order.client_order_id if order else None,
            broker_order_id=order.broker_order_id if order else None,
            symbol=order.symbol if order else None,
            status=order.status if order else None,
            **kw,
        )
        self._events.append(event)
        self._pending.append(event)
        log = logger.warning if event.severity != "INFO" else logger.info
        log("[%s] %s", kind, message)
        return event

    async def _flush(self) -> None:
        events, self._pending = self._pending, []
        if events and self._on_events is not None:
            try:
                await self._on_events(events)
            except Exception:  # noqa: BLE001 - event delivery must not break order handling
                logger.exception("Order event delivery failed")

    # --- submission --------------------------------------------------------------

    def _possible_duplicate(self, request: OrderRequest) -> Order | None:
        now = self._clock()
        for t in self._tracked.values():
            o = t.order
            if (
                o.status.is_working
                and o.symbol == request.symbol
                and o.side is request.side
                and o.quantity == request.quantity
                and o.order_type is request.order_type
                and o.tag == request.tag
                and (now - o.placed_at).total_seconds() <= self._duplicate_window
            ):
                return o
        return None

    async def submit(self, request: OrderRequest, *, risk_approved: bool) -> Order:
        """Send an order the risk engine approved. Returns the tracked order
        (REJECTED with a reason when blocked or refused)."""

        if not request.client_order_id:
            request = request.model_copy(update={"client_order_id": uuid.uuid4().hex})
        cid = request.client_order_id
        assert cid is not None
        lock = self._locks.setdefault(cid, asyncio.Lock())
        async with lock:
            existing = self._by_client_id.get(cid)
            if existing is not None:  # idempotent: the same intent is not sent twice
                return self.order(existing)
            order = await self._submit_locked(request, risk_approved=risk_approved)
        await self._flush()
        return order

    async def _submit_locked(self, request: OrderRequest, *, risk_approved: bool) -> Order:
        now = self._clock()
        decision = self.gate.evaluate(
            self.broker, risk_approved=risk_approved, resync_pending=self.resync_pending
        )
        duplicate = self._possible_duplicate(request) if decision.allowed else None
        if not decision.allowed or duplicate is not None:
            reason = (
                "; ".join(decision.reasons)
                if not decision.allowed
                else f"possible duplicate of working order {duplicate.client_order_id}"  # type: ignore[union-attr]
            )
            order = Order(
                client_order_id=request.client_order_id,
                tag=request.tag,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                quantity=request.quantity,
                price=request.price,
                trigger_price=request.trigger_price,
                status=OrderStatus.REJECTED,
                rejection_reason=f"blocked: {reason}",
                placed_at=now,
                updated_at=now,
                executable_after=now,
                exchange=request.exchange,
                product=request.product,
            )
            self._track(order, request)
            self._emit(
                "ORDER_BLOCKED", f"{_label(order)} not sent: {reason}", order, severity="WARNING"
            )
            return order.model_copy(deep=True)

        arm = getattr(self.broker, "arm_live_orders", None)
        if not self.broker.is_paper and arm is not None:
            arm()  # every gate condition holds for this order
        try:
            order = await self.broker.place_order(request)
        except BrokerError as exc:  # adapters report refusals as REJECTED; be defensive
            order = Order(
                client_order_id=request.client_order_id,
                tag=request.tag,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                quantity=request.quantity,
                status=OrderStatus.REJECTED,
                rejection_reason=str(exc),
                placed_at=now,
                updated_at=now,
                executable_after=now,
            )
        finally:
            disarm = getattr(self.broker, "disarm_live_orders", None)
            if disarm is not None:
                disarm()  # armed only for the duration of one gated request
        self._track(order, request)
        if order.status is OrderStatus.REJECTED:
            self._emit(
                "ORDER_REJECTED",
                f"{_label(order)} rejected: {order.rejection_reason}",
                order,
                severity="WARNING",
            )
        elif order.needs_reconciliation:
            self.resync_pending = self.broker.connection_state is not ConnectionState.CONNECTED
            self._emit(
                "ORDER_UNKNOWN",
                f"{_label(order)}: no answer from the broker; reconciling (not re-sent)",
                order,
                severity="WARNING",
            )
            await self.reconcile()
        else:
            self._emit("ORDER_SENT", f"{_label(order)} sent ({order.status.value})", order)
            self._record_fills(None, order)
        return self.order(order.id)

    def _track(self, order: Order, request: OrderRequest) -> None:
        self._tracked[order.id] = _Tracked(order=order.model_copy(deep=True), request=request)
        if order.client_order_id:
            self._by_client_id[order.client_order_id] = order.id

    # --- modify / cancel ---------------------------------------------------------------

    async def modify(self, order_id: uuid.UUID, changes: OrderModification) -> Order:
        tracked = self._tracked[order_id]
        if self.broker.connection_state is not ConnectionState.CONNECTED:
            raise BrokerError(f"broker is {self.broker.connection_state.value}")
        arm = getattr(self.broker, "arm_live_orders", None)
        if not self.broker.is_paper and arm is not None:
            decision = self.gate.evaluate(
                self.broker, risk_approved=True, resync_pending=self.resync_pending
            )
            if not decision.allowed:
                raise BrokerError("; ".join(decision.reasons))
            arm()
        try:
            order = await self.broker.modify_order(order_id, changes)
        finally:
            disarm = getattr(self.broker, "disarm_live_orders", None)
            if disarm is not None:
                disarm()
        self._update(tracked, order)
        self._emit("ORDER_MODIFIED", f"{_label(order)} modified", order)
        await self._flush()
        return self.order(order_id)

    async def cancel(self, order_id: uuid.UUID, reason: str = "requested") -> Order:
        """Cancellation is allowed whenever the broker is reachable: it only
        reduces exposure, so it is not subject to the live-entry conditions
        other than the explicit live configuration."""

        tracked = self._tracked[order_id]
        arm = getattr(self.broker, "arm_live_orders", None)
        if not self.broker.is_paper and arm is not None:
            if not self.gate.live_mode_configured():
                raise BrokerError("live trading is not enabled")
            arm()
        try:
            order = await self.broker.cancel_order(order_id)
        finally:
            disarm = getattr(self.broker, "disarm_live_orders", None)
            if disarm is not None:
                disarm()
        self._update(tracked, order)
        await self._flush()
        return self.order(order_id)

    async def cancel_all_working(self, reason: str) -> list[Order]:
        """Emergency stop: cancel what can be cancelled; positions are not closed."""

        out: list[Order] = []
        for order in self.working():
            if order.broker_order_id is None and order.needs_reconciliation:
                continue  # cannot cancel what the broker has not confirmed
            try:
                out.append(await self.cancel(order.id, reason))
            except BrokerError as exc:
                self._emit(
                    "CANCEL_FAILED",
                    f"{_label(order)} could not be cancelled: {exc}",
                    order,
                    severity="CRITICAL",
                )
        await self._flush()
        return out

    # --- reconciliation ------------------------------------------------------------------

    def _record_fills(self, before: Order | None, after: Order) -> None:
        previous = before.filled_quantity if before else 0
        delta = after.filled_quantity - previous
        if delta <= 0:
            return
        if after.filled_quantity >= after.quantity:
            kind, text = "ORDER_FILLED", "filled"
        else:
            kind, text = (
                "ORDER_PARTIAL_FILL",
                f"partly filled ({after.filled_quantity}/{after.quantity})",
            )
        self._emit(
            kind,
            f"{_label(after)} {text}: +{delta} @ {after.average_price}",
            after,
            filled_delta=delta,
            metadata={"average_price": after.average_price, "filled": after.filled_quantity},
        )

    def _update(self, tracked: _Tracked, new: Order) -> bool:
        old = tracked.order
        new = new.model_copy(deep=True)
        new.client_order_id = new.client_order_id or old.client_order_id
        new.tag = new.tag or old.tag
        tracked.order = new
        changed = (
            old.status is not new.status
            or old.filled_quantity != new.filled_quantity
            or old.broker_status != new.broker_status
        )
        self._record_fills(old, new)
        if old.status is not new.status:
            if new.status is OrderStatus.REJECTED:
                self._emit(
                    "ORDER_REJECTED",
                    f"{_label(new)} rejected by the broker: {new.rejection_reason or new.status_message}",
                    new,
                    severity="WARNING",
                )
            elif new.status is OrderStatus.CANCELLED:
                extra = f" after filling {new.filled_quantity}" if new.filled_quantity else ""
                self._emit("ORDER_CANCELLED", f"{_label(new)} cancelled{extra}", new)
            elif (
                new.status in (OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING)
                and old.status is OrderStatus.SUBMITTED
            ):
                self._emit("ORDER_ACCEPTED", f"{_label(new)} accepted ({new.status.value})", new)
        return changed

    async def reconcile(self) -> ReconcileReport:
        now = self._clock()
        report = ReconcileReport(ok=False, at=now)
        try:
            book = await self.broker.get_orders()
        except BrokerError as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            self.last_reconcile = report
            if self.broker.connection_state is not ConnectionState.CONNECTED:
                self.resync_pending = True
            await self._flush()
            return report
        by_id = {o.id: o for o in book}
        for tracked in self._tracked.values():
            current = tracked.order
            if current.status in _FINAL and not tracked.lost and current.id not in by_id:
                continue
            report.checked += 1
            found = by_id.get(current.id)
            if found is not None:
                if tracked.lost:
                    tracked.lost = False
                    self._emit(
                        "ORDER_REAPPEARED",
                        f"{_label(found)} was marked failed but exists at the broker "
                        f"({found.broker_status}); now tracked again",
                        found,
                        severity="CRITICAL",
                    )
                elif current.needs_reconciliation:
                    report.recovered += 1
                    self._emit(
                        "ORDER_RECOVERED",
                        f"{_label(found)} found at the broker ({found.broker_status}) after an unknown outcome",
                        found,
                        severity="WARNING",
                    )
                found.needs_reconciliation = False
                if self._update(tracked, found):
                    report.changed += 1
                continue
            if current.needs_reconciliation:
                tracked.unknown_checks += 1
                age = (now - current.placed_at).total_seconds()
                if (
                    tracked.unknown_checks >= self._unknown_max_checks
                    and age >= self._unknown_grace
                ):
                    failed = current.model_copy(deep=True)
                    failed.status = OrderStatus.REJECTED
                    failed.needs_reconciliation = False
                    failed.rejection_reason = (
                        "not found at the broker after a network failure; treated as not placed"
                    )
                    tracked.order = failed
                    tracked.lost = True
                    report.lost += 1
                    self._emit(
                        "ORDER_LOST",
                        f"{_label(failed)} never appeared at the broker; marked failed "
                        "(it will be re-adopted if it shows up)",
                        failed,
                        severity="CRITICAL",
                    )
            elif (
                current.broker_order_id
                and not tracked.missing_reported
                and current.status.is_working
            ):
                tracked.missing_reported = True
                self._emit(
                    "ORDER_MISSING",
                    f"{_label(current)} is not in the broker's order book",
                    current,
                    severity="WARNING",
                )
        tracked_ids = set(self._tracked)
        for o in book:
            if (
                o.id in tracked_ids
                or not o.broker_order_id
                or o.broker_order_id in self._external_seen
            ):
                continue
            self._external_seen.add(o.broker_order_id)
            report.external += 1
            self._emit(
                "EXTERNAL_ORDER",
                f"Order {o.broker_order_id} ({o.side.value} {o.quantity} {o.symbol}, "
                f"{o.broker_status}) was placed outside this app; it is reported, not managed",
                o,
                severity="WARNING",
            )
        report.ok = True
        self.resync_pending = False
        self.last_reconcile = report
        report.events = list(self._pending)
        await self._flush()
        return report

    # --- connection supervision -----------------------------------------------------------

    async def check_connection(self) -> BrokerHealth:
        """Health check; on loss blocks orders, on recovery reconnects and
        reconciles before orders are accepted again."""

        health = await self.broker.health_check()
        connected = health.state is ConnectionState.CONNECTED
        if not connected:
            if self._was_connected:
                self._emit(
                    "BROKER_DISCONNECTED",
                    f"{self.broker.name} is {health.state.value}: new orders blocked ({health.last_error or health.detail})",
                    severity="CRITICAL",
                )
            self.resync_pending = True
            self._was_connected = False
            if health.state not in (ConnectionState.NOT_CONFIGURED, ConnectionState.AUTH_REQUIRED):
                state = await self.broker.connect()
                connected = state is ConnectionState.CONNECTED
                if connected:
                    health = await self.broker.health_check()
        if connected and (not self._was_connected or self.resync_pending):
            report = await self.reconcile()
            if report.ok:
                if not self._was_connected:
                    self._emit(
                        "BROKER_CONNECTED",
                        f"{self.broker.name} connected; orders reconciled ({report.checked} checked)",
                    )
                self._was_connected = True
        await self._flush()
        return health


def _label(order: Order) -> str:
    return f"{order.side.value} {order.quantity} {order.symbol} [{order.client_order_id}]"
