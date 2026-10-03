"""PaperBrokerAdapter: simulated order execution against market prices.

No money, no broker connection, no real orders. Orders rest in memory
and fill when `on_price` reports a price that reaches them:

    MARKET  fills at the next price at or after the execution delay,
            with adverse slippage
    LIMIT   fills when price reaches the limit, at the limit price
            (no price improvement assumed)
    SL_M    triggers when price trades at or through the trigger, then
            fills like a market order at that price with slippage, so a
            gap through the stop fills at the worse gap price

Configurable assumptions (PaperBrokerConfig): slippage in basis points,
execution delay in seconds, the largest fraction of an order that one
price update can fill (below 1.0 this produces partial fills), lot size,
tick size, margin multiplier, and the transaction cost model.

Rules a real broker also enforces: quantity must be a positive multiple
of the lot size, a repeated client_order_id is rejected, an opening
order needs enough available margin, and a stop order whose trigger has
already been crossed is rejected instead of filling instantly.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.execution.broker import BrokerAdapter, BrokerError
from app.execution.costs import CostModel
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
from app.market_data.universe import NIFTY_50_UNIVERSE
from app.risk.position_sizing import floor_to_lot, round_to_tick

if TYPE_CHECKING:
    from app.core.config import Settings


class PaperBrokerConfig(BaseModel):
    slippage_bps: float = Field(default=5.0, ge=0, le=500)  # 5 bps = 0.05%
    execution_delay_seconds: float = Field(default=0.0, ge=0, le=60)
    max_fill_fraction: float = Field(default=1.0, gt=0, le=1)  # < 1 => partial fills
    lot_size: int = Field(default=1, ge=1)
    tick_size: float = Field(default=0.05, gt=0)
    margin_multiplier: float = Field(default=1.0, ge=1, le=5)
    costs: CostModel = Field(default_factory=CostModel)


class _PositionBook:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.quantity = 0
        self.average_price = 0.0
        self.realized = 0.0
        self.charges = 0.0

    def apply(self, side: Side, quantity: int, price: float) -> float:
        """Apply a fill and return the gross P&L it realized."""

        signed = side.sign * quantity
        realized = 0.0
        if self.quantity == 0 or (self.quantity > 0) == (signed > 0):
            total = abs(self.quantity) + quantity
            self.average_price = (
                abs(self.quantity) * self.average_price + quantity * price
            ) / total
            self.quantity += signed
        else:
            closing = min(quantity, abs(self.quantity))
            direction = 1 if self.quantity > 0 else -1
            realized = closing * (price - self.average_price) * direction
            self.quantity += side.sign * closing
            opening = quantity - closing
            if self.quantity == 0:
                self.average_price = 0.0
            if opening > 0:
                self.quantity = side.sign * opening
                self.average_price = price
        self.realized += realized
        return realized


class PaperBrokerAdapter(BrokerAdapter):
    name = "paper"
    is_paper = True

    def __init__(
        self,
        *,
        starting_capital: float,
        config: PaperBrokerConfig | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.config = config or PaperBrokerConfig()
        self.starting_capital = float(starting_capital)
        self._clock = clock
        self._orders: dict[uuid.UUID, Order] = {}
        self._client_ids: set[str] = set()
        self._books: dict[str, _PositionBook] = {}
        self._last_prices: dict[str, float] = {}
        self._realized = 0.0
        self._charges = 0.0

    # --- BrokerAdapter ----------------------------------------------------
    # The simulator lives in this process, so it is always "connected".

    @property
    def connection_state(self) -> ConnectionState:
        return ConnectionState.CONNECTED

    async def connect(self) -> ConnectionState:
        return ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        return None

    async def health_check(self) -> BrokerHealth:
        now = self._clock()
        return BrokerHealth(
            broker=self.name,
            state=ConnectionState.CONNECTED,
            healthy=True,
            detail="Simulated broker in this process; no real orders",
            checked_at=now,
            latency_ms=0.0,
            last_success_at=now,
        )

    async def get_profile(self) -> BrokerProfile:
        return BrokerProfile(
            broker=self.name,
            user_id="PAPER",
            user_name="Paper account",
            exchanges=["NSE"],
            products=["MIS"],
            order_types=[t.value for t in OrderType],
            is_paper=True,
        )

    async def get_orders(self) -> list[Order]:
        return self.orders()

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        if exchange not in (None, "NSE"):
            return []
        return [
            Instrument(
                symbol=i.symbol,
                exchange="NSE",
                name=i.name,
                segment="NSE",
                instrument_type="EQ",
                tick_size=self.config.tick_size,
                lot_size=self.config.lot_size,
            )
            for i in NIFTY_50_UNIVERSE
        ]

    async def place_order(self, request: OrderRequest) -> Order:
        return self.submit(request)

    async def modify_order(self, order_id: uuid.UUID, changes: OrderModification) -> Order:
        return self.modify(order_id, changes)

    async def cancel_order(self, order_id: uuid.UUID) -> Order:
        return self.cancel(order_id)

    async def get_order_status(self, order_id: uuid.UUID) -> Order:
        return self.order(order_id)

    async def get_positions(self) -> list[Position]:
        return self.positions()

    async def get_balance(self) -> Balance:
        return self.balance()

    # --- order entry --------------------------------------------------------

    def submit(self, request: OrderRequest) -> Order:
        now = self._clock()
        cfg = self.config
        order = Order(
            client_order_id=request.client_order_id,
            tag=request.tag,
            strategy_signal_id=request.strategy_signal_id,
            correlation_id=request.correlation_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=round_to_tick(request.price, cfg.tick_size) if request.price else None,
            trigger_price=(
                round_to_tick(request.trigger_price, cfg.tick_size)
                if request.trigger_price
                else None
            ),
            status=(
                OrderStatus.TRIGGER_PENDING
                if request.order_type is OrderType.SL_M
                else OrderStatus.OPEN
            ),
            placed_at=now,
            updated_at=now,
            executable_after=now + timedelta(seconds=cfg.execution_delay_seconds),
            exchange=request.exchange,
            product=request.product,
        )
        reason = self._validate(order)
        if request.client_order_id is not None:
            self._client_ids.add(request.client_order_id)
        if reason is not None:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = reason
        self._orders[order.id] = order
        if order.status.is_working and cfg.execution_delay_seconds == 0:
            last = self._last_prices.get(order.symbol)
            if last is not None and order.order_type is not OrderType.SL_M:
                self._try_fill(order, last, now)
        return order.model_copy(deep=True)

    def _validate(self, order: Order) -> str | None:
        cfg = self.config
        if order.client_order_id is not None and order.client_order_id in self._client_ids:
            return f"duplicate client_order_id {order.client_order_id!r}"
        if order.quantity <= 0:
            return "quantity must be positive"
        if order.quantity % cfg.lot_size:
            return f"quantity must be a multiple of the lot size {cfg.lot_size}"
        if order.order_type is OrderType.LIMIT and not order.price:
            return "LIMIT order needs a price"
        if order.order_type is OrderType.SL_M and not order.trigger_price:
            return "SL_M order needs a trigger price"
        last = self._last_prices.get(order.symbol)
        if order.order_type is OrderType.SL_M and last is not None:
            assert order.trigger_price is not None
            if order.side is Side.SELL and order.trigger_price >= last:
                return f"sell stop trigger {order.trigger_price} is not below last price {last}"
            if order.side is Side.BUY and order.trigger_price <= last:
                return f"buy stop trigger {order.trigger_price} is not above last price {last}"
        reference = order.price or order.trigger_price or last
        if reference is None:
            return f"no market price for {order.symbol} yet"
        opening = self._opening_quantity(order.symbol, order.side, order.quantity)
        if opening > 0:
            needed = opening * reference / cfg.margin_multiplier
            available = self.balance().available
            if needed > available + 1e-6:
                return f"insufficient margin: needs ₹{needed:,.2f}, available ₹{available:,.2f}"
        return None

    def _opening_quantity(self, symbol: str, side: Side, quantity: int) -> int:
        """How much of an order would open or add to a position."""

        book = self._books.get(symbol)
        current = book.quantity if book else 0
        if current == 0 or (current > 0) == (side is Side.BUY):
            return quantity
        return max(quantity - abs(current), 0)

    def modify(self, order_id: uuid.UUID, changes: OrderModification) -> Order:
        order = self._get(order_id)
        if not order.status.is_working:
            raise BrokerError(f"order {order_id} is {order.status.value} and cannot be modified")
        cfg = self.config
        if changes.quantity is not None:
            if changes.quantity < order.filled_quantity or changes.quantity <= 0:
                raise BrokerError(
                    f"quantity {changes.quantity} is below the filled quantity "
                    f"{order.filled_quantity}"
                )
            if changes.quantity % cfg.lot_size:
                raise BrokerError(f"quantity must be a multiple of the lot size {cfg.lot_size}")
            order.quantity = changes.quantity
        if changes.price is not None:
            if order.order_type is not OrderType.LIMIT:
                raise BrokerError("only LIMIT orders have a price")
            order.price = round_to_tick(changes.price, cfg.tick_size)
        if changes.trigger_price is not None:
            if order.order_type is not OrderType.SL_M or order.triggered:
                raise BrokerError("only an untriggered SL_M order has a trigger price")
            order.trigger_price = round_to_tick(changes.trigger_price, cfg.tick_size)
        order.updated_at = self._clock()
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        return order.model_copy(deep=True)

    def cancel(self, order_id: uuid.UUID) -> Order:
        order = self._get(order_id)
        if not order.status.is_working:
            raise BrokerError(f"order {order_id} is {order.status.value} and cannot be cancelled")
        order.status = OrderStatus.CANCELLED
        order.updated_at = self._clock()
        return order.model_copy(deep=True)

    def order(self, order_id: uuid.UUID) -> Order:
        return self._get(order_id).model_copy(deep=True)

    def orders(self) -> list[Order]:
        return [o.model_copy(deep=True) for o in self._orders.values()]

    def _get(self, order_id: uuid.UUID) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise BrokerError(f"unknown order {order_id}")
        return order

    # --- market simulation --------------------------------------------------

    def last_price(self, symbol: str) -> float | None:
        return self._last_prices.get(symbol)

    def on_price(self, symbol: str, price: float, timestamp: datetime | None = None) -> list[Fill]:
        """Feed a market price; returns the fills it caused, in order."""

        now = timestamp or self._clock()
        self._last_prices[symbol] = price
        fills: list[Fill] = []
        for order in list(self._orders.values()):
            if order.symbol != symbol or not order.status.is_working:
                continue
            if now < order.executable_after:
                continue
            fill = self._try_fill(order, price, now)
            if fill is not None:
                fills.append(fill)
        return fills

    def _try_fill(self, order: Order, price: float, now: datetime) -> Fill | None:
        cfg = self.config
        if order.order_type is OrderType.SL_M and not order.triggered:
            assert order.trigger_price is not None
            hit = (
                price >= order.trigger_price
                if order.side is Side.BUY
                else price <= order.trigger_price
            )
            if not hit:
                return None
            order.triggered = True
            order.status = (
                OrderStatus.PARTIALLY_FILLED if order.filled_quantity else OrderStatus.OPEN
            )

        if order.order_type is OrderType.LIMIT:
            assert order.price is not None
            reachable = price <= order.price if order.side is Side.BUY else price >= order.price
            if not reachable:
                return None
            fill_price = order.price
        else:
            slip = price * cfg.slippage_bps / 10_000
            if order.side is Side.BUY:
                fill_price = round_to_tick(price + slip, cfg.tick_size, "up")
            else:
                fill_price = round_to_tick(price - slip, cfg.tick_size, "down")

        remaining = order.remaining_quantity
        if cfg.max_fill_fraction >= 1:
            quantity = remaining
        else:
            chunk = floor_to_lot(math.floor(order.quantity * cfg.max_fill_fraction), cfg.lot_size)
            quantity = min(remaining, max(chunk, cfg.lot_size))
        if quantity <= 0:
            return None

        turnover = quantity * fill_price
        charges = cfg.costs.fill_charges(
            side=order.side,
            turnover=turnover,
            brokerage_already_charged=order.charges.brokerage,
        )
        book = self._books.setdefault(order.symbol, _PositionBook(order.symbol))
        self._realized += book.apply(order.side, quantity, fill_price)
        book.charges += charges.total
        self._charges += charges.total

        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=fill_price,
            market_price=price,
            slippage=round(abs(fill_price - price), 4),
            charges=charges,
            timestamp=now,
        )
        previous = order.filled_quantity
        order.filled_quantity += quantity
        order.average_price = round(
            ((order.average_price or 0.0) * previous + fill_price * quantity)
            / order.filled_quantity,
            4,
        )
        order.charges = order.charges + charges
        order.fills.append(fill)
        order.status = (
            OrderStatus.FILLED
            if order.filled_quantity >= order.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        order.updated_at = now
        return fill

    # --- account ------------------------------------------------------------

    def positions(self) -> list[Position]:
        out: list[Position] = []
        for symbol, book in self._books.items():
            last = self._last_prices.get(symbol)
            unrealized = (
                (last - book.average_price) * book.quantity
                if last is not None and book.quantity
                else 0.0
            )
            out.append(
                Position(
                    symbol=symbol,
                    quantity=book.quantity,
                    average_price=round(book.average_price, 4),
                    last_price=last,
                    realized_pnl=round(book.realized, 2),
                    unrealized_pnl=round(unrealized, 2),
                    charges=round(book.charges, 2),
                )
            )
        return out

    def balance(self) -> Balance:
        cash = self.starting_capital + self._realized - self._charges
        used = 0.0
        unrealized = 0.0
        for symbol, book in self._books.items():
            if book.quantity == 0:
                continue
            used += abs(book.quantity) * book.average_price / self.config.margin_multiplier
            last = self._last_prices.get(symbol)
            if last is not None:
                unrealized += (last - book.average_price) * book.quantity
        return Balance(
            starting_capital=round(self.starting_capital, 2),
            cash=round(cash, 2),
            used_margin=round(used, 2),
            available=round(cash - used, 2),
            realized_pnl=round(self._realized, 2),
            unrealized_pnl=round(unrealized, 2),
            charges_paid=round(self._charges, 2),
            equity=round(cash + unrealized, 2),
        )

    def open_exposure(self) -> float:
        return round(sum(p.value for p in self.positions() if p.quantity), 2)

    def working_orders(self) -> list[Order]:
        return [o.model_copy(deep=True) for o in self._orders.values() if o.status.is_working]


def paper_broker_config_from_settings(settings: Settings) -> PaperBrokerConfig:
    return PaperBrokerConfig(
        slippage_bps=settings.paper_slippage_bps,
        execution_delay_seconds=settings.paper_execution_delay_seconds,
        max_fill_fraction=settings.paper_max_fill_fraction,
        lot_size=settings.risk_lot_size,
        tick_size=settings.risk_tick_size,
        margin_multiplier=settings.risk_margin_multiplier,
        costs=CostModel(**(settings.paper_costs or {})),
    )
