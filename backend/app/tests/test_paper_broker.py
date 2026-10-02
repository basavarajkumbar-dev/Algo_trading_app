"""PaperBrokerAdapter: order types, slippage, delay, partial fills,
modify / cancel, duplicates, charges and account accounting."""

from __future__ import annotations

import uuid

import pytest

from app.execution.broker import BrokerAdapter, BrokerError
from app.execution.costs import CostModel
from app.execution.models import (
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from app.execution.paper_broker import PaperBrokerAdapter, PaperBrokerConfig
from app.tests.paper_helpers import Clock

SYM = "TCS"


def broker(clock: Clock | None = None, capital: float = 100_000.0, **cfg) -> PaperBrokerAdapter:
    clock = clock or Clock()
    b = PaperBrokerAdapter(
        starting_capital=capital,
        config=PaperBrokerConfig(**{"slippage_bps": 0.0, **cfg}),
        clock=clock,
    )
    b.on_price(SYM, 1000.0)
    return b


def req(side: Side = Side.BUY, qty: int = 10, **kw) -> OrderRequest:
    return OrderRequest(symbol=SYM, side=side, quantity=qty, **kw)


# --- interface ----------------------------------------------------------------------


async def test_implements_the_broker_adapter_interface() -> None:
    b = broker()
    assert isinstance(b, BrokerAdapter) and b.is_paper
    order = await b.place_order(req())
    assert (await b.get_order_status(order.id)).status == OrderStatus.FILLED
    positions = await b.get_positions()
    assert positions[0].quantity == 10
    balance = await b.get_balance()
    assert balance.used_margin == 10_000.0
    working = await b.place_order(req(Side.SELL, 10, order_type=OrderType.LIMIT, price=1100))
    modified = await b.modify_order(working.id, OrderModification(price=1105))
    assert modified.price == 1105
    cancelled = await b.cancel_order(working.id)
    assert cancelled.status == OrderStatus.CANCELLED


# --- market orders and slippage ----------------------------------------------------------


def test_market_buy_fills_immediately_at_last_price_without_delay() -> None:
    b = broker()
    o = b.submit(req())
    assert o.status == OrderStatus.FILLED
    assert o.filled_quantity == 10 and o.average_price == 1000.0


def test_slippage_is_adverse_and_tick_rounded() -> None:
    b = broker(slippage_bps=10)  # 0.1%
    buy = b.submit(req())
    assert buy.average_price == 1001.0
    assert buy.fills[0].slippage == 1.0 and buy.fills[0].market_price == 1000.0
    sell = b.submit(req(Side.SELL))
    assert sell.average_price == 999.0
    b.on_price(SYM, 1234.57)
    odd = b.submit(req(Side.BUY, 1))
    assert odd.average_price == 1235.85  # 1235.80457 rounded UP to the tick


def test_execution_delay_holds_the_order_until_a_later_price() -> None:
    clock = Clock()
    b = broker(clock, execution_delay_seconds=2)
    o = b.submit(req())
    assert o.status == OrderStatus.OPEN and o.filled_quantity == 0
    clock.advance(1)
    assert b.on_price(SYM, 1001.0, clock()) == []
    clock.advance(1.5)
    fills = b.on_price(SYM, 1002.0, clock())
    assert len(fills) == 1 and fills[0].price == 1002.0
    assert b.order(o.id).status == OrderStatus.FILLED


# --- limit and stop orders ---------------------------------------------------------------


def test_limit_sell_fills_at_limit_when_reached() -> None:
    b = broker()
    b.submit(req())
    tp = b.submit(req(Side.SELL, order_type=OrderType.LIMIT, price=1020))
    assert tp.status == OrderStatus.OPEN
    assert b.on_price(SYM, 1019.95) == []
    fills = b.on_price(SYM, 1025.0)  # gap above the limit
    assert fills[0].price == 1020.0  # no price improvement assumed
    assert b.order(tp.id).status == OrderStatus.FILLED


def test_limit_buy_fills_when_price_falls_to_limit() -> None:
    b = broker()
    b.submit(req(order_type=OrderType.LIMIT, price=990))
    assert b.on_price(SYM, 995) == []
    assert b.on_price(SYM, 990)[0].price == 990


def test_stop_market_triggers_and_fills_at_gap_price_with_slippage() -> None:
    b = broker(slippage_bps=10)
    b.submit(req())
    sl = b.submit(req(Side.SELL, order_type=OrderType.SL_M, trigger_price=990))
    assert sl.status == OrderStatus.TRIGGER_PENDING
    assert b.on_price(SYM, 990.05) == []
    fills = b.on_price(SYM, 985.0)  # gapped through 990
    assert fills[0].price == 984.0  # 985 - 0.1%, rounded down: worse than the trigger
    order = b.order(sl.id)
    assert order.triggered and order.status == OrderStatus.FILLED


def test_buy_stop_for_a_short() -> None:
    b = broker()
    b.submit(req(Side.SELL))
    sl = b.submit(req(Side.BUY, order_type=OrderType.SL_M, trigger_price=1010))
    assert b.on_price(SYM, 1009) == []
    assert b.on_price(SYM, 1010)[0].price == 1010
    assert b.order(sl.id).status == OrderStatus.FILLED


def test_stop_already_through_trigger_is_rejected() -> None:
    b = broker()
    sell_stop = b.submit(req(Side.SELL, order_type=OrderType.SL_M, trigger_price=1000))
    assert sell_stop.status == OrderStatus.REJECTED
    assert "not below last price" in (sell_stop.rejection_reason or "")
    buy_stop = b.submit(req(Side.BUY, order_type=OrderType.SL_M, trigger_price=999))
    assert buy_stop.status == OrderStatus.REJECTED


# --- partial fills -----------------------------------------------------------------------


def test_partial_fills_complete_over_several_prices() -> None:
    b = broker(max_fill_fraction=0.4)
    o = b.submit(req(qty=10))
    assert o.status == OrderStatus.PARTIALLY_FILLED and o.filled_quantity == 4
    b.on_price(SYM, 1002)
    mid = b.order(o.id)
    assert mid.filled_quantity == 8 and mid.status == OrderStatus.PARTIALLY_FILLED
    b.on_price(SYM, 1004)
    done = b.order(o.id)
    assert done.filled_quantity == 10 and done.status == OrderStatus.FILLED
    assert [f.quantity for f in done.fills] == [4, 4, 2]
    assert done.average_price == pytest.approx((4 * 1000 + 4 * 1002 + 2 * 1004) / 10)


def test_cancel_after_partial_fill_keeps_the_filled_part() -> None:
    b = broker(max_fill_fraction=0.5)
    o = b.submit(req(qty=10))
    c = b.cancel(o.id)
    assert c.status == OrderStatus.CANCELLED and c.filled_quantity == 5
    assert b.positions()[0].quantity == 5
    assert b.on_price(SYM, 1001) == []


# --- modify / cancel -----------------------------------------------------------------------


def test_modify_quantity_and_trigger() -> None:
    b = broker()
    b.submit(req(qty=20))
    sl = b.submit(req(Side.SELL, 20, order_type=OrderType.SL_M, trigger_price=990))
    m = b.modify(sl.id, OrderModification(quantity=15, trigger_price=992.02))
    assert m.quantity == 15 and m.trigger_price == 992.0
    fills = b.on_price(SYM, 991)
    assert fills[0].quantity == 15


def test_modify_rejections() -> None:
    b = broker(max_fill_fraction=0.5)
    o = b.submit(req(qty=10))  # 5 filled, 5 working
    with pytest.raises(BrokerError):
        b.modify(o.id, OrderModification(quantity=4))  # below filled
    with pytest.raises(BrokerError):
        b.modify(o.id, OrderModification(price=1001))  # MARKET has no price
    with pytest.raises(BrokerError):
        b.modify(o.id, OrderModification(trigger_price=1001))
    shrunk = b.modify(o.id, OrderModification(quantity=5))
    assert shrunk.status == OrderStatus.FILLED  # nothing left to fill
    with pytest.raises(BrokerError):
        b.modify(o.id, OrderModification(quantity=6))


def test_cannot_cancel_a_filled_order_or_an_unknown_one() -> None:
    b = broker()
    o = b.submit(req())
    with pytest.raises(BrokerError):
        b.cancel(o.id)
    with pytest.raises(BrokerError):
        b.order(uuid.uuid4())


# --- validation --------------------------------------------------------------------------


def test_duplicate_client_order_id_is_rejected() -> None:
    b = broker()
    first = b.submit(req(client_order_id="sig-1-entry"))
    second = b.submit(req(client_order_id="sig-1-entry"))
    assert first.status == OrderStatus.FILLED
    assert second.status == OrderStatus.REJECTED
    assert "duplicate client_order_id" in (second.rejection_reason or "")
    assert b.positions()[0].quantity == 10  # only one fill


@pytest.mark.parametrize(
    "request_kw, reason",
    [
        ({"qty": 0}, "positive"),
        ({"qty": 7}, "lot size"),
        ({"order_type": OrderType.LIMIT}, "needs a price"),
        ({"order_type": OrderType.SL_M}, "needs a trigger"),
        ({"qty": 101}, "insufficient margin"),
    ],
)
def test_invalid_orders_are_rejected(request_kw: dict, reason: str) -> None:
    b = broker(lot_size=1 if request_kw.get("qty") != 7 else 5)
    o = b.submit(req(**request_kw))
    assert o.status == OrderStatus.REJECTED and reason in (o.rejection_reason or "")


def test_order_without_any_price_is_rejected() -> None:
    b = broker()
    o = b.submit(OrderRequest(symbol="UNKNOWN", side=Side.BUY, quantity=1))
    assert o.status == OrderStatus.REJECTED and "no market price" in (o.rejection_reason or "")


def test_closing_order_needs_no_margin() -> None:
    b = broker(capital=10_000)
    assert b.submit(req(qty=10)).status == OrderStatus.FILLED  # uses all capital
    assert b.submit(req(Side.SELL, 10)).status == OrderStatus.FILLED


# --- charges -------------------------------------------------------------------------------


def test_charges_follow_the_cost_model() -> None:
    costs = CostModel()
    buy = costs.fill_charges(side=Side.BUY, turnover=100_000)
    assert buy.brokerage == 20.0  # capped (0.03% would be 30)
    assert buy.stt == 0.0 and buy.stamp_duty == 3.0
    assert buy.exchange == pytest.approx(2.97)
    assert buy.sebi == pytest.approx(0.1)
    assert buy.gst == pytest.approx((20 + 2.97 + 0.1) * 0.18, abs=1e-3)
    sell = costs.fill_charges(side=Side.SELL, turnover=10_000)
    assert sell.brokerage == 3.0 and sell.stt == 2.5 and sell.stamp_duty == 0.0


def test_brokerage_cap_applies_per_order_across_partial_fills() -> None:
    b = broker(capital=1_000_000, max_fill_fraction=0.5)
    o = b.submit(req(qty=200))  # ₹200,000 turnover in two fills of ₹100,000
    b.on_price(SYM, 1000)
    done = b.order(o.id)
    assert done.status == OrderStatus.FILLED
    assert done.charges.brokerage == 20.0  # not 40


def test_zero_cost_model_charges_nothing() -> None:
    free = CostModel(
        brokerage_rate=0,
        stt_sell_rate=0,
        exchange_rate=0,
        sebi_per_crore=0,
        stamp_buy_rate=0,
        gst_rate=0,
    )
    b = broker(costs=free)
    assert b.submit(req()).charges.total == 0


# --- positions and balance ------------------------------------------------------------------


def test_long_round_trip_accounting() -> None:
    b = broker()
    b.submit(req(qty=10))
    b.on_price(SYM, 1010)
    mid = b.balance()
    assert mid.unrealized_pnl == 100.0 and mid.used_margin == 10_000
    assert mid.available == pytest.approx(mid.cash - 10_000)
    b.submit(req(Side.SELL, 10))
    bal = b.balance()
    assert bal.realized_pnl == 100.0 and bal.unrealized_pnl == 0 and bal.used_margin == 0
    assert bal.cash == pytest.approx(100_000 + 100 - bal.charges_paid)
    assert bal.equity == bal.cash
    pos = b.positions()[0]
    assert pos.quantity == 0 and pos.realized_pnl == 100.0


def test_short_round_trip_accounting() -> None:
    b = broker()
    b.submit(req(Side.SELL, 10))
    b.on_price(SYM, 990)
    assert b.positions()[0].unrealized_pnl == 100.0
    b.submit(req(Side.BUY, 10))
    assert b.balance().realized_pnl == 100.0


def test_position_flip_realizes_then_opens_the_rest() -> None:
    b = broker()
    b.submit(req(qty=10))
    b.on_price(SYM, 1010)
    b.submit(req(Side.SELL, 15))
    pos = b.positions()[0]
    assert pos.quantity == -5 and pos.average_price == 1010
    assert pos.realized_pnl == 100.0
    assert b.open_exposure() == 5 * 1010
