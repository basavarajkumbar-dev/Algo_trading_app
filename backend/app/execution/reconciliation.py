"""OrderReconciliationService: local records vs the broker's books (Phase 13).

Compares, read-only,

    orders     local (OrderManager) vs the broker's order book
    trades     each order's filled quantity vs the executions reported
    positions  net quantity per symbol, local vs broker

and reports every difference:

    MISSING_ORDER        a local order with a broker id the broker does not have
    UNKNOWN_ORDER        a broker order this app did not place
    QUANTITY_MISMATCH    filled quantity differs
    STATUS_MISMATCH      status differs
    AVG_PRICE_MISMATCH   average fill price differs beyond a tick tolerance
    TRADE_MISMATCH       an order's executions do not add up to its fills
    POSITION_MISMATCH    net position differs

It changes nothing: OrderManager.reconcile() adopts broker state for
tracked orders, and nothing here ever places or re-sends an order.
Logged as ORDER_RECONCILIATION.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.execution.broker import BrokerAdapter, BrokerError
from app.execution.models import Order, OrderStatus, Position, Trade
from app.market_data.base import utcnow

logger = get_logger(__name__)


class Mismatch(BaseModel):
    kind: str
    symbol: str | None = None
    order_id: uuid.UUID | None = None
    broker_order_id: str | None = None
    local: str | None = None
    broker: str | None = None
    detail: str


class ReconciliationResult(BaseModel):
    ok: bool  # the comparison ran (not: no differences)
    at: datetime
    orders_compared: int = 0
    positions_compared: int = 0
    trades_compared: int = 0
    mismatches: list[Mismatch] = Field(default_factory=list)
    error: str | None = None

    @property
    def clean(self) -> bool:
        return self.ok and not self.mismatches


def compare(
    local_orders: list[Order],
    broker_orders: list[Order],
    broker_trades: list[Trade],
    local_positions: list[Position] | None,
    broker_positions: list[Position] | None,
    *,
    price_tolerance: float = 0.05,
) -> tuple[list[Mismatch], int, int, int]:
    """Pure comparison; returns (mismatches, orders, positions, trades compared)."""

    out: list[Mismatch] = []
    by_broker_id = {o.broker_order_id: o for o in broker_orders if o.broker_order_id}
    by_id = {o.id: o for o in broker_orders}
    local_broker_ids = {o.broker_order_id for o in local_orders if o.broker_order_id}
    local_ids = {o.id for o in local_orders}
    compared = 0
    for lo in local_orders:
        if lo.status is OrderStatus.REJECTED and not lo.broker_order_id:
            continue  # blocked or refused before reaching the broker
        bo = by_id.get(lo.id) or (
            by_broker_id.get(lo.broker_order_id) if lo.broker_order_id else None
        )
        if bo is None:
            if lo.broker_order_id or lo.needs_reconciliation:
                out.append(
                    Mismatch(
                        kind="MISSING_ORDER",
                        symbol=lo.symbol,
                        order_id=lo.id,
                        broker_order_id=lo.broker_order_id,
                        local=lo.status.value,
                        detail="tracked locally but not in the broker's order book",
                    )
                )
            continue
        compared += 1
        if lo.filled_quantity != bo.filled_quantity:
            out.append(
                Mismatch(
                    kind="QUANTITY_MISMATCH",
                    symbol=lo.symbol,
                    order_id=lo.id,
                    broker_order_id=bo.broker_order_id,
                    local=str(lo.filled_quantity),
                    broker=str(bo.filled_quantity),
                    detail="filled quantity differs",
                )
            )
        if lo.status is not bo.status:
            out.append(
                Mismatch(
                    kind="STATUS_MISMATCH",
                    symbol=lo.symbol,
                    order_id=lo.id,
                    broker_order_id=bo.broker_order_id,
                    local=lo.status.value,
                    broker=bo.status.value,
                    detail="status differs",
                )
            )
        if (
            lo.average_price is not None
            and bo.average_price is not None
            and abs(lo.average_price - bo.average_price) > price_tolerance
        ):
            out.append(
                Mismatch(
                    kind="AVG_PRICE_MISMATCH",
                    symbol=lo.symbol,
                    order_id=lo.id,
                    broker_order_id=bo.broker_order_id,
                    local=f"{lo.average_price:.2f}",
                    broker=f"{bo.average_price:.2f}",
                    detail="average fill price differs",
                )
            )
    for bo in broker_orders:
        if bo.id in local_ids or (bo.broker_order_id and bo.broker_order_id in local_broker_ids):
            continue
        out.append(
            Mismatch(
                kind="UNKNOWN_ORDER",
                symbol=bo.symbol,
                broker_order_id=bo.broker_order_id,
                broker=bo.status.value,
                detail="in the broker's order book but not placed by this app",
            )
        )

    traded: dict[str, int] = defaultdict(int)
    for t in broker_trades:
        if t.broker_order_id:
            traded[t.broker_order_id] += t.quantity
    for bo in broker_orders:
        if bo.broker_order_id and bo.filled_quantity != traded.get(bo.broker_order_id, 0):
            out.append(
                Mismatch(
                    kind="TRADE_MISMATCH",
                    symbol=bo.symbol,
                    broker_order_id=bo.broker_order_id,
                    local=str(bo.filled_quantity),
                    broker=str(traded.get(bo.broker_order_id, 0)),
                    detail="the order's executions do not add up to its filled quantity",
                )
            )

    positions = 0
    if local_positions is not None and broker_positions is not None:
        mine = {p.symbol: p.quantity for p in local_positions}
        theirs = {p.symbol: p.quantity for p in broker_positions}
        for symbol in sorted(set(mine) | set(theirs)):
            positions += 1
            if mine.get(symbol, 0) != theirs.get(symbol, 0):
                out.append(
                    Mismatch(
                        kind="POSITION_MISMATCH",
                        symbol=symbol,
                        local=str(mine.get(symbol, 0)),
                        broker=str(theirs.get(symbol, 0)),
                        detail="net position differs",
                    )
                )
    return out, compared, positions, len(broker_trades)


def positions_from_orders(orders: list[Order]) -> list[Position]:
    """Net positions implied by the fills of locally tracked orders."""

    net: dict[str, int] = defaultdict(int)
    for o in orders:
        signed = o.filled_quantity if o.side.value == "BUY" else -o.filled_quantity
        net[o.symbol] += signed
    return [Position(symbol=s, quantity=q, average_price=0.0) for s, q in sorted(net.items()) if q]


class OrderReconciliationService:
    def __init__(
        self,
        broker: BrokerAdapter,
        local_orders: Callable[[], list[Order]],
        local_positions: Callable[[], list[Position]] | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
        price_tolerance: float = 0.05,
    ) -> None:
        self.broker = broker
        self._local_orders = local_orders
        self._local_positions = local_positions
        self._clock = clock
        self.price_tolerance = price_tolerance
        self.last: ReconciliationResult | None = None

    async def run(self) -> ReconciliationResult:
        now = self._clock()
        try:
            orders = await self.broker.get_orders()
            trades = await self.broker.get_trades()
            positions = await self.broker.get_positions() if self._local_positions else None
        except BrokerError as exc:
            result = ReconciliationResult(ok=False, at=now, error=f"{type(exc).__name__}: {exc}")
            logger.warning("ORDER_RECONCILIATION failed: %s", result.error)
            self.last = result
            return result
        mismatches, n_orders, n_positions, n_trades = compare(
            self._local_orders(),
            orders,
            trades,
            self._local_positions() if self._local_positions else None,
            positions,
            price_tolerance=self.price_tolerance,
        )
        result = ReconciliationResult(
            ok=True,
            at=now,
            orders_compared=n_orders,
            positions_compared=n_positions,
            trades_compared=n_trades,
            mismatches=mismatches,
        )
        log = logger.warning if mismatches else logger.info
        log(
            "ORDER_RECONCILIATION broker=%s orders=%d positions=%d trades=%d mismatches=%d %s",
            self.broker.name,
            n_orders,
            n_positions,
            n_trades,
            len(mismatches),
            ",".join(sorted({m.kind for m in mismatches})),
        )
        self.last = result
        return result


__all__ = [
    "Mismatch",
    "OrderReconciliationService",
    "ReconciliationResult",
    "compare",
    "positions_from_orders",
]
