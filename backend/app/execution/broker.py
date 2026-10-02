"""BrokerAdapter: the one interface the trading engine places orders
through.

Only PaperBrokerAdapter implements it in Phase 8. A real broker
(Zerodha) adapter is a later phase and must not be connected before the
live-trading safety conditions in PROJECT_CONTRACT.md section 9 exist.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.execution.models import Balance, Order, OrderModification, OrderRequest, Position


class BrokerError(Exception):
    """An order operation the broker refused (unknown order, not modifiable...)."""


class BrokerAdapter(ABC):
    name: str = "broker"
    is_paper: bool = True

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Order:
        """Submit an order. Validation failures (including a repeated
        client_order_id) return an order with status REJECTED rather than
        raising, matching how brokers report them."""

    @abstractmethod
    async def modify_order(self, order_id: uuid.UUID, changes: OrderModification) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: uuid.UUID) -> Order: ...

    @abstractmethod
    async def get_order_status(self, order_id: uuid.UUID) -> Order: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_balance(self) -> Balance: ...
