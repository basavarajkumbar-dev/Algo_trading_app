"""MockBrokerAdapter: an in-memory broker the tests script.

It behaves like a remote broker rather than like the paper simulator:
orders get a broker order id, start SUBMITTED, are accepted (OPEN or
TRIGGER_PENDING), and only fill when the test says so (`fill`), which
makes partial fills, exchange rejections and cancellations explicit.

Fault injection:

    fail_next(n)          the next n calls raise BrokerUnavailableError
                          before reaching the "broker"
    lose_next_response()  the next place_order reaches the broker (the
                          order exists) but the response is lost
    reject_next(reason)   the next place_order is rejected by the broker
    go_down() / come_back()  every call fails until come_back()
    expire_session()      calls raise BrokerAuthError until connect()

`calls` counts the requests that reached the broker per operation, so a
test can prove an order was never sent twice. No network, no money.
"""

from __future__ import annotations

import itertools
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from app.execution.broker import (
    BrokerAdapter,
    BrokerAuthError,
    BrokerError,
    BrokerUnavailableError,
)
from app.execution.models import (
    Balance,
    BrokerHealth,
    BrokerProfile,
    ConnectionState,
    Fill,
    Instrument,
    Order,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
from app.market_data.base import utcnow


class MockBrokerAdapter(BrokerAdapter):
    name = "mock"
    is_paper = True  # no real orders: nothing leaves this process

    def __init__(
        self,
        *,
        starting_cash: float = 1_000_000.0,
        accept_immediately: bool = True,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.starting_cash = starting_cash
        self.accept_immediately = accept_immediately
        self._clock = clock
        self._state = ConnectionState.DISCONNECTED
        self._orders: dict[uuid.UUID, Order] = {}
        self._by_tag: dict[str, uuid.UUID] = {}
        self._seq = itertools.count(1)
        self._fail_next = 0
        self._lose_next = False
        self._reject_next: str | None = None
        self._down = False
        self._session_expired = False
        self._last_success: datetime | None = None
        self._last_error: str | None = None
        self._failures = 0
        self.calls: Counter[str] = Counter()
        self.positions_book: dict[str, tuple[int, float]] = {}  # symbol -> (qty, avg)

    # --- fault injection ------------------------------------------------------

    def fail_next(self, n: int = 1) -> None:
        self._fail_next += n

    def lose_next_response(self) -> None:
        self._lose_next = True

    def reject_next(self, reason: str) -> None:
        self._reject_next = reason

    def go_down(self) -> None:
        self._down = True

    def come_back(self) -> None:
        self._down = False

    def expire_session(self) -> None:
        self._session_expired = True

    def _gate(self, op: str) -> None:
        """Simulate the transport: raise before the request reaches the broker."""

        if self._down or self._fail_next > 0:
            if self._fail_next > 0:
                self._fail_next -= 1
            self._failures += 1
            self._last_error = f"{op}: network unreachable"
            if self._state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED):
                self._state = (
                    ConnectionState.DISCONNECTED
                    if self._failures >= 3
                    else ConnectionState.DEGRADED
                )
            raise BrokerUnavailableError(f"{op}: network unreachable (mock)")
        if self._session_expired:
            self._state = ConnectionState.AUTH_REQUIRED
            raise BrokerAuthError("session expired (mock)")
        if self._state not in (ConnectionState.CONNECTED, ConnectionState.DEGRADED):
            raise BrokerUnavailableError(f"{op}: not connected")
        self.calls[op] += 1

    def _ok(self) -> None:
        self._failures = 0
        self._last_success = self._clock()
        if self._state is ConnectionState.DEGRADED:
            self._state = ConnectionState.CONNECTED

    # --- connection -----------------------------------------------------------

    @property
    def connection_state(self) -> ConnectionState:
        return self._state

    async def connect(self) -> ConnectionState:
        if self._down:
            self._state = ConnectionState.DISCONNECTED
            self._last_error = "connect: network unreachable"
            return self._state
        self._session_expired = False
        self._state = ConnectionState.CONNECTED
        self._ok()
        return self._state

    async def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    async def health_check(self) -> BrokerHealth:
        try:
            self._gate("health")
            self._ok()
        except BrokerError as exc:
            self._last_error = str(exc)
        healthy = self._state is ConnectionState.CONNECTED
        return BrokerHealth(
            broker=self.name,
            state=self._state,
            healthy=healthy,
            detail="Mock broker " + ("reachable" if healthy else f"not usable: {self._last_error}"),
            checked_at=self._clock(),
            latency_ms=0.0,
            last_success_at=self._last_success,
            last_error=self._last_error,
            consecutive_failures=self._failures,
        )

    # --- account --------------------------------------------------------------

    async def get_profile(self) -> BrokerProfile:
        self._gate("profile")
        self._ok()
        return BrokerProfile(
            broker=self.name,
            user_id="MOCK01",
            user_name="Mock account",
            exchanges=["NSE"],
            products=["MIS", "CNC"],
            order_types=[t.value for t in OrderType],
            is_paper=True,
        )

    async def get_balance(self) -> Balance:
        self._gate("balance")
        self._ok()
        used = sum(abs(q) * p for q, p in self.positions_book.values())
        return Balance(
            starting_capital=self.starting_cash,
            cash=self.starting_cash,
            used_margin=round(used, 2),
            available=round(self.starting_cash - used, 2),
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            charges_paid=0.0,
            equity=self.starting_cash,
        )

    async def get_positions(self) -> list[Position]:
        self._gate("positions")
        self._ok()
        return [
            Position(symbol=s, quantity=q, average_price=round(p, 4))
            for s, (q, p) in self.positions_book.items()
        ]

    # --- orders ---------------------------------------------------------------

    async def get_orders(self) -> list[Order]:
        self._gate("orders")
        self._ok()
        return [o.model_copy(deep=True) for o in self._orders.values()]

    async def get_order_status(self, order_id: uuid.UUID) -> Order:
        self._gate("order_status")
        self._ok()
        return self._get(order_id).model_copy(deep=True)

    async def place_order(self, request: OrderRequest) -> Order:
        now = self._clock()
        tag = request.client_order_id
        order = Order(
            client_order_id=request.client_order_id,
            tag=request.tag,
            strategy_signal_id=request.strategy_signal_id,
            correlation_id=request.correlation_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            trigger_price=request.trigger_price,
            status=OrderStatus.SUBMITTED,
            placed_at=now,
            updated_at=now,
            executable_after=now,
            exchange=request.exchange,
            product=request.product,
            broker_tag=tag,
        )
        if tag is not None and tag in self._by_tag:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"duplicate client_order_id {tag!r}"
            return order
        try:
            self._gate("place")
        except BrokerAuthError as exc:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(exc)
            return order
        except BrokerUnavailableError as exc:
            # never reached the broker; the caller cannot know that, so the
            # outcome is reported as unknown
            order.needs_reconciliation = True
            order.status_message = str(exc)
            return order
        if self._reject_next is not None:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = self._reject_next
            order.broker_order_id = f"MOCK-{next(self._seq)}"
            order.broker_status = "REJECTED"
            self._reject_next = None
        else:
            order.broker_order_id = f"MOCK-{next(self._seq)}"
            if self.accept_immediately:
                self._accept(order)
        self._orders[order.id] = order
        if tag is not None:
            self._by_tag[tag] = order.id
        self._ok()
        if self._lose_next:
            self._lose_next = False
            lost = order.model_copy(deep=True)
            lost.status = OrderStatus.SUBMITTED
            lost.broker_order_id = None
            lost.broker_status = None
            lost.needs_reconciliation = True
            lost.status_message = "response lost (mock)"
            return lost
        return order.model_copy(deep=True)

    def _accept(self, order: Order) -> None:
        order.status = (
            OrderStatus.TRIGGER_PENDING if order.order_type is OrderType.SL_M else OrderStatus.OPEN
        )
        order.broker_status = order.status.value
        order.updated_at = self._clock()

    async def modify_order(self, order_id: uuid.UUID, changes: OrderModification) -> Order:
        self._gate("modify")
        order = self._get(order_id)
        if not order.status.is_working:
            raise BrokerError(f"order is {order.status.value} and cannot be modified")
        if changes.quantity is not None:
            if changes.quantity < order.filled_quantity:
                raise BrokerError("quantity below the filled quantity")
            order.quantity = changes.quantity
        if changes.price is not None:
            order.price = changes.price
        if changes.trigger_price is not None:
            order.trigger_price = changes.trigger_price
        order.modifications += 1
        order.updated_at = self._clock()
        self._ok()
        return order.model_copy(deep=True)

    async def cancel_order(self, order_id: uuid.UUID) -> Order:
        self._gate("cancel")
        order = self._get(order_id)
        if not order.status.is_working:
            raise BrokerError(f"order is {order.status.value} and cannot be cancelled")
        order.cancelled_quantity = order.quantity - order.filled_quantity
        order.status = OrderStatus.CANCELLED
        order.broker_status = "CANCELLED"
        order.updated_at = self._clock()
        self._ok()
        return order.model_copy(deep=True)

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        self._gate("instruments")
        self._ok()
        return [
            Instrument(symbol=s, exchange="NSE", instrument_token=i, instrument_type="EQ")
            for i, s in enumerate(("RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"), start=1)
            if exchange in (None, "NSE")
        ]

    # --- the "exchange" side (driven by tests) -----------------------------------

    def _get(self, order_id: uuid.UUID) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise BrokerError(f"unknown order {order_id}")
        return order

    def broker_order(self, broker_order_id: str) -> Order:
        for o in self._orders.values():
            if o.broker_order_id == broker_order_id:
                return o
        raise BrokerError(f"unknown broker order {broker_order_id}")

    def accept(self, order_id: uuid.UUID) -> None:
        self._accept(self._get(order_id))

    def fill(self, order_id: uuid.UUID, quantity: int, price: float) -> Fill:
        order = self._get(order_id)
        if not order.status.is_working:
            raise BrokerError(f"cannot fill a {order.status.value} order")
        quantity = min(quantity, order.remaining_quantity)
        now = self._clock()
        previous = order.filled_quantity
        order.filled_quantity += quantity
        order.average_price = round(
            ((order.average_price or 0.0) * previous + price * quantity) / order.filled_quantity, 4
        )
        order.status = (
            OrderStatus.FILLED
            if order.filled_quantity >= order.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        order.broker_status = "COMPLETE" if order.status is OrderStatus.FILLED else "OPEN"
        order.updated_at = now
        qty, avg = self.positions_book.get(order.symbol, (0, 0.0))
        signed = quantity * order.side.sign
        new_qty = qty + signed
        if new_qty == 0:
            self.positions_book.pop(order.symbol, None)
        else:
            same = qty == 0 or (qty > 0) == (signed > 0)
            new_avg = (abs(qty) * avg + quantity * price) / abs(new_qty) if same else avg
            self.positions_book[order.symbol] = (new_qty, new_avg)
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            market_price=price,
            slippage=0.0,
            charges=order.charges,
            timestamp=now,
        )
        order.fills.append(fill)
        return fill

    def exchange_reject(self, order_id: uuid.UUID, reason: str) -> None:
        order = self._get(order_id)
        order.status = OrderStatus.REJECTED
        order.rejection_reason = reason
        order.broker_status = "REJECTED"
        order.updated_at = self._clock()

    def exchange_cancel(self, order_id: uuid.UUID, reason: str = "cancelled by exchange") -> None:
        order = self._get(order_id)
        order.cancelled_quantity = order.quantity - order.filled_quantity
        order.status = OrderStatus.CANCELLED
        order.status_message = reason
        order.broker_status = "CANCELLED"
        order.updated_at = self._clock()

    def side_of(self, order_id: uuid.UUID) -> Side:
        return self._get(order_id).side
