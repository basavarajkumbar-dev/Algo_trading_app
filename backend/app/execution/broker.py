"""BrokerAdapter: the one interface orders reach a broker through.

    STRATEGY -> SIGNAL -> RISK -> ORDER MANAGER -> BrokerAdapter -> broker

Implementations:

    PaperBrokerAdapter    simulated fills (app.execution.paper_broker); what
                          the paper trading engine uses
    MockBrokerAdapter     scriptable in-memory broker with fault injection,
                          for tests (app.brokers.mock)
    ZerodhaBrokerAdapter  Kite Connect v3 (app.brokers.zerodha); read-only
                          unless live orders are explicitly armed

Strategies, signals and the risk engine never import an adapter: they hand
an approved order to the order manager (enforced by a test).

Error contract:
    place_order        a refusal (validation, margin, duplicate
                       client_order_id, broker rejection) returns an order
                       with status REJECTED, as brokers report it. An
                       unknown outcome (the request may or may not have
                       reached the broker) returns status SUBMITTED with
                       needs_reconciliation=True; it is never re-sent.
    other calls        raise BrokerError (refused), BrokerAuthError (log in
                       again), BrokerUnavailableError (network / broker
                       down) or BrokerRateLimitError.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.execution.models import (
    Balance,
    BrokerHealth,
    BrokerProfile,
    ConnectionState,
    Instrument,
    Margins,
    Order,
    OrderModification,
    OrderRequest,
    Position,
    Trade,
)


class BrokerError(Exception):
    """An order operation the broker refused (unknown order, not modifiable...)."""


class BrokerUnavailableError(BrokerError):
    """Network failure, timeout or broker outage: the outcome may be unknown."""


class BrokerAuthError(BrokerError):
    """Session missing, expired or rejected: a new login is needed."""


class BrokerRateLimitError(BrokerError):
    """Too many requests; the request was not processed."""


class BrokerNotConfiguredError(BrokerError):
    """No credentials: this broker cannot be used (the app stays in PAPER)."""


class LiveOrdersDisabledError(BrokerError):
    """A live broker refused an order operation because live orders are not armed."""


class BrokerAdapter(ABC):
    name: str = "broker"
    is_paper: bool = True

    # --- connection ---------------------------------------------------------

    @property
    @abstractmethod
    def connection_state(self) -> ConnectionState: ...

    @abstractmethod
    async def connect(self) -> ConnectionState:
        """Open (or verify) the session. Never raises for an unreachable or
        unconfigured broker: the returned state says what happened."""

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def health_check(self) -> BrokerHealth: ...

    # --- account ------------------------------------------------------------

    @abstractmethod
    async def get_profile(self) -> BrokerProfile: ...

    @abstractmethod
    async def get_balance(self) -> Balance: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    # --- orders -------------------------------------------------------------

    @abstractmethod
    async def get_orders(self) -> list[Order]:
        """Every order of the trading day the broker knows about."""

    @abstractmethod
    async def get_order_status(self, order_id: uuid.UUID) -> Order: ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Order:
        """See the error contract in the module docstring."""

    @abstractmethod
    async def modify_order(self, order_id: uuid.UUID, changes: OrderModification) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: uuid.UUID) -> Order: ...

    # --- reference data -----------------------------------------------------

    @abstractmethod
    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]: ...

    # --- executions and funds (Phase 13) ---------------------------------------
    # Defaults derive them from orders and the balance, which is exact for
    # the in-process brokers (paper, mock); a remote broker overrides them
    # with its own trade book and margin endpoints.

    async def get_trades(self) -> list[Trade]:
        """Every execution of the trading day."""

        trades: list[Trade] = []
        for order in await self.get_orders():
            trades.extend(_trades_of(order, self.is_paper))
        return trades

    async def get_order_trades(self, order_id: uuid.UUID) -> list[Trade]:
        return _trades_of(await self.get_order_status(order_id), self.is_paper)

    async def get_margins(self) -> Margins:
        b = await self.get_balance()
        return Margins(
            available_cash=b.available,
            opening_balance=b.starting_capital,
            used=b.used_margin,
            net=b.equity,
            is_paper=self.is_paper,
        )


def _trades_of(order: Order, is_paper: bool) -> list[Trade]:
    return [
        Trade(
            trade_id=f"{order.id.hex[:12]}-{i}",
            order_id=order.id,
            broker_order_id=order.broker_order_id,
            symbol=order.symbol,
            exchange=order.exchange,
            side=f.side,
            quantity=f.quantity,
            price=f.price,
            timestamp=f.timestamp,
            is_paper=is_paper,
        )
        for i, f in enumerate(order.fills, start=1)
    ]
