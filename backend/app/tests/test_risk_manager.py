"""RiskManager: position sizing, every rejection reason, daily counters
and session rules."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.risk.config import MarketSessionConfig, RiskLimits, SessionMode
from app.risk.manager import RiskManager
from app.risk.position_sizing import calculate_position_size, floor_to_lot, round_to_tick
from app.risk.schemas import RejectionReason as R
from app.risk.schemas import TradeSide
from app.tests.paper_helpers import T0, Clock, account, conditions, intent, limits

# --- position sizing --------------------------------------------------------------------


def size(**kw):
    defaults = dict(
        capital=100_000.0,
        available_capital=100_000.0,
        entry=1000.0,
        stop_loss=990.0,
        side=TradeSide.LONG,
        limits=limits(max_position_value=1_000_000, max_exposure=1_000_000),
    )
    return calculate_position_size(**{**defaults, **kw})


def test_quantity_is_risk_amount_over_stop_distance() -> None:
    s = size()
    assert s.risk_amount == 1000.0  # 100,000 x 1%
    assert s.risk_per_unit == 10.0
    assert s.raw_quantity == 100.0
    assert s.quantity == 100
    assert s.actual_risk == 1000.0
    assert s.position_value == 100_000.0
    assert s.constraints == []


def test_short_uses_the_absolute_stop_distance() -> None:
    s = size(stop_loss=1010.0, side=TradeSide.SHORT)
    assert s.quantity == 100 and s.risk_per_unit == 10.0


def test_quantity_rounds_down_never_up() -> None:
    s = size(stop_loss=993.0, available_capital=500_000)  # 1000 / 7 = 142.86
    assert s.quantity == 142
    assert s.actual_risk <= s.risk_amount
    assert "rounded down to lot size 1" in s.constraints


def test_lot_size_constraint() -> None:
    lim = limits(lot_size=25, max_position_value=1_000_000, max_exposure=1_000_000)
    s = size(stop_loss=993.0, limits=lim, available_capital=500_000)
    assert s.quantity == 125  # 142.86 -> 5 lots of 25
    assert s.quantity % 25 == 0


def test_max_position_value_caps_quantity() -> None:
    s = size(limits=limits())  # default max ₹50,000
    assert s.quantity == 50
    assert s.position_value == 50_000
    assert "capped by max position value" in s.constraints
    assert s.actual_risk == 500.0  # less than the 1% budget


def test_available_capital_and_margin_cap_quantity() -> None:
    lim = limits(max_position_value=1_000_000, max_exposure=1_000_000)
    s = size(available_capital=20_000, limits=lim)
    assert s.quantity == 20 and "capped by available capital" in s.constraints
    lev = limits(max_position_value=1_000_000, max_exposure=1_000_000, margin_multiplier=5)
    assert size(available_capital=20_000, limits=lev).quantity == 100


def test_max_order_quantity_caps_quantity() -> None:
    lim = limits(max_position_value=1_000_000, max_exposure=1_000_000, max_order_quantity=40)
    s = size(limits=lim)
    assert s.quantity == 40 and "capped by max order quantity" in s.constraints


def test_prices_round_to_tick_with_stop_rounded_away_from_entry() -> None:
    long = size(entry=1000.02, stop_loss=990.03)
    assert long.entry == 1000.0 and long.stop_loss == 990.0  # long stop rounds down
    short = size(entry=1000.02, stop_loss=1009.97, side=TradeSide.SHORT)
    assert short.stop_loss == 1010.0  # short stop rounds up
    assert round_to_tick(102.35, 0.05) == 102.35
    assert round_to_tick(102.351, 0.05, "down") == 102.35
    assert round_to_tick(102.351, 0.05, "up") == 102.4
    assert floor_to_lot(74.9, 25) == 50 and floor_to_lot(-3, 1) == 0


def test_zero_stop_distance_gives_zero_quantity() -> None:
    s = size(stop_loss=1000.0)
    assert s.quantity == 0 and s.constraints == ["invalid stop distance"]


# --- evaluate: approval --------------------------------------------------------------


def manager(**lim) -> tuple[RiskManager, Clock]:
    clock = Clock()
    return RiskManager(limits(**lim), capital=100_000, now=clock()), clock


def test_clean_trade_is_approved_with_every_check_passing() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock), account())
    assert d.approved, d.summary
    assert d.rejection_reasons == []
    assert all(c.passed for c in d.checks)
    names = {c.name for c in d.checks}
    for expected in (
        "trading_mode",
        "emergency_stop",
        "system_health",
        "market_hours",
        "data_freshness",
        "one_active_trade",
        "duplicate_order",
        "daily_loss_limit",
        "max_trades",
        "consecutive_losses",
        "signal_score",
        "stop_validity",
        "target_validity",
        "entry_deviation",
        "quantity_validity",
        "sufficient_capital",
        "max_exposure",
    ):
        assert expected in names
    assert d.size is not None and d.size.quantity == 50
    assert d.summary.startswith("Approved LONG 50 RELIANCE")


def reasons(d) -> set:
    return set(d.rejection_reasons)


# --- evaluate: each rejection reason ------------------------------------------------------


def test_reject_trading_mode_other_than_paper() -> None:
    rm, clock = manager()
    c = conditions(clock).model_copy(update={"trading_mode": "LIVE"})
    assert reasons(rm.evaluate(intent(), c, account())) == {R.TRADING_MODE_NOT_PAPER}


def test_reject_emergency_stop() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock), account(emergency_stop=True))
    assert reasons(d) == {R.EMERGENCY_STOP}


def test_reject_system_unhealthy() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock, healthy=False), account())
    assert reasons(d) == {R.SYSTEM_UNHEALTHY}
    assert "not running" in next(c.detail for c in d.checks if c.name == "system_health")


@pytest.mark.parametrize(
    "when, why",
    [
        (datetime(2026, 1, 10, 5, 0, tzinfo=UTC), "weekend"),  # Saturday
        (datetime(2026, 1, 6, 3, 0, tzinfo=UTC), "outside"),  # 08:30 IST
        (datetime(2026, 1, 6, 10, 30, tzinfo=UTC), "outside"),  # 16:00 IST
        (datetime(2026, 1, 6, 9, 35, tzinfo=UTC), "no new entries"),  # 15:05 IST
    ],
)
def test_reject_market_closed(when: datetime, why: str) -> None:
    rm = RiskManager(limits(), capital=100_000, now=when)
    clock = Clock(when)
    d = rm.evaluate(intent(), conditions(clock), account())
    assert reasons(d) == {R.MARKET_CLOSED}
    assert why in next(c.detail for c in d.checks if c.name == "market_hours")


def test_reject_market_closed_on_configured_holiday() -> None:
    session = MarketSessionConfig(holidays=[date(2026, 1, 6)])
    rm, clock = manager(session=session)
    d = rm.evaluate(intent(), conditions(clock), account())
    assert reasons(d) == {R.MARKET_CLOSED}


def test_simulated_session_ignores_exchange_hours() -> None:
    sat = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)
    rm = RiskManager(
        limits(session=MarketSessionConfig(mode=SessionMode.SIMULATED_24H)),
        capital=100_000,
        now=sat,
    )
    assert rm.evaluate(intent(), conditions(Clock(sat)), account()).approved
    assert rm.square_off_due(sat) is False


def test_reject_stale_price() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock, age=45), account())
    assert reasons(d) == {R.DATA_STALE}
    no_price = rm.evaluate(intent(), conditions(clock, price=None), account())
    assert R.DATA_STALE in reasons(no_price)


def test_reject_stale_or_expired_signal() -> None:
    rm, clock = manager()
    old = intent(signal_last_seen_at=clock() - timedelta(minutes=5))
    assert reasons(rm.evaluate(old, conditions(clock), account())) == {R.DATA_STALE}
    expired = intent(signal_active=False)
    assert reasons(rm.evaluate(expired, conditions(clock), account())) == {R.DATA_STALE}
    fresh = intent(signal_last_seen_at=clock() - timedelta(seconds=10))
    assert rm.evaluate(fresh, conditions(clock), account()).approved


def test_reject_when_a_trade_is_active() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock), account(active_trade="LONG TCS"))
    assert reasons(d) == {R.ACTIVE_POSITION_EXISTS}


def test_reject_duplicate_signal_and_open_order() -> None:
    rm, clock = manager()
    i = intent()
    rm.record_signal_attempt(i.signal_id)
    assert reasons(rm.evaluate(i, conditions(clock), account())) == {R.DUPLICATE_ORDER}
    d = rm.evaluate(intent(), conditions(clock), account(open_order_symbols={"RELIANCE"}))
    assert reasons(d) == {R.DUPLICATE_ORDER}


def test_reject_daily_loss_limit_reached() -> None:
    rm, clock = manager()
    rm.record_trade_result(-3_000.0)  # 3% of 100,000
    d = rm.evaluate(intent(), conditions(clock), account())
    assert R.DAILY_LOSS_LIMIT in reasons(d)


def test_trade_risk_is_reduced_to_fit_the_remaining_daily_loss() -> None:
    rm, clock = manager(max_position_value=1_000_000, max_exposure=1_000_000)
    rm.record_trade_result(-2_400.0)  # ₹600 of the ₹3,000 limit left
    d = rm.evaluate(intent(), conditions(clock), account())
    assert d.approved
    assert d.size is not None and d.size.quantity == 60  # not 100
    assert d.size.actual_risk <= 600
    assert "capped by remaining daily loss" in d.size.constraints


def test_reject_when_remaining_daily_loss_cannot_fit_one_share() -> None:
    rm, clock = manager()
    rm.record_trade_result(-2_995.0)  # ₹5 left, one share risks ₹10
    d = rm.evaluate(intent(), conditions(clock), account())
    assert R.INVALID_QUANTITY in reasons(d)


def test_reject_max_trades() -> None:
    rm, clock = manager(max_trades_per_day=2)
    rm.record_entry(None)
    rm.record_entry(None)
    assert reasons(rm.evaluate(intent(), conditions(clock), account())) == {R.MAX_TRADES_REACHED}


def test_reject_consecutive_losses_and_a_win_resets_the_run() -> None:
    rm, clock = manager(max_consecutive_losses=2, daily_loss_limit_percent=10)
    rm.record_trade_result(-100)
    rm.record_trade_result(0.0)  # break-even resets
    rm.record_trade_result(-100)
    assert rm.evaluate(intent(), conditions(clock), account()).approved
    rm.record_trade_result(-100)
    d = rm.evaluate(intent(), conditions(clock), account())
    assert reasons(d) == {R.MAX_CONSECUTIVE_LOSSES}
    assert rm.state.losses_today == 3 and rm.state.wins_today == 1


def test_reject_signal_below_executable_threshold() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(score=84.9), conditions(clock), account())
    assert reasons(d) == {R.SIGNAL_NOT_EXECUTABLE}


def test_reject_short_when_disabled() -> None:
    rm, clock = manager(allow_short=False)
    d = rm.evaluate(intent(side=TradeSide.SHORT), conditions(clock), account())
    assert reasons(d) == {R.SHORT_NOT_ALLOWED}


@pytest.mark.parametrize(
    "stop, side",
    [
        (1005.0, TradeSide.LONG),  # long stop above entry
        (1000.0, TradeSide.LONG),  # zero distance
        (995.0, TradeSide.SHORT),  # short stop below entry
        (-1.0, TradeSide.LONG),
    ],
)
def test_reject_invalid_stop(stop: float, side: TradeSide) -> None:
    rm, clock = manager()
    target = 1020.0 if side == TradeSide.LONG else 980.0
    d = rm.evaluate(intent(side=side, stop=stop, target=target), conditions(clock), account())
    assert R.INVALID_STOP in reasons(d)
    assert R.INVALID_QUANTITY in reasons(d)  # no size without a valid stop
    assert d.size is None


def test_reject_stop_already_crossed_by_live_price() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock, price=989.0), account())
    assert R.INVALID_STOP in reasons(d)


def test_reject_invalid_target() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(target=995.0), conditions(clock), account())
    assert reasons(d) == {R.INVALID_TARGET}


def test_reject_price_moved_away_from_entry() -> None:
    rm, clock = manager()
    # planned risk 10, allowed adverse 2.5
    assert rm.evaluate(intent(), conditions(clock, price=1002.5), account()).approved
    d = rm.evaluate(intent(), conditions(clock, price=1003.0), account())
    assert reasons(d) == {R.PRICE_MOVED_AWAY}
    short = intent(side=TradeSide.SHORT)
    assert reasons(rm.evaluate(short, conditions(clock, price=997.0), account())) == {
        R.PRICE_MOVED_AWAY
    }


def test_reject_insufficient_capital() -> None:
    rm, clock = manager()
    d = rm.evaluate(intent(), conditions(clock), account(available=500.0))
    # sizing caps quantity to what is affordable; nothing is affordable here
    assert R.INVALID_QUANTITY in reasons(d) and R.INSUFFICIENT_CAPITAL in reasons(d)
    tight = rm.evaluate(
        intent(), conditions(clock), account(available=50_000.0, estimated_charges=40.0)
    )
    assert R.INSUFFICIENT_CAPITAL in reasons(tight)  # charges push it over


def test_reject_max_exposure() -> None:
    rm, clock = manager(max_exposure=60_000)
    d = rm.evaluate(intent(), conditions(clock), account(open_exposure=20_000))
    assert reasons(d) == {R.MAX_EXPOSURE}


def test_every_failure_is_reported_not_just_the_first() -> None:
    rm, clock = manager()
    d = rm.evaluate(
        intent(score=50),
        conditions(clock, age=300, healthy=False),
        account(emergency_stop=True, active_trade="SHORT INFY"),
    )
    assert reasons(d) >= {
        R.EMERGENCY_STOP,
        R.SYSTEM_UNHEALTHY,
        R.DATA_STALE,
        R.ACTIVE_POSITION_EXISTS,
        R.SIGNAL_NOT_EXECUTABLE,
    }
    assert not d.approved
    assert d.summary.startswith("Rejected: ")


# --- daily state and session --------------------------------------------------------------


def test_counters_reset_on_a_new_ist_day() -> None:
    rm, clock = manager()
    rm.record_entry(None)
    rm.record_trade_result(-500)
    # 23:00 IST the same day: still today
    assert rm.ensure_day(datetime(2026, 1, 6, 17, 30, tzinfo=UTC), 99_500) is False
    assert rm.state.trades_today == 1
    # 00:30 IST next day (19:00 UTC on the 6th)
    assert rm.ensure_day(datetime(2026, 1, 6, 19, 0, tzinfo=UTC), 99_500) is True
    assert rm.state.trades_today == 0 and rm.state.realized_pnl_today == 0
    assert rm.state.day_start_capital == 99_500


def test_square_off_due_at_configured_time() -> None:
    rm, _ = manager()
    assert rm.square_off_due(datetime(2026, 1, 6, 9, 44, tzinfo=UTC)) is False  # 15:14 IST
    assert rm.square_off_due(datetime(2026, 1, 6, 9, 45, tzinfo=UTC)) is True  # 15:15 IST


def test_session_config_must_be_ordered() -> None:
    with pytest.raises(ValueError):
        MarketSessionConfig(last_entry_time=time(15, 20), square_off_time=time(15, 15))


def test_snapshot_reports_remaining_budget_and_blocks() -> None:
    rm, clock = manager(max_trades_per_day=1)
    rm.record_entry(None)
    rm.record_trade_result(-1_000)
    snap = rm.snapshot(account(capital=99_000), clock())
    assert snap.daily_loss_limit_amount == 3_000
    assert snap.daily_loss_remaining == 2_000
    assert snap.trades_remaining == 0
    assert snap.entries_blocked and R.MAX_TRADES_REACHED in snap.blocked_reasons
    assert snap.risk_per_trade_amount == 990.0


def test_limits_reject_unsafe_values() -> None:
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade_percent=0)
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade_percent=10)  # above the 5% ceiling
    assert T0.tzinfo is UTC
