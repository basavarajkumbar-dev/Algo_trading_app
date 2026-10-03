"""Every adapter implements the full BrokerAdapter interface the same way:
paper, mock, and Zerodha (against the fake Kite server, live orders armed
directly for the test; the execution gate is tested elsewhere)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.brokers.zerodha import ZerodhaBrokerAdapter
from app.execution.broker import BrokerAdapter
from app.execution.models import (
    Balance,
    BrokerHealth,
    BrokerProfile,
    ConnectionState,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from app.execution.paper_broker import PaperBrokerAdapter
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, FakeKite

NOW = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)


async def _paper() -> BrokerAdapter:
    b = PaperBrokerAdapter(starting_capital=1_000_000, clock=lambda: NOW)
    b.on_price("RELIANCE", 1410.0, NOW)
    return b


async def _mock() -> BrokerAdapter:
    return MockBrokerAdapter(clock=lambda: NOW)


async def _zerodha() -> BrokerAdapter:
    async def no_sleep(_: float) -> None:
        return None

    z = ZerodhaBrokerAdapter(
        api_key=API_KEY,
        api_secret=API_SECRET,
        access_token=ACCESS_TOKEN,
        base_url="https://api.kite.test",
        transport=FakeKite().transport(),
        clock=lambda: NOW,
        sleep=no_sleep,
    )
    return z


FACTORIES: dict[str, Callable[[], Awaitable[BrokerAdapter]]] = {
    "paper": _paper,
    "mock": _mock,
    "zerodha": _zerodha,
}


@pytest.fixture(params=list(FACTORIES))
async def broker(request: pytest.FixtureRequest) -> BrokerAdapter:
    b = await FACTORIES[request.param]()
    assert await b.connect() is ConnectionState.CONNECTED
    if isinstance(b, ZerodhaBrokerAdapter):
        b.arm_live_orders()
    return b


async def test_connection_health_and_account(broker: BrokerAdapter) -> None:
    assert broker.connection_state is ConnectionState.CONNECTED
    health = await broker.health_check()
    assert isinstance(health, BrokerHealth) and health.healthy
    profile = await broker.get_profile()
    assert isinstance(profile, BrokerProfile) and profile.is_paper is broker.is_paper
    assert isinstance(await broker.get_balance(), Balance)
    assert isinstance(await broker.get_positions(), list)
    instruments = await broker.get_instruments("NSE")
    assert any(i.symbol == "RELIANCE" for i in instruments)


async def test_order_lifecycle(broker: BrokerAdapter) -> None:
    request = OrderRequest(
        symbol="RELIANCE",
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.LIMIT,
        price=1400.0,
        client_order_id="conf-1",
        tag="ENTRY",
    )
    order = await broker.place_order(request)
    assert order.status is OrderStatus.OPEN and order.client_order_id == "conf-1"
    assert (await broker.get_order_status(order.id)).id == order.id
    assert order.id in {o.id for o in await broker.get_orders()}
    modified = await broker.modify_order(order.id, OrderModification(price=1401.0))
    assert modified.price == 1401.0
    cancelled = await broker.cancel_order(order.id)
    assert cancelled.status is OrderStatus.CANCELLED
    duplicate = await broker.place_order(request)
    assert duplicate.status is OrderStatus.REJECTED
    assert "duplicate" in (duplicate.rejection_reason or "")


async def test_disconnect_is_supported(broker: BrokerAdapter) -> None:
    await broker.disconnect()
    assert await broker.connect() is ConnectionState.CONNECTED
