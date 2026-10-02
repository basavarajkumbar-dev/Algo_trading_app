"""PaperTradingEngine.

The complete paper lifecycle, one trade at a time:

    SIGNAL -> RISK -> PAPER ORDER -> FILL -> POSITION -> SL/TP -> EXIT
           -> TRADE -> P&L

* `execute` takes a TradeIntent (a signal's symbol, side, entry, stop,
  target), runs the RiskManager, and on approval places a MARKET entry
  for the approved quantity. The client order id is derived from the
  signal id, so the broker also refuses a repeated signal.
* Once the entry fills (fully or partly) the engine places a protective
  SL_M stop and a LIMIT target for the filled quantity, and keeps both
  sized to the open quantity as further entry fills or partial exits
  arrive. The two exit legs are one-cancels-other: whichever fills, the
  other is reduced or cancelled.
* Exits: stop loss, target, square-off time, manual exit, and an
  emergency stop that explicitly asks for it. Any exit other than SL/TP
  cancels the working orders and closes the remaining quantity at market.
* Emergency stop: disables new trading and cancels pending (entry) orders.
  It does not close an open position unless explicitly requested or
  configured; the position keeps its protective stop and target.
* When the position is flat the trade is closed with its average entry
  and exit, gross P&L, charges, net P&L and R multiple, and the result
  is fed back into the RiskManager's daily counters.

The engine is driven from outside: `on_price` feeds prices into the
paper broker, `on_clock` handles timeouts and square-off. It never talks
to a real broker; it only accepts a PaperBrokerAdapter.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, Field

from app.execution.models import (
    Order,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from app.execution.paper_broker import PaperBrokerAdapter
from app.market_data.base import utcnow
from app.risk.manager import AccountView, RiskManager
from app.risk.position_sizing import round_to_tick
from app.risk.schemas import MarketConditions, RiskDecision, TradeIntent, TradeSide
from app.trading.schemas import (
    EngineEvent,
    ExitReason,
    OrderPurpose,
    PaperTrade,
    TradeStatus,
)
from app.trading.state import TradingState, TradingStateMachine


class EngineConfig(BaseModel):
    trading_mode: str = "PAPER"
    # Cancel an entry (or its unfilled remainder) this long after placing it.
    entry_timeout_seconds: float = Field(default=60.0, gt=0)
    # Whether an emergency stop also closes the active position when the
    # request does not say. Off: positions are never auto-closed unless
    # configured or explicitly requested (PROJECT_CONTRACT.md section 9).
    emergency_close_positions: bool = False
    history_size: int = Field(default=200, ge=10)


class EngineError(Exception):
    """An operator action that is not possible in the current state."""


class PaperTradingEngine:
    def __init__(
        self,
        broker: PaperBrokerAdapter,
        risk: RiskManager,
        *,
        config: EngineConfig | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if not broker.is_paper:
            raise ValueError("PaperTradingEngine only accepts a paper broker")
        self.broker = broker
        self.risk = risk
        self.config = config or EngineConfig()
        self._clock = clock
        self.machine = TradingStateMachine(clock())
        self.auto_trading = False
        self.emergency_stop_active = False
        self.emergency_reason: str | None = None
        self.active: PaperTrade | None = None
        self.closed: list[PaperTrade] = []  # most recent last
        self.last_decision: RiskDecision | None = None
        self._order_purpose: dict[uuid.UUID, OrderPurpose] = {}
        self._order_trade: dict[uuid.UUID, uuid.UUID] = {}
        self._entry_placed_at: datetime | None = None
        self._pending_exit_reason: ExitReason | None = None
        self._exit_attempts = 0
        # outbox drained by the service for persistence
        self._order_updates: dict[uuid.UUID, Order] = {}
        self._trade_updates: dict[uuid.UUID, PaperTrade] = {}
        self._events: list[EngineEvent] = []

    # --- state ------------------------------------------------------------

    @property
    def state(self) -> TradingState:
        return self.machine.state

    @property
    def rest_state(self) -> TradingState:
        return TradingState.SCANNING if self.auto_trading else TradingState.IDLE

    def _to(self, target: TradingState, reason: str) -> None:
        self.machine.to(target, self._clock(), reason)

    def _rest(self, reason: str) -> None:
        if self.state != self.rest_state:
            self._to(self.rest_state, reason)

    def _event(self, event_type: str, message: str, severity: str = "INFO", **metadata) -> None:
        self._events.append(
            EngineEvent(
                event_type=event_type,
                severity=severity,
                message=message,
                metadata=metadata,
                at=self._clock(),
            )
        )

    def drain(self) -> tuple[list[Order], list[PaperTrade], list[EngineEvent]]:
        orders = list(self._order_updates.values())
        trades = list(self._trade_updates.values())
        events = list(self._events)
        self._order_updates.clear()
        self._trade_updates.clear()
        self._events.clear()
        return orders, trades, events

    def requeue(
        self, orders: list[Order], trades: list[PaperTrade], events: list[EngineEvent]
    ) -> None:
        """Put drained changes back after a failed save (newer updates win)."""

        for o in orders:
            self._order_updates.setdefault(o.id, o)
        for t in trades:
            self._trade_updates.setdefault(t.id, t)
        self._events[:0] = events

    def _track(self, order: Order) -> Order:
        self._order_updates[order.id] = order
        return order

    def _touch_trade(self) -> None:
        if self.active is not None:
            self._trade_updates[self.active.id] = self.active.model_copy(deep=True)

    def purpose_of(self, order_id: uuid.UUID) -> OrderPurpose | None:
        return self._order_purpose.get(order_id)

    def trade_of(self, order_id: uuid.UUID) -> uuid.UUID | None:
        return self._order_trade.get(order_id)

    # --- account ----------------------------------------------------------

    def account_view(self, *, for_price: float | None = None) -> AccountView:
        balance = self.broker.balance()
        estimated = 0.0
        if for_price:
            qty = math.floor(self.risk.limits.max_position_value / for_price)
            estimated = self.broker.config.costs.round_trip_estimate(
                entry_side=Side.BUY, quantity=qty, price=for_price
            )
        return AccountView(
            capital=balance.equity,
            available_capital=balance.available,
            open_exposure=self.broker.open_exposure(),
            emergency_stop=self.emergency_stop_active,
            active_trade=(
                f"{self.active.side.value} {self.active.symbol} ({self.state.value})"
                if self.active is not None
                else None
            ),
            open_order_symbols={o.symbol for o in self.broker.working_orders()},
            estimated_charges=estimated,
        )

    # --- operator controls --------------------------------------------------

    def set_auto_trading(self, enabled: bool) -> None:
        if enabled and self.emergency_stop_active:
            raise EngineError("Reset the emergency stop before enabling automatic trading.")
        self.auto_trading = enabled
        if self.state in (TradingState.IDLE, TradingState.SCANNING):
            self._rest("automatic trading " + ("on" if enabled else "off"))
        self._event(
            "AUTO_TRADING", f"Automatic paper trading {'enabled' if enabled else 'disabled'}"
        )

    async def emergency_stop(
        self, reason: str = "operator request", *, close_position: bool | None = None
    ) -> None:
        """Disable new trading and cancel pending orders where safe.

        Safe to cancel: an entry order (or its unfilled remainder) and any
        order not protecting the active position. Not cancelled: the
        protective stop and target of an open position, which keep
        working. The position itself is closed only when `close_position`
        is true (an explicit request) or, when it is not given, when
        `EngineConfig.emergency_close_positions` is configured on.
        """

        close = self.config.emergency_close_positions if close_position is None else close_position
        self.emergency_stop_active = True
        self.emergency_reason = reason
        self.auto_trading = False
        self._event(
            "EMERGENCY_STOP",
            f"Emergency stop activated: {reason}. New trading disabled"
            + ("; closing the active position at market" if close and self.active else "")
            + ".",
            severity="CRITICAL",
            close_position=close,
        )
        if self.active is None:
            self._cancel_stray_orders()
            if self.state in (TradingState.IDLE, TradingState.SCANNING):
                self._rest("emergency stop")
            return
        if self.state == TradingState.ORDER_PENDING:
            await self._cancel_unfilled_entry(
                ExitReason.EMERGENCY, "entry cancelled by emergency stop"
            )
        elif self.state == TradingState.POSITION_ACTIVE:
            entry = self._entry()
            if entry.status.is_working:
                self._track(self.broker.cancel(entry.id))
                self._event(
                    "ENTRY_CANCELLED",
                    "Unfilled entry remainder cancelled by emergency stop",
                    trade_id=str(self.active.id),
                )
            if close:
                await self._begin_exit(ExitReason.EMERGENCY)
            else:
                self._event(
                    "POSITION_KEPT",
                    f"{self.active.symbol} position kept with its stop loss and target "
                    "working; use Exit position to close it",
                    severity="WARNING",
                    trade_id=str(self.active.id),
                )
                await self._sync()
        self._refresh_trade_numbers()
        self._touch_trade()

    def reset_emergency_stop(self) -> None:
        if self.state == TradingState.EXIT:
            raise EngineError("An exit is in progress; reset once it has filled.")
        self.emergency_stop_active = False
        self.emergency_reason = None
        self._event("EMERGENCY_STOP_RESET", "Emergency stop reset; automatic trading stays off")

    async def exit_position(self, reason: ExitReason = ExitReason.MANUAL) -> None:
        if self.active is None:
            raise EngineError("No active trade to exit.")
        if self.state == TradingState.ORDER_PENDING:
            await self._cancel_unfilled_entry(reason, "entry cancelled by operator")
        elif self.state == TradingState.POSITION_ACTIVE:
            await self._begin_exit(reason)
        else:
            raise EngineError(f"Already exiting ({self.state.value}).")
        self._refresh_trade_numbers()
        self._touch_trade()

    def _cancel_stray_orders(self, *, keep_exit: bool = False) -> None:
        exit_id = self._current_exit_id()
        for order in self.broker.working_orders():
            if keep_exit and order.id == exit_id:
                continue
            self._track(self.broker.cancel(order.id))

    def trail_stop(self, new_stop: float) -> bool:
        """Tighten the protective stop of the open position (never loosen it).

        Returns True when the working SL_M order was moved. The trade's
        initial `stop_loss` is kept for R-multiple accounting; the moved
        level is recorded in `trailing_stop`. A level already through the
        last price is refused (the stop would fill at once)."""

        trade = self.active
        if trade is None or self.state != TradingState.POSITION_ACTIVE:
            return False
        stop = self._latest(OrderPurpose.STOP_LOSS)
        if stop is None or not stop.status.is_working or stop.triggered:
            return False
        level = round_to_tick(
            new_stop,
            self.broker.config.tick_size,
            "down" if trade.side == TradeSide.LONG else "up",
        )
        current = trade.trailing_stop or trade.stop_loss
        last = self.broker.last_price(trade.symbol)
        long = trade.side == TradeSide.LONG
        improves = level > current if long else level < current
        valid = last is None or (level < last if long else level > last)
        if not (improves and valid):
            return False
        self._track(self.broker.modify(stop.id, OrderModification(trigger_price=level)))
        trade.trailing_stop = level
        self._event(
            "STOP_TRAILED",
            f"{trade.symbol} stop moved {current} -> {level}",
            trade_id=str(trade.id),
        )
        self._touch_trade()
        return True

    # --- SIGNAL -> RISK -> ORDER -------------------------------------------

    async def execute(self, intent: TradeIntent, conditions: MarketConditions) -> RiskDecision:
        now = self._clock()
        conditions = conditions.model_copy(update={"trading_mode": self.config.trading_mode})
        resting = self.state.is_resting and self.active is None
        if resting:
            self._to(TradingState.SIGNAL_FOUND, f"{intent.side.value} {intent.symbol}")
            self._to(TradingState.RISK_CHECK, "evaluating risk")
        account = self.account_view(for_price=conditions.last_price or intent.entry)
        decision = self.risk.evaluate(intent, conditions, account)
        self.last_decision = decision
        if not decision.approved:
            self._event(
                "RISK_REJECTED",
                f"{intent.side.value} {intent.symbol}: {decision.summary}",
                severity="WARNING",
                signal_id=str(intent.signal_id) if intent.signal_id else None,
                reasons=[r.value for r in decision.rejection_reasons],
            )
            if resting:
                self._rest("risk check rejected")
            return decision

        assert decision.size is not None
        size = decision.size
        trade = PaperTrade(
            signal_id=intent.signal_id,
            symbol=intent.symbol,
            side=intent.side,
            strategy=intent.strategy,
            score=intent.score,
            planned_entry=intent.entry,
            stop_loss=size.stop_loss,
            target=round_to_tick(intent.target, self.risk.limits.tick_size),
            quantity=size.quantity,
            initial_risk=size.actual_risk,
            created_at=now,
            risk_summary=decision.summary,
            risk_checks=[c.model_dump(mode="json") for c in decision.checks],
            last_price=conditions.last_price,
        )
        self.active = trade
        self._pending_exit_reason = None
        self._exit_attempts = 0
        self.risk.record_signal_attempt(intent.signal_id)
        key = f"sig-{intent.signal_id}" if intent.signal_id else f"trade-{trade.id}"
        entry_side = Side.BUY if intent.side == TradeSide.LONG else Side.SELL
        order = self._place(
            OrderPurpose.ENTRY,
            OrderRequest(
                symbol=intent.symbol,
                side=entry_side,
                quantity=size.quantity,
                order_type=OrderType.MARKET,
                client_order_id=f"{key}-entry",
                tag=OrderPurpose.ENTRY.value,
            ),
        )
        self._entry_placed_at = now
        self._to(TradingState.ORDER_PENDING, f"entry order {order.status.value}")
        self._event(
            "ORDER_PLACED",
            f"Paper {entry_side.value} {size.quantity} {intent.symbol} at market "
            f"(stop {size.stop_loss}, target {intent.target})",
            trade_id=str(trade.id),
            order_id=str(order.id),
        )
        if order.status == OrderStatus.REJECTED:
            trade.status = TradeStatus.REJECTED
            self._event(
                "ORDER_REJECTED",
                f"Entry rejected by the paper broker: {order.rejection_reason}",
                severity="WARNING",
                trade_id=str(trade.id),
            )
            self._finish(ExitReason.ENTRY_REJECTED, TradeStatus.REJECTED)
            return decision
        self._touch_trade()
        await self._sync()
        return decision

    def _place(self, purpose: OrderPurpose, request: OrderRequest) -> Order:
        order = self.broker.submit(request)
        self._order_purpose[order.id] = purpose
        assert self.active is not None
        self._order_trade[order.id] = self.active.id
        self.active.order_ids.setdefault(purpose.value, []).append(order.id)
        return self._track(order)

    # --- price and clock ---------------------------------------------------

    async def on_price(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        fills = self.broker.on_price(symbol, price, timestamp)
        for fill in fills:
            self._track(self.broker.order(fill.order_id))
        if self.active is not None and self.active.symbol == symbol:
            self.active.last_price = price
            await self._sync()

    async def on_clock(self) -> None:
        if self.active is not None:
            await self._sync()

    # --- lifecycle --------------------------------------------------------

    def _orders(self, purpose: OrderPurpose) -> list[Order]:
        if self.active is None:
            return []
        return [self.broker.order(i) for i in self.active.order_ids.get(purpose.value, [])]

    def _entry(self) -> Order:
        orders = self._orders(OrderPurpose.ENTRY)
        assert orders, "active trade without an entry order"
        return orders[0]

    def _latest(self, purpose: OrderPurpose) -> Order | None:
        orders = self._orders(purpose)
        return orders[-1] if orders else None

    def _current_exit_id(self) -> uuid.UUID | None:
        latest = self._latest(OrderPurpose.EXIT)
        return latest.id if latest is not None and latest.status.is_working else None

    def _exit_filled(self) -> int:
        return sum(
            o.filled_quantity
            for p in (OrderPurpose.STOP_LOSS, OrderPurpose.TARGET, OrderPurpose.EXIT)
            for o in self._orders(p)
        )

    async def _sync(self) -> None:
        trade = self.active
        if trade is None:
            return
        now = self._clock()
        entry = self._entry()
        timed_out = (
            self._entry_placed_at is not None
            and (now - self._entry_placed_at).total_seconds() >= self.config.entry_timeout_seconds
        )

        if self.state == TradingState.ORDER_PENDING:
            if entry.filled_quantity == 0:
                if entry.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
                    self._finish(ExitReason.ENTRY_EXPIRED, TradeStatus.CANCELLED)
                elif timed_out:
                    await self._cancel_unfilled_entry(
                        ExitReason.ENTRY_EXPIRED,
                        f"entry not filled within {self.config.entry_timeout_seconds:.0f}s",
                    )
                return
            trade.opened_at = entry.fills[0].timestamp
            self.risk.record_entry(trade.signal_id)
            self._to(
                TradingState.POSITION_ACTIVE,
                f"entry filled {entry.filled_quantity}/{entry.quantity} @ {entry.average_price}",
            )
            self._event(
                "POSITION_OPENED",
                f"{trade.side.value} {entry.filled_quantity} {trade.symbol} @ {entry.average_price}",
                trade_id=str(trade.id),
            )

        if self.state == TradingState.POSITION_ACTIVE:
            await self._manage_position(entry, timed_out)

        if self.state == TradingState.EXIT:
            await self._manage_exit()

        self._refresh_trade_numbers()
        self._touch_trade()

    async def _manage_position(self, entry: Order, timed_out: bool) -> None:
        trade = self.active
        assert trade is not None
        stop = self._latest(OrderPurpose.STOP_LOSS)
        target = self._latest(OrderPurpose.TARGET)
        exit_leg_filled = any(o is not None and o.filled_quantity > 0 for o in (stop, target))

        # The unfilled entry remainder is cancelled once it times out or an
        # exit leg has started filling (never add to a trade that is exiting).
        if entry.status.is_working and (timed_out or exit_leg_filled):
            self._track(self.broker.cancel(entry.id))
            entry = self.broker.order(entry.id)

        open_qty = entry.filled_quantity - self._exit_filled()
        if open_qty <= 0 and not entry.status.is_working:
            reason = self._exit_reason_from_legs(stop, target)
            for leg in (stop, target):
                if leg is not None and leg.status.is_working:
                    self._track(self.broker.cancel(leg.id))
            self._to(TradingState.EXIT, f"{reason.value} filled")
            self._finish(reason, TradeStatus.CLOSED)
            return

        if self.risk.square_off_due(self._clock()):
            await self._begin_exit(ExitReason.TIME_EXIT)
            return

        # An exit leg completed while more entry quantity filled in the same
        # update: close what is left with the same reason.
        for leg, reason in ((stop, ExitReason.STOP_LOSS), (target, ExitReason.TARGET)):
            if leg is not None and leg.status == OrderStatus.FILLED:
                await self._begin_exit(reason)
                return

        exit_side = Side.SELL if trade.side == TradeSide.LONG else Side.BUY
        key = f"trade-{trade.id}"
        # protective stop
        if stop is None or not stop.status.is_working:
            if stop is None or stop.status == OrderStatus.CANCELLED:
                placed = self._place(
                    OrderPurpose.STOP_LOSS,
                    OrderRequest(
                        symbol=trade.symbol,
                        side=exit_side,
                        quantity=open_qty,
                        order_type=OrderType.SL_M,
                        trigger_price=trade.trailing_stop or trade.stop_loss,
                        client_order_id=f"{key}-sl-{len(self._orders(OrderPurpose.STOP_LOSS))}",
                        tag=OrderPurpose.STOP_LOSS.value,
                    ),
                )
                if placed.status == OrderStatus.REJECTED:
                    # Price is already through the stop: get out at market.
                    self._event(
                        "STOP_REJECTED",
                        f"Protective stop rejected ({placed.rejection_reason}); exiting at market",
                        severity="WARNING",
                        trade_id=str(trade.id),
                    )
                    await self._begin_exit(ExitReason.STOP_LOSS)
                    return
                stop = placed
        else:
            self._resize(stop, open_qty)
        # target
        if target is None:
            target = self._place(
                OrderPurpose.TARGET,
                OrderRequest(
                    symbol=trade.symbol,
                    side=exit_side,
                    quantity=open_qty,
                    order_type=OrderType.LIMIT,
                    price=trade.target,
                    client_order_id=f"{key}-tp",
                    tag=OrderPurpose.TARGET.value,
                ),
            )
        elif target.status.is_working:
            self._resize(target, open_qty)

        # A leg may have filled the rest on placement (e.g. marketable target).
        if self.active is not None and entry.filled_quantity - self._exit_filled() <= 0:
            await self._manage_position(self.broker.order(entry.id), timed_out)

    def _resize(self, order: Order, open_qty: int) -> None:
        """Keep a working exit leg's unfilled quantity equal to the open quantity."""

        wanted = order.filled_quantity + open_qty
        if order.quantity != wanted and open_qty > 0:
            self._track(self.broker.modify(order.id, OrderModification(quantity=wanted)))
        elif open_qty <= 0:
            self._track(self.broker.cancel(order.id))

    def _exit_reason_from_legs(self, stop: Order | None, target: Order | None) -> ExitReason:
        stop_qty = stop.filled_quantity if stop else 0
        target_qty = target.filled_quantity if target else 0
        if self._pending_exit_reason is not None:
            return self._pending_exit_reason
        if target_qty > stop_qty:
            return ExitReason.TARGET
        return (
            ExitReason.TRAILING_STOP
            if self.active and self.active.trailing_stop
            else ExitReason.STOP_LOSS
        )

    async def _begin_exit(self, reason: ExitReason) -> None:
        trade = self.active
        assert trade is not None
        self._pending_exit_reason = reason
        entry = self._entry()
        if entry.status.is_working:
            self._track(self.broker.cancel(entry.id))
        for purpose in (OrderPurpose.STOP_LOSS, OrderPurpose.TARGET):
            for order in self._orders(purpose):
                if order.status.is_working:
                    self._track(self.broker.cancel(order.id))
        if self.state == TradingState.POSITION_ACTIVE:
            self._to(TradingState.EXIT, f"{reason.value}: closing at market")
        self._event(
            "EXIT_STARTED",
            f"{reason.value}: closing {trade.symbol} at market",
            severity="CRITICAL" if reason == ExitReason.EMERGENCY else "INFO",
            trade_id=str(trade.id),
        )
        await self._manage_exit()

    async def _manage_exit(self) -> None:
        trade = self.active
        assert trade is not None
        entry = self._entry()
        open_qty = entry.filled_quantity - self._exit_filled()
        if open_qty <= 0:
            for order in self.broker.working_orders():
                if order.symbol == trade.symbol:
                    self._track(self.broker.cancel(order.id))
            self._finish(self._pending_exit_reason or ExitReason.MANUAL, TradeStatus.CLOSED)
            return
        latest = self._latest(OrderPurpose.EXIT)
        if latest is not None and latest.status.is_working:
            if latest.remaining_quantity != open_qty:
                self._resize(latest, open_qty)
            return
        self._exit_attempts += 1
        exit_side = Side.SELL if trade.side == TradeSide.LONG else Side.BUY
        order = self._place(
            OrderPurpose.EXIT,
            OrderRequest(
                symbol=trade.symbol,
                side=exit_side,
                quantity=open_qty,
                order_type=OrderType.MARKET,
                client_order_id=f"trade-{trade.id}-exit-{self._exit_attempts}",
                tag=OrderPurpose.EXIT.value,
            ),
        )
        if order.status == OrderStatus.REJECTED:
            self._event(
                "EXIT_REJECTED",
                f"Exit order rejected: {order.rejection_reason}; retrying on the next price",
                severity="CRITICAL",
                trade_id=str(trade.id),
            )
            return
        if entry.filled_quantity - self._exit_filled() <= 0:
            self._finish(self._pending_exit_reason or ExitReason.MANUAL, TradeStatus.CLOSED)

    async def _cancel_unfilled_entry(self, reason: ExitReason, detail: str) -> None:
        entry = self._entry()
        if entry.status.is_working:
            self._track(self.broker.cancel(entry.id))
        entry = self.broker.order(entry.id)
        if entry.filled_quantity > 0:
            # A fill raced the cancel: manage it as a position.
            await self._sync()
            if self.state == TradingState.POSITION_ACTIVE:
                await self._begin_exit(reason)
            return
        self._event(
            "ENTRY_CANCELLED", detail, trade_id=str(self.active.id) if self.active else None
        )
        self._finish(reason, TradeStatus.CANCELLED)

    # --- closing ----------------------------------------------------------

    def _refresh_trade_numbers(self) -> None:
        trade = self.active
        if trade is None:
            return
        entry = self._entry()
        exits = [
            f
            for p in (OrderPurpose.STOP_LOSS, OrderPurpose.TARGET, OrderPurpose.EXIT)
            for o in self._orders(p)
            for f in o.fills
        ]
        direction = 1 if trade.side == TradeSide.LONG else -1
        trade.filled_quantity = entry.filled_quantity
        exit_qty = sum(f.quantity for f in exits)
        trade.open_quantity = entry.filled_quantity - exit_qty
        trade.entry_price = entry.average_price
        trade.exit_price = (
            round(sum(f.quantity * f.price for f in exits) / exit_qty, 4) if exit_qty else None
        )
        charges = sum(o.charges.total for p in OrderPurpose for o in self._orders(p))
        gross = 0.0
        if trade.entry_price is not None and exit_qty:
            gross = sum(f.quantity * (f.price - trade.entry_price) * direction for f in exits)
        trade.gross_pnl = round(gross, 2)
        trade.charges = round(charges, 2)
        trade.net_pnl = round(gross - charges, 2)
        last = self.broker.last_price(trade.symbol)
        trade.unrealized_pnl = (
            round((last - trade.entry_price) * trade.open_quantity * direction, 2)
            if last is not None and trade.entry_price is not None and trade.open_quantity
            else 0.0
        )
        if self.state == TradingState.ORDER_PENDING:
            trade.status = TradeStatus.PENDING
        elif self.state == TradingState.POSITION_ACTIVE:
            trade.status = TradeStatus.OPEN
        elif self.state == TradingState.EXIT:
            trade.status = TradeStatus.EXITING

    def _finish(self, reason: ExitReason, status: TradeStatus) -> None:
        trade = self.active
        assert trade is not None
        now = self._clock()
        self._refresh_trade_numbers()
        trade.status = status
        trade.exit_reason = reason
        trade.closed_at = now
        trade.unrealized_pnl = 0.0
        if trade.filled_quantity > 0 and trade.entry_price is not None:
            # R is measured against the risk the position was sized for (planned
            # entry to stop). Using the fill would turn a gap toward the stop
            # into an outsized R loss on a small rupee loss.
            risk_per_unit = abs(trade.planned_entry - trade.stop_loss)
            initial = risk_per_unit * trade.filled_quantity
            trade.r_multiple = round(trade.net_pnl / initial, 2) if initial > 0 else None
            self.risk.record_trade_result(trade.net_pnl)
        self._to(TradingState.CLOSED, f"{status.value}: {reason.value}")
        self._event(
            "TRADE_CLOSED" if status == TradeStatus.CLOSED else f"TRADE_{status.value}",
            (
                f"{trade.side.value} {trade.filled_quantity} {trade.symbol}: {reason.value}, "
                f"net P&L ₹{trade.net_pnl:,.2f}"
                + (f" ({trade.r_multiple:+.2f}R)" if trade.r_multiple is not None else "")
            ),
            severity="INFO" if trade.net_pnl >= 0 else "WARNING",
            trade_id=str(trade.id),
            net_pnl=trade.net_pnl,
            exit_reason=reason.value,
        )
        self._touch_trade()
        self.closed.append(trade.model_copy(deep=True))
        del self.closed[: max(len(self.closed) - self.config.history_size, 0)]
        self.active = None
        self._entry_placed_at = None
        self._pending_exit_reason = None
        if self.risk.account_blocks():
            self._event(
                "ENTRIES_BLOCKED",
                "New entries blocked: " + "; ".join(text for _, text in self.risk.account_blocks()),
                severity="WARNING",
            )
        self._to(self.rest_state, "ready for the next signal")
