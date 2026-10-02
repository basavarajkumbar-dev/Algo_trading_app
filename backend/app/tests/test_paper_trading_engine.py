"""The paper trading engine end to end:

SIGNAL -> RISK -> PAPER ORDER -> FILL -> POSITION -> SL/TP -> EXIT -> TRADE -> P&L
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.execution.models import OrderStatus, OrderType
from app.risk.schemas import RejectionReason as R
from app.risk.schemas import TradeSide
from app.tests.paper_helpers import Clock, conditions, intent, make_engine, open_trade, price
from app.trading.engine import EngineError, PaperTradingEngine
from app.trading.schemas import ExitReason, OrderPurpose, TradeStatus
from app.trading.state import InvalidTransition, TradingState, TradingStateMachine

S = TradingState


def orders_of(engine: PaperTradingEngine, purpose: OrderPurpose):
    assert engine.active is not None
    return [engine.broker.order(i) for i in engine.active.order_ids.get(purpose.value, [])]


def states(engine: PaperTradingEngine) -> list[str]:
    return [c.to_state.value for c in engine.machine.history]


# --- state machine -------------------------------------------------------------------


def test_state_machine_allows_the_lifecycle_and_nothing_else() -> None:
    clock = Clock()
    m = TradingStateMachine(clock())
    for target in (
        S.SCANNING,
        S.SIGNAL_FOUND,
        S.RISK_CHECK,
        S.ORDER_PENDING,
        S.POSITION_ACTIVE,
        S.EXIT,
        S.CLOSED,
        S.SCANNING,
    ):
        m.to(target, clock(), "test")
    assert m.state == S.SCANNING and len(m.history) == 8
    for bad in (S.POSITION_ACTIVE, S.ORDER_PENDING, S.EXIT, S.CLOSED):
        assert not m.can(bad)
        with pytest.raises(InvalidTransition):
            m.to(bad, clock())


def test_position_active_can_only_go_to_exit() -> None:
    clock = Clock()
    m = TradingStateMachine(clock())
    for target in (S.SIGNAL_FOUND, S.RISK_CHECK, S.ORDER_PENDING, S.POSITION_ACTIVE):
        m.to(target, clock())
    for bad in (S.IDLE, S.SCANNING, S.SIGNAL_FOUND, S.CLOSED):
        with pytest.raises(InvalidTransition):
            m.to(bad, clock())


# --- the full lifecycle: target ---------------------------------------------------------


async def test_long_trade_hits_target() -> None:
    clock = Clock()
    e = make_engine(clock)
    d = await open_trade(e, clock)
    assert d.approved and d.size is not None and d.size.quantity == 50
    assert e.state == S.POSITION_ACTIVE
    trade = e.active
    assert trade is not None and trade.status == TradeStatus.OPEN
    entry = orders_of(e, OrderPurpose.ENTRY)[0]
    assert entry.order_type == OrderType.MARKET and entry.status == OrderStatus.FILLED
    stop = orders_of(e, OrderPurpose.STOP_LOSS)[0]
    target = orders_of(e, OrderPurpose.TARGET)[0]
    assert (stop.order_type, stop.trigger_price, stop.quantity) == (OrderType.SL_M, 990.0, 50)
    assert (target.order_type, target.price, target.quantity) == (OrderType.LIMIT, 1020.0, 50)

    await price(e, clock, 1010)
    assert e.active is not None and e.active.unrealized_pnl == 500.0
    await price(e, clock, 1020)

    assert e.state == S.IDLE and e.active is None
    closed = e.closed[-1]
    assert closed.status == TradeStatus.CLOSED and closed.exit_reason == ExitReason.TARGET
    assert closed.entry_price == 1000 and closed.exit_price == 1020
    assert closed.gross_pnl == 1000.0
    assert closed.charges > 0
    assert closed.net_pnl == pytest.approx(1000.0 - closed.charges)
    assert closed.r_multiple == pytest.approx(closed.net_pnl / 500, abs=0.01)
    assert e.broker.order(stop.id).status == OrderStatus.CANCELLED  # OCO
    assert states(e) == [
        "SIGNAL_FOUND",
        "RISK_CHECK",
        "ORDER_PENDING",
        "POSITION_ACTIVE",
        "EXIT",
        "CLOSED",
        "IDLE",
    ]
    bal = e.broker.balance()
    assert bal.cash == pytest.approx(100_000 + closed.net_pnl)
    assert e.risk.state.trades_today == 1 and e.risk.state.realized_pnl_today == closed.net_pnl


async def test_short_trade_hits_target() -> None:
    clock = Clock()
    e = make_engine(clock)
    d = await open_trade(e, clock, side=TradeSide.SHORT)
    assert d.approved
    stop = orders_of(e, OrderPurpose.STOP_LOSS)[0]
    assert stop.side.value == "BUY" and stop.trigger_price == 1010
    await price(e, clock, 990)
    await price(e, clock, 980)
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.TARGET and t.gross_pnl == 1000.0


# --- stop loss ---------------------------------------------------------------------------


async def test_long_trade_hits_stop_loss() -> None:
    clock = Clock()
    e = make_engine(clock)
    await open_trade(e, clock)
    target_id = orders_of(e, OrderPurpose.TARGET)[0].id
    await price(e, clock, 995)
    await price(e, clock, 990)
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.STOP_LOSS
    assert t.gross_pnl == -500.0
    assert t.net_pnl < -500.0  # charges make a loss bigger
    assert t.r_multiple is not None and t.r_multiple < -1
    assert e.broker.order(target_id).status == OrderStatus.CANCELLED
    assert e.risk.state.consecutive_losses == 1


async def test_short_trade_hits_stop_loss() -> None:
    clock = Clock()
    e = make_engine(clock)
    await open_trade(e, clock, side=TradeSide.SHORT)
    await price(e, clock, 1012)
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.STOP_LOSS and t.exit_price == 1012
    assert t.gross_pnl == -600.0  # gapped 2 points past the stop


async def test_gap_through_stop_fills_worse_with_slippage() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"slippage_bps": 10})
    await open_trade(e, clock)
    entry = e.active.entry_price if e.active else None
    assert entry == 1001.0  # market entry slipped 0.1%
    await price(e, clock, 980)
    t = e.closed[-1]
    assert t.exit_price == 979.0  # 980 - 0.1%
    assert t.gross_pnl == pytest.approx((979 - 1001) * t.filled_quantity)


async def test_stop_rejected_because_price_already_crossed_exits_at_market() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"execution_delay_seconds": 1})
    await open_trade(e, clock)
    assert e.state == S.ORDER_PENDING  # delayed fill
    await price(e, clock, 989)  # entry fills at 989: already below the 990 stop
    assert e.state == S.EXIT  # protective stop refused, market exit working
    await price(e, clock, 988)
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.STOP_LOSS
    assert orders_of_closed(e, t, OrderPurpose.STOP_LOSS)[0].status == OrderStatus.REJECTED
    assert e.state == S.IDLE


def orders_of_closed(e: PaperTradingEngine, trade, purpose: OrderPurpose):
    return [e.broker.order(i) for i in trade.order_ids.get(purpose.value, [])]


# --- one active trade and duplicates ------------------------------------------------------


async def test_only_one_active_trade_at_a_time() -> None:
    clock = Clock()
    e = make_engine(clock, risk={"max_exposure": 200_000})
    await open_trade(e, clock)
    first = e.active
    await e.on_price("INFY", 1500, clock())
    second = await e.execute(
        intent(symbol="INFY", entry=1500, stop=1490, target=1530), conditions(clock, 1500)
    )
    assert not second.approved
    assert R.ACTIVE_POSITION_EXISTS in second.rejection_reasons
    assert e.state == S.POSITION_ACTIVE and e.active is first
    assert [p.symbol for p in e.broker.positions() if p.quantity] == ["RELIANCE"]


async def test_one_active_trade_while_entry_is_pending() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"execution_delay_seconds": 5})
    await open_trade(e, clock)
    assert e.state == S.ORDER_PENDING
    d = await e.execute(intent(symbol="INFY"), conditions(clock))
    assert R.ACTIVE_POSITION_EXISTS in d.rejection_reasons
    assert e.state == S.ORDER_PENDING


async def test_same_signal_cannot_be_executed_twice() -> None:
    clock = Clock()
    e = make_engine(clock)
    i = intent()
    await e.on_price("RELIANCE", 1000, clock())
    assert (await e.execute(i, conditions(clock))).approved
    await price(e, clock, 1020)  # closed at target
    again = await e.execute(i, conditions(clock, 1000))
    assert again.rejection_reasons == [R.DUPLICATE_ORDER]
    assert e.state == S.IDLE


async def test_broker_rejects_a_repeated_client_order_id_too() -> None:
    clock = Clock()
    e = make_engine(clock)
    i = intent()
    await e.on_price("RELIANCE", 1000, clock())
    await e.execute(i, conditions(clock))
    await price(e, clock, 1020)
    # bypass the risk manager's memory (e.g. after a restart) - the broker still refuses
    e.risk.state.executed_signal_ids.clear()
    d = await e.execute(i, conditions(clock, 1020.0).model_copy(update={"last_price": 1000}))
    assert d.approved  # risk had no record...
    t = e.closed[-1]
    assert t.status == TradeStatus.REJECTED and t.exit_reason == ExitReason.ENTRY_REJECTED
    assert e.state == S.IDLE
    assert len([p for p in e.broker.positions() if p.quantity]) == 0


# --- partial fills -------------------------------------------------------------------------


async def test_partial_entry_fills_resize_the_protective_orders() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"max_fill_fraction": 0.4})
    await open_trade(e, clock)  # 50 approved, 20 filled now
    assert e.state == S.POSITION_ACTIVE
    stop = orders_of(e, OrderPurpose.STOP_LOSS)[0]
    target = orders_of(e, OrderPurpose.TARGET)[0]
    assert stop.quantity == 20 and target.quantity == 20
    await price(e, clock, 1001)  # +20
    assert e.broker.order(stop.id).quantity == 40
    assert e.broker.order(target.id).quantity == 40
    await price(e, clock, 1002)  # +10 -> 50
    assert e.active is not None and e.active.filled_quantity == 50
    assert e.broker.order(stop.id).quantity == 50
    entry = orders_of(e, OrderPurpose.ENTRY)[0]
    assert entry.status == OrderStatus.FILLED
    assert entry.average_price == pytest.approx((20 * 1000 + 20 * 1001 + 10 * 1002) / 50)


async def test_partial_exit_fills_close_the_trade_over_several_prices() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"max_fill_fraction": 0.4})
    await open_trade(e, clock)
    await price(e, clock, 1001)
    await price(e, clock, 1002)  # fully in: 50
    await price(e, clock, 1020)  # target fills 20
    assert e.state == S.POSITION_ACTIVE and e.active is not None
    assert e.active.open_quantity == 30
    stop = orders_of(e, OrderPurpose.STOP_LOSS)[0]
    assert e.broker.order(stop.id).remaining_quantity == 30  # OCO resized
    await price(e, clock, 1021)
    await price(e, clock, 1022)
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.TARGET and t.filled_quantity == 50
    assert t.exit_price == 1020.0


async def test_entry_remainder_is_cancelled_when_the_entry_times_out() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"max_fill_fraction": 0.4}, engine={"entry_timeout_seconds": 5})
    await open_trade(e, clock)  # 20 of 50
    clock.advance(6)
    await e.on_clock()
    entry = orders_of(e, OrderPurpose.ENTRY)[0]
    assert entry.status == OrderStatus.CANCELLED and entry.filled_quantity == 20
    assert e.state == S.POSITION_ACTIVE
    for _ in range(3):  # the target fills 8 + 8 + 4
        await price(e, clock, 1020)
    t = e.closed[-1]
    assert t.filled_quantity == 20 and t.quantity == 50
    assert t.gross_pnl == 400.0


async def test_unfilled_entry_expires_and_closes_without_a_trade() -> None:
    clock = Clock()
    e = make_engine(
        clock, broker={"execution_delay_seconds": 30}, engine={"entry_timeout_seconds": 10}
    )
    await open_trade(e, clock)
    assert e.state == S.ORDER_PENDING
    clock.advance(11)
    await e.on_clock()
    t = e.closed[-1]
    assert t.status == TradeStatus.CANCELLED and t.exit_reason == ExitReason.ENTRY_EXPIRED
    assert t.filled_quantity == 0 and t.net_pnl == 0
    assert e.risk.state.trades_today == 0  # nothing was traded
    assert "CLOSED" in states(e) and e.state == S.IDLE


# --- account limits through the engine ------------------------------------------------------


async def lose(e: PaperTradingEngine, clock: Clock, symbol: str = "RELIANCE") -> None:
    d = await open_trade(e, clock, symbol=symbol)
    assert d.approved, d.summary
    await price(e, clock, 985, symbol)


async def test_daily_loss_limit_blocks_new_entries() -> None:
    clock = Clock()
    e = make_engine(
        clock,
        risk={
            "max_position_value": 1_000_000,
            "max_exposure": 1_000_000,
            "margin_multiplier": 5,
            "max_consecutive_losses": 10,
            "max_trades_per_day": 10,
        },
    )
    # each loss: 1% sized, stop gapped by 5 -> about 1.5% of capital
    await lose(e, clock)
    await lose(e, clock)
    assert e.risk.state.realized_pnl_today < -e.risk.daily_loss_limit_amount
    d = await open_trade(e, clock)
    assert R.DAILY_LOSS_LIMIT in d.rejection_reasons
    assert any(ev.event_type == "ENTRIES_BLOCKED" for ev in e.drain()[2])


async def test_daily_loss_limit_shrinks_the_last_trade_to_fit() -> None:
    clock = Clock()
    e = make_engine(
        clock,
        risk={
            "max_position_value": 1_000_000,
            "max_exposure": 1_000_000,
            "margin_multiplier": 5,
            "daily_loss_limit_percent": 2.5,
            "max_consecutive_losses": 10,
        },
    )
    await lose(e, clock)  # about -1.5%
    remaining = e.risk.daily_loss_remaining
    d = await open_trade(e, clock)
    assert d.approved and d.size is not None
    assert d.size.actual_risk <= remaining
    assert "capped by remaining daily loss" in d.size.constraints


async def test_consecutive_losses_block_new_entries() -> None:
    clock = Clock()
    e = make_engine(clock, risk={"max_consecutive_losses": 2, "daily_loss_limit_percent": 20})
    await lose(e, clock)
    await lose(e, clock)
    d = await open_trade(e, clock)
    assert d.rejection_reasons == [R.MAX_CONSECUTIVE_LOSSES]


async def test_max_trades_per_day() -> None:
    clock = Clock()
    e = make_engine(clock, risk={"max_trades_per_day": 2})
    for _ in range(2):
        await open_trade(e, clock)
        await price(e, clock, 1020)
    d = await open_trade(e, clock)
    assert d.rejection_reasons == [R.MAX_TRADES_REACHED]


# --- exits -------------------------------------------------------------------------------------


async def test_manual_exit_closes_at_market() -> None:
    clock = Clock()
    e = make_engine(clock)
    await open_trade(e, clock)
    await price(e, clock, 1005)
    await e.exit_position()
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.MANUAL and t.exit_price == 1005
    assert all(not o.status.is_working for o in e.broker.orders())
    with pytest.raises(EngineError):
        await e.exit_position()


async def test_manual_exit_while_entry_pending_cancels_it() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"execution_delay_seconds": 5})
    await open_trade(e, clock)
    await e.exit_position()
    t = e.closed[-1]
    assert t.status == TradeStatus.CANCELLED and e.state == S.IDLE


async def test_exit_with_delay_waits_in_exit_state() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"execution_delay_seconds": 1})
    await open_trade(e, clock)
    await price(e, clock, 1001)  # entry fills
    assert e.state == S.POSITION_ACTIVE
    await e.exit_position()
    assert e.state == S.EXIT and e.active is not None
    assert e.active.status == TradeStatus.EXITING
    await price(e, clock, 1003)
    assert e.state == S.IDLE and e.closed[-1].exit_price == 1003


async def test_square_off_time_exit() -> None:
    clock = Clock(datetime(2026, 1, 6, 9, 40, tzinfo=UTC))  # 15:10 IST, entries closed
    e = make_engine(
        clock, risk={"session": {"last_entry_time": "15:12", "square_off_time": "15:15"}}
    )
    d = await open_trade(e, clock)
    assert d.approved
    clock.now = datetime(2026, 1, 6, 9, 45, tzinfo=UTC)  # 15:15 IST
    await price(e, clock, 1004)
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.TIME_EXIT and t.exit_price == 1004


async def test_market_closed_and_stale_data_reject_through_the_engine() -> None:
    sunday = Clock(datetime(2026, 1, 11, 5, 0, tzinfo=UTC))
    e = make_engine(sunday)
    d = await open_trade(e, sunday)
    assert d.rejection_reasons == [R.MARKET_CLOSED]
    assert e.state == S.IDLE and e.active is None

    clock = Clock()
    e2 = make_engine(clock)
    await e2.on_price("RELIANCE", 1000, clock())
    d2 = await e2.execute(intent(), conditions(clock, age=120))
    assert d2.rejection_reasons == [R.DATA_STALE]
    d3 = await e2.execute(intent(), conditions(clock, healthy=False))
    assert d3.rejection_reasons == [R.SYSTEM_UNHEALTHY]
    assert e2.state == S.IDLE and not e2.broker.orders()


async def test_non_paper_mode_never_places_an_order() -> None:
    clock = Clock()
    e = make_engine(clock, engine={"trading_mode": "LIVE"})
    d = await open_trade(e, clock)
    assert d.rejection_reasons == [R.TRADING_MODE_NOT_PAPER]
    assert e.broker.orders() == []


# --- emergency stop -----------------------------------------------------------------------------


async def test_emergency_stop_flattens_cancels_and_blocks() -> None:
    clock = Clock()
    e = make_engine(clock)
    e.set_auto_trading(True)
    assert e.state == S.SCANNING
    await open_trade(e, clock)
    await price(e, clock, 1006)
    await e.emergency_stop("test")
    t = e.closed[-1]
    assert t.exit_reason == ExitReason.EMERGENCY and t.exit_price == 1006
    assert e.emergency_stop_active and not e.auto_trading
    assert e.state == S.IDLE
    assert all(not o.status.is_working for o in e.broker.orders())
    assert all(p.quantity == 0 for p in e.broker.positions())
    events = [ev.event_type for ev in e.drain()[2]]
    assert "EMERGENCY_STOP" in events and "EXIT_STARTED" in events

    d = await open_trade(e, clock)
    assert d.rejection_reasons == [R.EMERGENCY_STOP]
    with pytest.raises(EngineError):
        e.set_auto_trading(True)

    e.reset_emergency_stop()
    assert not e.emergency_stop_active and not e.auto_trading
    assert (await open_trade(e, clock, at=1006)).approved


async def test_emergency_stop_with_pending_entry_cancels_it() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"execution_delay_seconds": 5})
    await open_trade(e, clock)
    await e.emergency_stop()
    assert e.closed[-1].status == TradeStatus.CANCELLED
    assert e.closed[-1].exit_reason == ExitReason.EMERGENCY
    assert not e.broker.working_orders()


async def test_emergency_reset_is_refused_while_the_exit_is_still_working() -> None:
    clock = Clock()
    e = make_engine(clock, broker={"execution_delay_seconds": 1})
    await open_trade(e, clock)
    await price(e, clock, 1001)
    await e.emergency_stop()
    assert e.state == S.EXIT
    with pytest.raises(EngineError):
        e.reset_emergency_stop()
    await price(e, clock, 1000)
    assert e.state == S.IDLE
    e.reset_emergency_stop()


async def test_emergency_stop_when_flat_just_blocks() -> None:
    clock = Clock()
    e = make_engine(clock)
    await e.emergency_stop()
    assert e.emergency_stop_active and e.state == S.IDLE


# --- outbox ---------------------------------------------------------------------------------------


async def test_drain_reports_orders_trades_and_events_once() -> None:
    clock = Clock()
    e = make_engine(clock)
    await open_trade(e, clock)
    await price(e, clock, 1020)
    orders, trades, events = e.drain()
    purposes = {e.purpose_of(o.id) for o in orders}
    assert purposes == {OrderPurpose.ENTRY, OrderPurpose.STOP_LOSS, OrderPurpose.TARGET}
    assert all(e.trade_of(o.id) == trades[0].id for o in orders)
    assert trades[0].status == TradeStatus.CLOSED
    types = [ev.event_type for ev in events]
    assert types[:2] == ["ORDER_PLACED", "POSITION_OPENED"] and types[-1] == "TRADE_CLOSED"
    assert e.drain() == ([], [], [])


async def test_risk_rejection_emits_an_event_and_keeps_the_resting_state() -> None:
    clock = Clock()
    e = make_engine(clock)
    e.set_auto_trading(True)
    await e.on_price("RELIANCE", 1000, clock())
    d = await e.execute(intent(score=40), conditions(clock))
    assert not d.approved and e.state == S.SCANNING
    assert e.last_decision is d
    events = e.drain()[2]
    rejected = [ev for ev in events if ev.event_type == "RISK_REJECTED"]
    assert rejected and rejected[0].metadata["reasons"] == ["SIGNAL_NOT_EXECUTABLE"]
    assert "SIGNAL_FOUND" in states(e) and "RISK_CHECK" in states(e)


async def test_many_trades_keep_the_books_consistent() -> None:
    clock = Clock()
    e = make_engine(
        clock,
        broker={"slippage_bps": 5},
        risk={
            "max_trades_per_day": 20,
            "max_consecutive_losses": 20,
            "daily_loss_limit_percent": 20,
        },
    )
    exits = [1020, 985, 1020, 1020, 985, 1020]
    for i, px in enumerate(exits):
        d = await open_trade(e, clock, at=1000 + i * 0.05)
        assert d.approved, d.summary
        await price(e, clock, px + i * 0.05)
        clock.advance(timedelta(minutes=1).total_seconds())
    net = sum(t.net_pnl for t in e.closed)
    bal = e.broker.balance()
    assert bal.cash == pytest.approx(100_000 + net, abs=0.05)
    assert bal.charges_paid == pytest.approx(sum(t.charges for t in e.closed), abs=0.05)
    assert e.risk.state.realized_pnl_today == pytest.approx(net, abs=0.05)
    assert e.risk.state.trades_today == len(exits)
    assert e.risk.state.wins_today == 4 and e.risk.state.losses_today == 2
