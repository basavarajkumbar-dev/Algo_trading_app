"""OrderReconciliationService (local vs broker orders / trades / positions),
the safe retry of a LOST order, idempotency fields, and the Phase 13
broker-interface additions (trades, order trades, margins) for the paper,
mock and Zerodha adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.brokers.zerodha import ZerodhaBrokerAdapter
from app.execution.models import OrderRequest, OrderStatus, OrderType, Position, Side
from app.execution.order_manager import OrderManager
from app.execution.paper_broker import PaperBrokerAdapter, PaperBrokerConfig
from app.execution.reconciliation import (
    OrderReconciliationService,
    compare,
    positions_from_orders,
)
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, FakeKite


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def req(cid: str = "c1", **kw: Any) -> OrderRequest:
    base: dict[str, Any] = {
        "symbol": "RELIANCE",
        "side": Side.BUY,
        "quantity": 10,
        "order_type": OrderType.LIMIT,
        "price": 1400.0,
        "client_order_id": cid,
        "tag": "ENTRY",
        "strategy_signal_id": "sig-1",
        "correlation_id": "trade-1",
    }
    base.update(kw)
    return OrderRequest(**base)


async def setup() -> tuple[MockBrokerAdapter, OrderManager, Clock]:
    clock = Clock()
    broker = MockBrokerAdapter(clock=clock)
    await broker.connect()
    return broker, OrderManager(broker, clock=clock, unknown_grace_seconds=30), clock


async def test_clean_books_reconcile_without_mismatches() -> None:
    broker, om, _ = await setup()
    order = await om.submit(req(), risk_approved=True)
    broker.fill(order.id, 10, 1400.0)
    await om.reconcile()
    svc = OrderReconciliationService(broker, om.orders, lambda: positions_from_orders(om.orders()))
    result = await svc.run()
    assert result.ok and result.clean, result.mismatches
    assert result.orders_compared == 1 and result.trades_compared == 1
    tracked = om.order(order.id)
    assert (tracked.strategy_signal_id, tracked.correlation_id) == ("sig-1", "trade-1")


async def test_every_kind_of_mismatch_is_reported() -> None:
    broker, om, _ = await setup()
    a = await om.submit(req("a"), risk_approved=True)
    b = await om.submit(req("b", symbol="TCS", price=3000.0), risk_approved=True)
    broker.fill(a.id, 10, 1400.0)
    await om.reconcile()
    local = {o.id: o for o in om.orders()}  # local view before the broker moved on
    local[b.id] = local[b.id].model_copy(update={"status": OrderStatus.OPEN})
    broker.fill(b.id, 4, 3000.0)  # partially filled at the broker, unknown locally
    ghost = local[a.id].model_copy(
        update={"id": a.id.__class__(int=1), "broker_order_id": "MOCK-404"}
    )
    book = await broker.get_orders()
    book_a = next(o for o in book if o.id == a.id)
    book_a.average_price = 1401.0  # broker reports another average
    external = book_a.model_copy(update={"id": a.id.__class__(int=2), "broker_order_id": "EXT-1"})
    trades = await broker.get_trades()
    mismatches, n, _, _ = compare(
        [*local.values(), ghost],
        [*book, external],
        trades,
        [Position(symbol="RELIANCE", quantity=10, average_price=1400.0)],
        [
            Position(symbol="RELIANCE", quantity=10, average_price=1400.0),
            Position(symbol="TCS", quantity=4, average_price=3000.0),
        ],
    )
    kinds = {m.kind for m in mismatches}
    assert {
        "MISSING_ORDER",
        "UNKNOWN_ORDER",
        "QUANTITY_MISMATCH",
        "STATUS_MISMATCH",
        "AVG_PRICE_MISMATCH",
        "TRADE_MISMATCH",
        "POSITION_MISMATCH",
    } <= kinds
    assert n == 2


async def test_reconciliation_reports_a_broker_outage_instead_of_guessing() -> None:
    broker, om, _ = await setup()
    broker.go_down()
    result = await OrderReconciliationService(broker, om.orders).run()
    assert not result.ok and result.error and result.mismatches == []


async def test_an_uncertain_order_is_never_retried_blindly() -> None:
    broker, om, clock = await setup()
    broker.fail_next(1)  # the placement request fails: outcome unknown
    order = await om.submit(req("u1"), risk_approved=True)
    assert om.order(order.id).needs_reconciliation
    with pytest.raises(ValueError):  # not confirmed LOST yet: no retry
        await om.retry_lost(order.id, risk_approved=True)
    assert broker.calls["place"] == 0
    for _ in range(3):
        await om.reconcile()
    clock.now += timedelta(seconds=31)
    await om.reconcile()
    assert om.order(order.id).status is OrderStatus.REJECTED  # confirmed not at the broker
    retry = await om.retry_lost(order.id, risk_approved=True)
    assert retry.client_order_id == "u1-r1" and retry.correlation_id == "trade-1"
    assert broker.calls["place"] == 1
    again = await om.submit(req("u1-r1"), risk_approved=True)  # idempotent
    assert again.id == retry.id and broker.calls["place"] == 1


async def test_paper_and_mock_brokers_report_trades_and_margins() -> None:
    clock = Clock()
    paper = PaperBrokerAdapter(
        starting_capital=100_000.0, config=PaperBrokerConfig(slippage_bps=0), clock=clock
    )
    paper.on_price("RELIANCE", 1400.0)  # the paper broker needs a market price first
    order = await paper.place_order(req("p1", order_type=OrderType.MARKET, price=None, quantity=5))
    trades = await paper.get_trades()
    assert len(trades) == 1 and trades[0].quantity == 5 and trades[0].is_paper
    assert (await paper.get_order_trades(order.id))[0].order_id == order.id
    margins = await paper.get_margins()
    assert margins.is_paper and margins.used > 0
    broker, om, _ = await setup()
    o = await om.submit(req("m1"), risk_approved=True)
    broker.fill(o.id, 3, 1400.0)
    broker.fill(o.id, 7, 1400.5)
    assert [t.quantity for t in await broker.get_order_trades(o.id)] == [3, 7]


async def test_zerodha_trades_and_margins_map_from_kite() -> None:
    fake = FakeKite()
    fake.trades = [
        {
            "trade_id": "10000001",
            "order_id": "250930000000001",
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "average_price": 1400.5,
            "fill_timestamp": "2026-10-01 10:00:05",
        }
    ]
    a = ZerodhaBrokerAdapter(
        api_key=API_KEY,
        api_secret=API_SECRET,
        access_token=ACCESS_TOKEN,
        transport=fake.transport(),
    )
    await a.connect()
    (trade,) = await a.get_trades()
    assert trade.trade_id == "10000001" and trade.side is Side.BUY and not trade.is_paper
    assert trade.price == 1400.5 and trade.broker_order_id == "250930000000001"
    margins = await a.get_margins()
    assert margins.available_cash == 99000.0 and margins.used == 1000.0 and margins.net == 98000.5
    assert fake.posts() == []
