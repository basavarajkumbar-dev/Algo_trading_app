"""ZerodhaBrokerAdapter against a fake Kite Connect v3 server (no network)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr

from app.brokers.zerodha import ZerodhaBrokerAdapter, mapping, zerodha_from_settings
from app.core.config import Settings
from app.execution.broker import BrokerAuthError, BrokerError
from app.execution.models import (
    ConnectionState,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, REQUEST_TOKEN, FakeKite


async def _no_sleep(_: float) -> None:
    return None


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)  # 10:00 IST

    def __call__(self) -> datetime:
        return self.now


def make(
    kite: FakeKite, *, token: str | None = ACCESS_TOKEN, clock: Clock | None = None, **kw: Any
) -> ZerodhaBrokerAdapter:
    return ZerodhaBrokerAdapter(
        api_key=API_KEY,
        api_secret=API_SECRET,
        access_token=token,
        base_url="https://api.kite.test",
        transport=kite.transport(),
        clock=clock or Clock(),
        sleep=_no_sleep,
        **kw,
    )


async def connected(kite: FakeKite, **kw: Any) -> ZerodhaBrokerAdapter:
    a = make(kite, **kw)
    assert await a.connect() is ConnectionState.CONNECTED
    a.arm_live_orders()  # these tests exercise the adapter itself; the gate is tested separately
    return a


def req(cid: str = "c1", **kw: Any) -> OrderRequest:
    base: dict[str, Any] = {
        "symbol": "RELIANCE",
        "side": Side.BUY,
        "quantity": 10,
        "order_type": OrderType.LIMIT,
        "price": 1400.0,
        "client_order_id": cid,
        "tag": "ENTRY",
    }
    base.update(kw)
    return OrderRequest(**base)


# --- configuration and session ----------------------------------------------------------


async def test_without_credentials_it_reports_not_configured_and_never_calls_kite() -> None:
    a = ZerodhaBrokerAdapter(api_key=None, api_secret=None)
    assert a.connection_state is ConnectionState.NOT_CONFIGURED
    assert await a.connect() is ConnectionState.NOT_CONFIGURED
    health = await a.health_check()
    assert health.state is ConnectionState.NOT_CONFIGURED and not health.healthy
    assert "PAPER" in health.detail
    order = await a.place_order(req())
    assert order.status is OrderStatus.REJECTED


async def test_blank_environment_values_count_as_unset() -> None:
    blank: dict[str, Any] = {
        "ZERODHA_API_KEY": "  ",
        "ZERODHA_API_SECRET": "",
        "ZERODHA_ACCESS_TOKEN": "",
    }
    s = Settings(**blank)  # as read from an .env with empty values
    assert not s.zerodha_configured
    assert zerodha_from_settings(s).connection_state is ConnectionState.NOT_CONFIGURED


async def test_without_an_access_token_a_login_is_required() -> None:
    kite = FakeKite()
    a = make(kite, token=None)
    assert await a.connect() is ConnectionState.AUTH_REQUIRED
    assert kite.requests == []
    assert a.login_url() == f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}"


async def test_login_exchanges_the_request_token_with_the_documented_checksum() -> None:
    kite = FakeKite(token=None)
    a = make(kite, token=None)
    profile = await a.generate_session(REQUEST_TOKEN)
    method, path, form, _ = kite.requests[0]
    assert (method, path) == ("POST", "/session/token")
    assert form["checksum"] == mapping.session_checksum(API_KEY, REQUEST_TOKEN, API_SECRET)
    assert a.connection_state is ConnectionState.CONNECTED
    assert profile.user_id == "AB****" and not profile.is_paper
    # the token expires at 06:00 IST the next day
    assert a.session_expires_at == datetime(2026, 10, 2, 0, 30, tzinfo=UTC)
    assert ACCESS_TOKEN not in profile.model_dump_json()


async def test_requests_carry_the_version_and_authorization_headers() -> None:
    kite = FakeKite()
    a = await connected(kite)
    _, path, _, headers = kite.requests[-1]
    assert path == "/user/profile"
    assert headers["x-kite-version"] == "3"
    assert headers["authorization"] == f"token {API_KEY}:{ACCESS_TOKEN}"
    assert a.connection_state is ConnectionState.CONNECTED


async def test_secrets_never_reach_repr_settings_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    kite = FakeKite()
    kite.status_next = [
        (400, {"status": "error", "message": "bad", "error_type": "InputException"})
    ]
    a = make(kite)
    await a.connect()
    s = Settings(
        ZERODHA_API_KEY=API_KEY,
        ZERODHA_API_SECRET=SecretStr(API_SECRET),
        ZERODHA_ACCESS_TOKEN=SecretStr(ACCESS_TOKEN),
    )
    text = repr(a) + repr(s) + str(s.model_dump()) + caplog.text
    assert API_SECRET not in text and ACCESS_TOKEN not in text


async def test_an_expired_session_needs_a_new_login() -> None:
    clock = Clock()
    kite = FakeKite()
    a = await connected(kite, clock=clock)
    clock.now += timedelta(days=1)  # past 06:00 IST
    assert a.connection_state is ConnectionState.AUTH_REQUIRED
    with pytest.raises(BrokerAuthError):
        await a.get_positions()


async def test_a_rejected_token_needs_a_new_login_and_disarms_orders() -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.valid_token = "rotated"
    with pytest.raises(BrokerAuthError):
        await a.get_orders()
    assert a.connection_state is ConnectionState.AUTH_REQUIRED
    assert not a.live_orders_armed


# --- reads --------------------------------------------------------------------------------


async def test_profile_balance_positions_and_instruments_are_mapped() -> None:
    kite = FakeKite()
    a = await connected(kite)
    profile = await a.get_profile()
    assert profile.user_id == "AB****" and "MIS" in profile.products
    bal = await a.get_balance()
    assert bal.available == 98000.5 and bal.cash == 99000.0 and bal.used_margin == 1000.0
    assert bal.unrealized_pnl == 45.5 and bal.realized_pnl == -120.0
    (pos,) = await a.get_positions()
    assert (pos.symbol, pos.quantity, pos.average_price, pos.unrealized_pnl) == (
        "RELIANCE",
        -10,
        1400.5,
        55.0,
    )
    instruments = await a.get_instruments("NSE")
    assert [i.symbol for i in instruments] == ["RELIANCE", "TCS"]
    assert instruments[0].instrument_token == 738561 and instruments[0].tick_size == 0.1


async def test_reads_are_retried_on_gateway_errors() -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.status_next = [
        (502, {"status": "error", "message": "OMS down", "error_type": "NetworkException"})
    ]
    assert (await a.get_profile()).user_name == "Test"
    assert sum(1 for m, p, *_ in kite.requests if p == "/user/profile") == 3  # connect + 2 attempts


async def test_repeated_failures_disconnect_and_a_success_reconnects() -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.down = True
    for expected in (
        ConnectionState.DEGRADED,
        ConnectionState.DEGRADED,
        ConnectionState.DISCONNECTED,
    ):
        with pytest.raises(BrokerError):
            await a.get_orders()
        assert a.connection_state is expected
    assert not a.live_orders_armed
    health = await a.health_check()
    assert not health.healthy and health.consecutive_failures >= 3
    kite.down = False
    assert await a.connect() is ConnectionState.CONNECTED


async def test_a_requested_disconnect_stops_all_calls_until_connect() -> None:
    kite = FakeKite()
    a = await connected(kite)
    await a.disconnect()
    before = len(kite.requests)
    assert (await a.health_check()).state is ConnectionState.DISCONNECTED
    with pytest.raises(BrokerError):
        await a.get_orders()
    assert len(kite.requests) == before
    assert await a.connect() is ConnectionState.CONNECTED


# --- order placement ------------------------------------------------------------------------


async def test_orders_are_refused_unless_live_orders_are_armed() -> None:
    kite = FakeKite()
    a = make(kite)
    await a.connect()
    order = await a.place_order(req())
    assert order.status is OrderStatus.REJECTED and "not armed" in (order.rejection_reason or "")
    assert kite.posts() == []


async def test_a_limit_order_is_sent_with_the_documented_parameters() -> None:
    kite = FakeKite()
    a = await connected(kite)
    order = await a.place_order(req())
    (form,) = kite.posts()
    assert form == {
        "tradingsymbol": "RELIANCE",
        "exchange": "NSE",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "quantity": "10",
        "product": "MIS",
        "validity": "DAY",
        "tag": mapping.order_tag("c1"),
        "price": "1400.0",
    }
    assert len(form["tag"]) == 20 and form["tag"].isalnum()
    assert order.status is OrderStatus.OPEN and order.broker_order_id
    assert order.client_order_id == "c1" and order.tag == "ENTRY"


async def test_market_and_stop_orders_carry_market_protection() -> None:
    kite = FakeKite()
    a = await connected(kite)
    await a.place_order(req("m", order_type=OrderType.MARKET, price=None))
    stop = await a.place_order(
        req("s", side=Side.SELL, order_type=OrderType.SL_M, price=None, trigger_price=1390.0)
    )
    market, slm = kite.posts()
    assert market["market_protection"] == "-1" and "price" not in market
    assert (
        slm["order_type"] == "SL-M"
        and slm["trigger_price"] == "1390.0"
        and slm["market_protection"] == "-1"
    )
    assert stop.status is OrderStatus.TRIGGER_PENDING
    zero = await a.place_order(
        req("z", order_type=OrderType.MARKET, price=None, market_protection=0)
    )
    assert zero.status is OrderStatus.REJECTED and len(kite.posts()) == 2
    with pytest.raises(ValueError):
        make(kite, market_protection=0)


async def test_a_repeated_client_order_id_is_never_sent_twice() -> None:
    kite = FakeKite()
    a = await connected(kite)
    first = await a.place_order(req("dup"))
    again = await a.place_order(req("dup"))
    assert first.status is OrderStatus.OPEN
    assert again.status is OrderStatus.REJECTED and "duplicate" in (again.rejection_reason or "")
    assert len(kite.posts()) == 1
    missing = await a.place_order(req(None))  # type: ignore[arg-type]
    assert missing.status is OrderStatus.REJECTED and len(kite.posts()) == 1


async def test_a_lost_response_is_reconciled_by_tag_not_resent() -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.timeout_after_processing_next = True
    order = await a.place_order(req("lost"))
    assert order.status is OrderStatus.SUBMITTED and order.needs_reconciliation
    assert order.broker_order_id is None
    assert len(kite.posts()) == 1  # sent once, never retried
    # the broker has it; the next order-book read adopts it under our id
    book = await a.get_orders()
    (found,) = (o for o in book if o.id == order.id)
    assert found.broker_order_id in kite.orders and found.client_order_id == "lost"
    assert found.status is OrderStatus.OPEN
    status = await a.get_order_status(order.id)
    assert status.broker_order_id == found.broker_order_id
    assert len(kite.posts()) == 1


async def test_a_connection_failure_means_the_order_was_not_sent() -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.connect_error_next = 1
    order = await a.place_order(req("nc"))
    assert order.status is OrderStatus.REJECTED and "not sent" in (order.rejection_reason or "")
    assert not order.needs_reconciliation


async def test_writes_are_never_retried_after_a_gateway_error() -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.status_next = [
        (504, {"status": "error", "message": "timeout", "error_type": "NetworkException"})
    ]
    order = await a.place_order(req("gw"))
    assert order.needs_reconciliation and order.status is OrderStatus.SUBMITTED
    assert sum(1 for m, p, *_ in kite.requests if m == "POST" and p == "/orders/regular") == 1


@pytest.mark.parametrize(
    ("code", "error_type", "message", "expected"),
    [
        (400, "MarginException", "Insufficient funds", "Insufficient funds"),
        (400, "InputException", "Invalid `tradingsymbol`", "Invalid"),
        (429, "NetworkException", "Too many requests", "rate limit"),
    ],
)
async def test_broker_refusals_are_failed_orders_with_the_reason(
    code: int, error_type: str, message: str, expected: str
) -> None:
    kite = FakeKite()
    a = await connected(kite)
    kite.status_next = [(code, {"status": "error", "message": message, "error_type": error_type})]
    order = await a.place_order(req("r"))
    assert order.status is OrderStatus.REJECTED and expected in (order.rejection_reason or "")
    assert not order.needs_reconciliation
    assert a.connection_state is ConnectionState.CONNECTED  # the broker answered


async def test_an_exchange_rejection_after_acceptance_is_reported() -> None:
    kite = FakeKite()
    kite.initial_status = "REJECTED"
    a = await connected(kite)
    order = await a.place_order(req("x"))
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_reason == "RMS:Margin Exceeds"


async def test_partial_fills_and_completion_follow_the_order_book() -> None:
    kite = FakeKite()
    a = await connected(kite)
    order = await a.place_order(req("p", quantity=100))
    oid = order.broker_order_id
    assert oid is not None
    kite.set_status(oid, "OPEN", filled=40, average=1399.5)
    partial = await a.get_order_status(order.id)
    assert partial.status is OrderStatus.PARTIALLY_FILLED and partial.filled_quantity == 40
    kite.set_status(oid, "COMPLETE", filled=100, average=1399.8)
    done = await a.get_order_status(order.id)
    assert done.status is OrderStatus.FILLED and done.average_price == 1399.8


async def test_modify_and_cancel_keep_a_partial_fill() -> None:
    kite = FakeKite()
    a = await connected(kite)
    order = await a.place_order(req("mc", quantity=100))
    moved = await a.modify_order(order.id, OrderModification(price=1401.0))
    assert moved.price == 1401.0 and moved.modifications == 1
    put = [f for m, p, f, _ in kite.requests if m == "PUT"][0]
    assert put == {"order_type": "LIMIT", "price": "1401.0"}
    assert order.broker_order_id is not None
    kite.set_status(order.broker_order_id, "OPEN", filled=30, average=1401.0)
    cancelled = await a.cancel_order(order.id)
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == 30 and cancelled.cancelled_quantity == 70
    with pytest.raises(BrokerError):
        await a.cancel_order(order.id)


async def test_at_most_25_modifications_are_attempted() -> None:
    kite = FakeKite()
    a = await connected(kite)
    order = await a.place_order(req("many"))
    for i in range(mapping.MAX_MODIFICATIONS):
        await a.modify_order(order.id, OrderModification(price=1400.0 + i))
    with pytest.raises(BrokerError, match="25 modifications"):
        await a.modify_order(order.id, OrderModification(price=1500.0))


async def test_orders_placed_elsewhere_are_listed_but_not_ours() -> None:
    kite = FakeKite()
    a = await connected(kite)
    oid = kite.add_external()
    (external,) = await a.get_orders()
    assert external.broker_order_id == oid and external.client_order_id is None
    assert external.id == mapping.external_order_id(oid)


# --- mapping ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kite_status", "order_type", "filled", "previous", "expected"),
    [
        ("COMPLETE", OrderType.LIMIT, 10, None, OrderStatus.FILLED),
        ("REJECTED", OrderType.LIMIT, 0, None, OrderStatus.REJECTED),
        ("CANCELLED", OrderType.LIMIT, 4, None, OrderStatus.CANCELLED),
        ("OPEN", OrderType.LIMIT, 0, None, OrderStatus.OPEN),
        ("OPEN", OrderType.LIMIT, 3, None, OrderStatus.PARTIALLY_FILLED),
        ("TRIGGER PENDING", OrderType.SL_M, 0, None, OrderStatus.TRIGGER_PENDING),
        ("PUT ORDER REQ RECEIVED", OrderType.LIMIT, 0, None, OrderStatus.SUBMITTED),
        ("VALIDATION PENDING", OrderType.LIMIT, 0, None, OrderStatus.SUBMITTED),
        ("OPEN PENDING", OrderType.LIMIT, 0, None, OrderStatus.SUBMITTED),
        ("AMO REQ RECEIVED", OrderType.LIMIT, 0, None, OrderStatus.SUBMITTED),
        (
            "MODIFY PENDING",
            OrderType.SL_M,
            0,
            OrderStatus.TRIGGER_PENDING,
            OrderStatus.TRIGGER_PENDING,
        ),
        ("MODIFY VALIDATION PENDING", OrderType.LIMIT, 0, None, OrderStatus.OPEN),
        (
            "CANCEL PENDING",
            OrderType.LIMIT,
            5,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PARTIALLY_FILLED,
        ),
        ("MODIFIED", OrderType.SL_M, 0, None, OrderStatus.TRIGGER_PENDING),
        ("SOMETHING NEW", OrderType.LIMIT, 0, OrderStatus.OPEN, OrderStatus.OPEN),
    ],
)
def test_order_status_mapping(
    kite_status: str,
    order_type: OrderType,
    filled: int,
    previous: OrderStatus | None,
    expected: OrderStatus,
) -> None:
    assert (
        mapping.map_status(
            kite_status, order_type=order_type, filled_quantity=filled, previous=previous
        )
        is expected
    )


def test_tags_are_deterministic_short_and_alphanumeric() -> None:
    assert mapping.order_tag("abc") == mapping.order_tag("abc") != mapping.order_tag("abd")
    assert len(mapping.order_tag("x" * 500)) == 20 and mapping.order_tag("é/ü").isalnum()


def test_session_expiry_is_six_am_ist() -> None:
    before_six = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)  # 05:30 IST
    assert mapping.session_expiry(before_six) == datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
    after = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)  # 08:30 IST
    assert mapping.session_expiry(after) == datetime(2026, 10, 2, 0, 30, tzinfo=UTC)


def test_settings_reject_zero_market_protection() -> None:
    with pytest.raises(ValueError):
        Settings(ZERODHA_MARKET_PROTECTION=0)
    assert Settings().zerodha_market_protection == -1
    assert Settings().zerodha_orders_per_second <= 10
