"""STOP entries (audit finding F1): a trigger entry never fills before price
trades through its trigger, expires when it is not reached, and realised R
is measured from the fill (F2). Also: no decisions at the last entry time
(F11)."""

from __future__ import annotations

import pytest

from app.backtesting.calendar import bar_starts
from app.risk.config import RiskLimits
from app.risk.manager import AccountView, RiskManager
from app.risk.schemas import MarketConditions, TradeIntent, TradeSide
from app.tests.test_backtesting import DAY, _day_bars, _record, _sim
from app.trading.schemas import ExitReason, TradeStatus


def _stop_record(decision_bar: int) -> tuple[str, dict]:
    # a long trigger at 100.5 (above the signal candle), stop 99.5, target 102.5
    return _record("AAA", decision_bar, entry=100.5, stop=99.5, target=102.5, et="STOP", evb=2)


def test_stop_entry_waits_for_the_trigger_and_fills_there() -> None:
    # decided at the close of bar 10 (price 100.0, below the 100.5 trigger);
    # bar 11 stays below it, bar 12 trades up through it
    path = {11: (100.0, 100.3, 99.9, 100.2), 12: (100.2, 100.9, 100.1, 100.8)}
    t, rec = _stop_record(10)
    r = _sim({"AAA": _day_bars("AAA", DAY, path)}, {t: [rec]})
    trade = next(x for x in r.trades if x.filled_quantity > 0)
    assert trade.opened_at is not None
    assert trade.opened_at >= bar_starts(DAY)[12]  # not on bar 11
    # filled at the trigger plus slippage (5 bps), never at bar 11's prices
    assert trade.entry_price == pytest.approx(100.5 * 1.0005, abs=0.06)


def test_stop_entry_expires_when_the_trigger_is_not_reached() -> None:
    t, rec = _stop_record(10)  # flat bars at 100.0: never trades at 100.5
    r = _sim({"AAA": _day_bars("AAA", DAY, {})}, {t: [rec]})
    assert not [x for x in r.trades if x.filled_quantity > 0]
    expired = [x for x in r.trades if x.status == TradeStatus.CANCELLED]
    assert expired and expired[0].exit_reason == ExitReason.ENTRY_EXPIRED


def test_trigger_reached_after_expiry_does_not_fill() -> None:
    # valid for 2 candles (bars 11 and 12); the move through 100.5 comes on bar 14
    path = {14: (100.0, 101.0, 99.9, 100.9)}
    t, rec = _stop_record(10)
    r = _sim({"AAA": _day_bars("AAA", DAY, path)}, {t: [rec]})
    assert not [x for x in r.trades if x.filled_quantity > 0]


def test_market_entry_is_unchanged() -> None:
    t, rec = _record("AAA", 10)  # no "et": MARKET at the next open
    r = _sim({"AAA": _day_bars("AAA", DAY, {})}, {t: [rec]})
    trade = next(x for x in r.trades if x.filled_quantity > 0)
    assert trade.opened_at is not None and trade.opened_at < bar_starts(DAY)[11].replace(second=2)


def test_r_multiple_uses_the_fill() -> None:
    # trigger fills at ~100.55, then the 99.5 stop is hit: about -1R of the fill risk
    path = {
        11: (100.2, 100.9, 100.1, 100.8),
        13: (100.6, 100.6, 99.3, 99.4),
    }
    t, rec = _stop_record(10)
    r = _sim({"AAA": _day_bars("AAA", DAY, path)}, {t: [rec]})
    trade = next(x for x in r.trades if x.filled_quantity > 0)
    assert trade.exit_reason == ExitReason.STOP_LOSS
    assert trade.r_multiple is not None and -1.3 < trade.r_multiple <= -1.0


def _risk(intent: TradeIntent, last: float):  # noqa: ANN202
    limits = RiskLimits(max_position_value=1_000_000, max_exposure=1_000_000)
    rm = RiskManager(limits, capital=100_000.0, now=bar_starts(DAY)[10])
    conditions = MarketConditions(
        now=bar_starts(DAY)[10],
        last_price=last,
        price_timestamp=bar_starts(DAY)[10],
        system_healthy=True,
        health_detail="test",
    )
    account = AccountView(capital=100_000.0, available_capital=100_000.0)
    return rm.evaluate(intent, conditions, account)


def test_stop_entry_is_sized_at_the_trigger() -> None:
    intent = TradeIntent(
        symbol="AAA",
        side=TradeSide.LONG,
        entry=101.0,
        stop_loss=100.0,
        target=103.0,
        score=90.0,
        entry_type="STOP",
    )
    decision = _risk(intent, last=100.4)  # below the trigger: sized at 101.0 - 100.0
    assert decision.approved, decision.summary
    assert decision.size is not None and decision.size.risk_per_unit == pytest.approx(1.0)


def test_stop_entry_far_beyond_the_trigger_is_refused() -> None:
    intent = TradeIntent(
        symbol="AAA",
        side=TradeSide.LONG,
        entry=101.0,
        stop_loss=100.0,
        target=104.0,
        score=90.0,
        entry_type="STOP",
    )
    decision = _risk(intent, last=101.6)  # 0.6R past the trigger, max 0.25R
    assert not decision.approved
    assert "PRICE_MOVED_AWAY" in [r.value for r in decision.rejection_reasons]


def test_no_decision_at_the_last_entry_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date, datetime, time

    import app.backtesting.replay as replay
    from app.backtesting.calendar import IST
    from app.backtesting.data import SyntheticHistorySource
    from app.backtesting.parameters import StrategyChoice

    seen: list[datetime] = []
    real = replay.build_context

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.append(kwargs["now"])
        return real(*args, **kwargs)

    monkeypatch.setattr(replay, "build_context", spy)
    src = SyntheticHistorySource(seed=5)
    job = replay.ReplayJob(
        symbol="TCS",
        bars=src.bars("TCS", date(2026, 7, 20), DAY),
        benchmark_bars=src.bars("NIFTY50", date(2026, 7, 20), DAY),
        decision_start=DAY,
        decision_end=DAY,
        entry="5m",
        last_entry_time=time(15, 0),
        strategy=StrategyChoice.ALL,
        strategy_param_sets=[{}],
    )
    replay.replay_symbol(job)
    local = [t.astimezone(IST).time() for t in seen]
    assert local and max(local) == time(14, 55)  # 15:00 is never analysed
