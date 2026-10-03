"""OrderManager + execution gate integration tests with the mock and paper
brokers, plus the live gate against the fake Kite server."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.brokers.zerodha import ZerodhaBrokerAdapter
from app.execution.models import (
    ConnectionState,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from app.execution.order_manager import ExecutionGate, OrderEvent, OrderManager
from app.execution.paper_broker import PaperBrokerAdapter
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, FakeKite


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def req(cid: str | None = "c1", **kw: Any) -> OrderRequest:
    base: dict[str, Any] = {
        "symbol": "RELIANCE",
        "side": Side.BUY,
        "quantity": 100,
        "order_type": OrderType.LIMIT,
        "price": 1400.0,
        "client_order_id": cid,
        "tag": "ENTRY",
    }
    base.update(kw)
    return OrderRequest(**base)


async def setup(**kw: Any) -> tuple[MockBrokerAdapter, OrderManager, Clock, list[OrderEvent]]:
    clock = Clock()
    broker = MockBrokerAdapter(clock=clock)
    await broker.connect()
    seen: list[OrderEvent] = []

    async def sink(events: list[OrderEvent]) -> None:
        seen.extend(events)

    om = OrderManager(
        broker, clock=clock, on_events=sink, unknown_grace_seconds=30, unknown_max_checks=3, **kw
    )
    return broker, om, clock, seen


def kinds(events: list[OrderEvent]) -> list[str]:
    return [e.kind for e in events]


# --- gate and duplicate protection ---------------------------------------------------------


async def test_orders_without_risk_approval_are_blocked_and_not_sent() -> None:
    broker, om, _, seen = await setup()
    order = await om.submit(req(), risk_approved=False)
    assert order.status is OrderStatus.REJECTED and "risk engine" in (order.rejection_reason or "")
    assert broker.calls["place"] == 0 and kinds(seen) == ["ORDER_BLOCKED"]


async def test_the_same_client_order_id_is_sent_once() -> None:
    broker, om, _, _ = await setup()
    first = await om.submit(req("same"), risk_approved=True)
    again = await om.submit(req("same", quantity=5), risk_approved=True)
    assert again.id == first.id and again.quantity == 100
    assert broker.calls["place"] == 1


async def test_concurrent_submissions_of_one_intent_send_one_order() -> None:
    broker, om, _, _ = await setup()
    results = await asyncio.gather(*(om.submit(req("race"), risk_approved=True) for _ in range(5)))
    assert len({r.id for r in results}) == 1 and broker.calls["place"] == 1


async def test_an_identical_working_order_within_the_window_is_refused() -> None:
    broker, om, clock, _ = await setup(duplicate_window_seconds=5)
    await om.submit(req("a"), risk_approved=True)
    dup = await om.submit(req("b"), risk_approved=True)
    assert dup.status is OrderStatus.REJECTED and "possible duplicate" in (
        dup.rejection_reason or ""
    )
    clock.advance(6)
    later = await om.submit(req("c"), risk_approved=True)
    assert later.status is OrderStatus.OPEN and broker.calls["place"] == 2


async def test_emergency_stop_blocks_new_orders() -> None:
    clock = Clock()
    broker = MockBrokerAdapter(clock=clock)
    await broker.connect()
    om = OrderManager(broker, ExecutionGate(emergency_stop=lambda: True), clock=clock)
    order = await om.submit(req(), risk_approved=True)
    assert "emergency stop" in (order.rejection_reason or "") and broker.calls["place"] == 0


# --- fills, rejections, cancellations ---------------------------------------------------------


async def test_partial_fills_are_recorded_as_deltas_until_filled() -> None:
    broker, om, _, seen = await setup()
    order = await om.submit(req("pf"), risk_approved=True)
    broker.fill(order.id, 30, 1400.0)
    await om.reconcile()
    broker.fill(order.id, 50, 1400.5)
    await om.reconcile()
    broker.fill(order.id, 20, 1401.0)
    await om.reconcile()
    await om.reconcile()  # nothing new: no duplicate events
    fills = [e for e in seen if e.kind in ("ORDER_PARTIAL_FILL", "ORDER_FILLED")]
    assert [e.filled_delta for e in fills] == [30, 50, 20]
    assert [e.kind for e in fills] == ["ORDER_PARTIAL_FILL", "ORDER_PARTIAL_FILL", "ORDER_FILLED"]
    assert om.order(order.id).status is OrderStatus.FILLED


async def test_a_broker_rejection_is_a_failed_order_with_its_reason() -> None:
    broker, om, _, seen = await setup()
    broker.reject_next("RMS: insufficient margin")
    order = await om.submit(req(), risk_approved=True)
    assert (
        order.status is OrderStatus.REJECTED
        and order.rejection_reason == "RMS: insufficient margin"
    )
    assert seen[-1].kind == "ORDER_REJECTED" and seen[-1].severity == "WARNING"
    # an exchange rejection after acceptance is picked up by reconciliation
    ok = await om.submit(req("later", symbol="TCS"), risk_approved=True)
    broker.exchange_reject(ok.id, "price band exceeded")
    await om.reconcile()
    assert om.order(ok.id).status is OrderStatus.REJECTED
    assert seen[-1].kind == "ORDER_REJECTED" and "price band" in seen[-1].message


async def test_a_partly_filled_order_cancelled_by_the_exchange_keeps_its_fill() -> None:
    broker, om, _, seen = await setup()
    order = await om.submit(req("pc"), risk_approved=True)
    broker.fill(order.id, 40, 1400.0)
    broker.exchange_cancel(order.id, "IOC remainder cancelled")
    await om.reconcile()
    final = om.order(order.id)
    assert final.status is OrderStatus.CANCELLED and final.filled_quantity == 40
    assert "ORDER_PARTIAL_FILL" in kinds(seen) and "cancelled after filling 40" in seen[-1].message


async def test_modify_and_cancel_go_through_the_manager() -> None:
    broker, om, _, _ = await setup()
    order = await om.submit(req("mc"), risk_approved=True)
    assert (await om.modify(order.id, OrderModification(price=1399.0))).price == 1399.0
    assert (await om.cancel(order.id)).status is OrderStatus.CANCELLED
    assert om.working() == []


async def test_emergency_cancel_all_cancels_working_orders_only() -> None:
    broker, om, _, _ = await setup()
    a = await om.submit(req("e1"), risk_approved=True)
    b = await om.submit(req("e2", symbol="TCS"), risk_approved=True)
    broker.fill(b.id, 100, 3000.0)
    await om.reconcile()
    cancelled = await om.cancel_all_working("emergency")
    assert [o.id for o in cancelled] == [a.id]
    assert om.order(b.id).status is OrderStatus.FILLED
    assert broker.positions_book["TCS"][0] == 100  # positions are never closed by it


# --- network failures and reconciliation ---------------------------------------------------------


async def test_a_lost_response_is_recovered_without_resending() -> None:
    broker, om, _, seen = await setup()
    broker.lose_next_response()
    order = await om.submit(req("lost"), risk_approved=True)
    assert broker.calls["place"] == 1
    # submit reconciles immediately and finds the order at the broker
    assert kinds(seen)[:2] == ["ORDER_UNKNOWN", "ORDER_RECOVERED"]
    tracked = om.order(order.id)
    assert tracked.status is OrderStatus.OPEN and not tracked.needs_reconciliation
    assert tracked.broker_order_id is not None and om.unresolved() == []
    again = await om.submit(req("lost"), risk_approved=True)
    assert again.id == order.id and broker.calls["place"] == 1


async def test_an_order_that_never_reached_the_broker_is_failed_after_a_grace_period() -> None:
    broker, om, clock, seen = await setup()
    broker.fail_next(1)  # the placement request itself fails
    order = await om.submit(req("gone"), risk_approved=True)
    assert om.order(order.id).needs_reconciliation
    for _ in range(3):
        await om.reconcile()
    assert om.order(order.id).status is OrderStatus.SUBMITTED  # not before the grace period
    clock.advance(31)
    await om.reconcile()
    failed = om.order(order.id)
    assert failed.status is OrderStatus.REJECTED and "treated as not placed" in (
        failed.rejection_reason or ""
    )
    assert seen[-1].kind == "ORDER_LOST" and seen[-1].severity == "CRITICAL"
    assert broker.calls["place"] == 0


async def test_an_order_marked_lost_that_appears_later_is_re_adopted() -> None:
    broker, om, clock, seen = await setup()
    broker.lose_next_response()
    broker.go_down()  # the immediate reconciliation cannot reach the broker
    order = await om.submit(req("late"), risk_approved=True)
    assert om.order(order.id).needs_reconciliation
    for _ in range(3):
        await om.reconcile()
    # nothing reached the broker: the lost-response order was never stored
    assert broker.calls["place"] == 0
    # unreadable order books do not count as "not found"
    assert om.order(order.id).status is OrderStatus.SUBMITTED
    broker.come_back()
    await om.check_connection()  # reconnects, then reconciles
    clock.advance(31)
    for _ in range(3):
        await om.reconcile()
    assert om.order(order.id).status is OrderStatus.REJECTED
    # simulate the broker finally showing the order (e.g. a delayed OMS write)
    broker._orders[order.id] = om.order(order.id).model_copy(
        update={
            "status": OrderStatus.OPEN,
            "broker_order_id": "MOCK-99",
            "broker_status": "OPEN",
            "rejection_reason": None,
        }
    )
    await om.reconcile()
    assert om.order(order.id).status is OrderStatus.OPEN
    assert "ORDER_REAPPEARED" in kinds(seen)


async def test_orders_placed_outside_the_app_are_reported_once() -> None:
    clock = Clock()
    broker = MockBrokerAdapter(clock=clock)
    await broker.connect()
    other = OrderManager(broker, clock=clock)
    await other.submit(
        req("elsewhere"), risk_approved=True
    )  # e.g. placed from the broker's own app
    om = OrderManager(broker, clock=clock)
    report = await om.reconcile()
    assert report.external == 1
    assert (await om.reconcile()).external == 0
    assert om.events()[0].kind == "EXTERNAL_ORDER"


async def test_a_disconnect_blocks_orders_until_reconnected_and_reconciled() -> None:
    broker, om, _, seen = await setup()
    await om.check_connection()
    order = await om.submit(req("before"), risk_approved=True)
    broker.go_down()
    for _ in range(3):
        await om.check_connection()
    assert broker.connection_state is ConnectionState.DISCONNECTED
    assert "BROKER_DISCONNECTED" in kinds(seen)
    blocked = await om.submit(req("during", symbol="TCS"), risk_approved=True)
    assert blocked.status is OrderStatus.REJECTED and "DISCONNECTED" in (
        blocked.rejection_reason or ""
    )
    # while it was away the order filled
    broker.fill(order.id, 100, 1400.0)
    broker.come_back()
    health = await om.check_connection()
    assert health.state is ConnectionState.CONNECTED and not om.resync_pending
    assert "BROKER_CONNECTED" in kinds(seen)
    assert om.order(order.id).status is OrderStatus.FILLED  # reconciled on reconnect
    ok = await om.submit(req("after", symbol="INFY"), risk_approved=True)
    assert ok.status is OrderStatus.OPEN


async def test_an_expired_session_blocks_orders_without_reconnect_attempts() -> None:
    broker, om, _, _ = await setup()
    broker.expire_session()
    health = await om.check_connection()
    assert health.state is ConnectionState.AUTH_REQUIRED
    order = await om.submit(req(), risk_approved=True)
    assert "AUTH_REQUIRED" in (order.rejection_reason or "")


# --- the paper broker behind the same manager ------------------------------------------------------


async def test_the_paper_broker_works_behind_the_order_manager() -> None:
    clock = Clock()
    paper = PaperBrokerAdapter(starting_capital=1_000_000, clock=clock)
    paper.on_price("RELIANCE", 1405.0, clock())
    om = OrderManager(paper, clock=clock)
    order = await om.submit(req("paper"), risk_approved=True)
    assert order.status is OrderStatus.OPEN
    paper.on_price("RELIANCE", 1399.0, clock())
    await om.reconcile()
    assert om.order(order.id).status is OrderStatus.FILLED
    assert om.events()[0].kind == "ORDER_FILLED"


# --- the live gate (Zerodha) -------------------------------------------------------------------------


def _zerodha(kite: FakeKite) -> ZerodhaBrokerAdapter:
    async def no_sleep(_: float) -> None:
        return None

    return ZerodhaBrokerAdapter(
        api_key=API_KEY,
        api_secret=API_SECRET,
        access_token=ACCESS_TOKEN,
        base_url="https://api.kite.test",
        transport=kite.transport(),
        clock=Clock(),
        sleep=no_sleep,
    )


@pytest.mark.parametrize(
    "missing",
    ["live_mode_configured", "user_armed", "system_healthy", "session_open"],
)
async def test_live_orders_need_every_condition(missing: str) -> None:
    kite = FakeKite()
    zerodha = _zerodha(kite)
    await zerodha.connect()
    conditions = {
        k: (lambda: True)
        for k in ("live_mode_configured", "user_armed", "system_healthy", "session_open")
    }
    conditions[missing] = lambda: False
    om = OrderManager(zerodha, ExecutionGate(**conditions))
    order = await om.submit(req("live"), risk_approved=True)
    assert order.status is OrderStatus.REJECTED and kite.posts() == []
    assert not zerodha.live_orders_armed


async def test_with_every_condition_a_live_order_is_sent_and_the_adapter_disarmed_again() -> None:
    kite = FakeKite()
    zerodha = _zerodha(kite)
    await zerodha.connect()
    gate = ExecutionGate(live_mode_configured=lambda: True, user_armed=lambda: True)
    om = OrderManager(zerodha, gate)
    order = await om.submit(req("go"), risk_approved=True)
    assert order.status is OrderStatus.OPEN and len(kite.posts()) == 1
    assert not zerodha.live_orders_armed  # armed only for that one request
    kite.set_status(order.broker_order_id or "", "COMPLETE", filled=100, average=1399.9)
    await om.reconcile()
    assert om.order(order.id).status is OrderStatus.FILLED


async def test_the_default_gate_never_allows_live_orders() -> None:
    kite = FakeKite()
    zerodha = _zerodha(kite)
    await zerodha.connect()
    om = OrderManager(zerodha)  # defaults: live not configured, not armed
    order = await om.submit(req("default"), risk_approved=True)
    assert order.status is OrderStatus.REJECTED and kite.posts() == []
    reasons = order.rejection_reason or ""
    assert "LIVE_MODE_ENABLED" in reasons and "armed" in reasons
