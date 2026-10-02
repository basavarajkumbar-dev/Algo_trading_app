"""Performance analytics (pure) and component health classification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.performance import compute_performance
from app.risk.schemas import TradeSide
from app.services.component_health import (
    HealthLevel,
    classify_market_data,
    classify_service,
    worst,
)
from app.trading.schemas import ExitReason, PaperTrade, TradeStatus

T0 = datetime(2026, 1, 6, 5, 0, tzinfo=UTC)  # 10:30 IST


def trade(
    net: float,
    minutes: int,
    *,
    strategy: str = "TREND_CONTINUATION",
    reason: ExitReason = ExitReason.TARGET,
    status: TradeStatus = TradeStatus.CLOSED,
    r: float | None = None,
    filled: int = 10,
) -> PaperTrade:
    closed_at = T0 + timedelta(minutes=minutes)
    return PaperTrade(
        id=uuid.uuid4(),
        symbol="TCS",
        side=TradeSide.LONG,
        strategy=strategy,
        status=status,
        planned_entry=100,
        stop_loss=99,
        target=102,
        quantity=10,
        filled_quantity=filled,
        initial_risk=10,
        net_pnl=net,
        charges=1.0,
        r_multiple=r if r is not None else net / 10,
        exit_reason=reason,
        created_at=closed_at - timedelta(minutes=5),
        closed_at=closed_at if status == TradeStatus.CLOSED else None,
    )


def test_empty_history() -> None:
    report = compute_performance([], starting_capital=100_000)
    s = report.summary
    assert s.closed_trades == 0 and s.win_rate is None and s.profit_factor is None
    assert s.expectancy is None and s.max_drawdown == 0 and s.ending_equity == 100_000
    assert report.equity_curve == [] and report.daily == []


def test_summary_figures() -> None:
    trades = [trade(100, 1), trade(-50, 2), trade(200, 3), trade(-150, 4), trade(0, 5)]
    s = compute_performance(trades, starting_capital=100_000).summary
    assert s.closed_trades == 5 and s.wins == 2 and s.losses == 2 and s.breakeven == 1
    assert s.win_rate == 40.0
    assert s.gross_profit == 300 and s.gross_loss == -200
    assert s.profit_factor == 1.5
    assert s.net_pnl == 100 and s.expectancy == 20
    assert s.average_win == 150 and s.average_loss == -100
    assert s.best_trade == 200 and s.worst_trade == -150
    assert s.charges == 5
    assert s.ending_equity == 100_100 and s.return_percent == 0.1


def test_profit_factor_is_undefined_without_losses() -> None:
    s = compute_performance([trade(10, 1), trade(20, 2)], starting_capital=1000).summary
    assert s.profit_factor is None and s.win_rate == 100


def test_equity_curve_and_drawdown_follow_closing_order() -> None:
    # given out of order on purpose
    trades = [trade(-300, 3), trade(500, 1), trade(-100, 2), trade(400, 4)]
    report = compute_performance(trades, starting_capital=10_000)
    assert [p.net_pnl for p in report.equity_curve] == [500, -100, -300, 400]
    assert [p.equity for p in report.equity_curve] == [10_500, 10_400, 10_100, 10_500]
    assert [p.drawdown for p in report.equity_curve] == [0, -100, -400, 0]
    assert report.summary.max_drawdown == -400
    assert report.summary.max_drawdown_percent == pytest.approx(-400 / 10_500 * 100, abs=1e-3)


def test_drawdown_from_the_starting_capital() -> None:
    report = compute_performance([trade(-200, 1)], starting_capital=10_000)
    assert report.equity_curve[0].drawdown == -200
    assert report.summary.max_drawdown_percent == -2.0


def test_streaks() -> None:
    nets = [10, 10, 10, -5, -5, 0, -5, 10]
    s = compute_performance(
        [trade(n, i) for i, n in enumerate(nets)], starting_capital=1000
    ).summary
    assert s.longest_win_streak == 3 and s.longest_loss_streak == 2


def test_only_closed_filled_trades_count() -> None:
    trades = [
        trade(100, 1),
        trade(0, 2, status=TradeStatus.CANCELLED, filled=0),
        trade(0, 3, status=TradeStatus.INTERRUPTED),
        trade(0, 4, status=TradeStatus.OPEN),
    ]
    report = compute_performance(trades, starting_capital=1000)
    assert report.summary.closed_trades == 1
    assert report.other_trades == {"CANCELLED": 1, "INTERRUPTED": 1, "OPEN": 1}


def test_groups_by_strategy_exit_reason_and_ist_day() -> None:
    trades = [
        trade(100, 1, strategy="BREAKOUT_MOMENTUM", reason=ExitReason.TARGET, r=2),
        trade(-50, 2, strategy="BREAKOUT_MOMENTUM", reason=ExitReason.STOP_LOSS, r=-1),
        trade(30, 3, reason=ExitReason.MANUAL, r=0.5),
        # 23:00 UTC = 04:30 IST next day
        trade(20, 18 * 60, reason=ExitReason.TARGET, r=0.4),
    ]
    report = compute_performance(trades, starting_capital=1000)
    bo = next(g for g in report.by_strategy if g.key == "BREAKOUT_MOMENTUM")
    assert (bo.trades, bo.wins, bo.losses, bo.net_pnl, bo.profit_factor) == (2, 1, 1, 50, 2.0)
    assert bo.average_r == 0.5 and bo.win_rate == 50
    assert {g.key: g.trades for g in report.by_exit_reason} == {
        "TARGET": 2,
        "STOP_LOSS": 1,
        "MANUAL": 1,
    }
    assert [(d.date.isoformat(), d.trades, d.net_pnl) for d in report.daily] == [
        ("2026-01-06", 3, 80),
        ("2026-01-07", 1, 20),
    ]
    assert "not evidence of profitability" in report.note


# --- component health classification --------------------------------------------------------


def test_market_data_classification() -> None:
    ok = classify_market_data(
        running=True, provider="CONNECTED", tick_age_seconds=1.0, demo_mode=True
    )
    assert ok[0] == HealthLevel.HEALTHY and "Simulated" in ok[1]
    assert (
        classify_market_data(running=False, provider=None, tick_age_seconds=None, demo_mode=True)[0]
        == HealthLevel.CRITICAL
    )
    assert (
        classify_market_data(
            running=True, provider="DISCONNECTED", tick_age_seconds=1, demo_mode=True
        )[0]
        == HealthLevel.CRITICAL
    )
    assert (
        classify_market_data(
            running=True, provider="CONNECTED", tick_age_seconds=20, demo_mode=True
        )[0]
        == HealthLevel.WARNING
    )
    assert (
        classify_market_data(
            running=True, provider="CONNECTED", tick_age_seconds=90, demo_mode=True
        )[0]
        == HealthLevel.CRITICAL
    )
    honest = classify_market_data(
        running=True, provider="CONNECTED", tick_age_seconds=1, demo_mode=False
    )
    assert honest[0] == HealthLevel.WARNING and "DEMO_MODE=false" in honest[1]


def test_service_classification_and_worst() -> None:
    def svc(**kw):
        base = dict(
            name="X", running=True, last_refresh_age=5.0, expected_seconds=15, last_error=None
        )
        return classify_service(**{**base, **kw})[0]

    assert svc() == HealthLevel.HEALTHY
    assert svc(running=False) == HealthLevel.CRITICAL
    assert svc(last_error="boom") == HealthLevel.WARNING
    assert svc(last_refresh_age=None) == HealthLevel.WARNING
    assert svc(last_refresh_age=46) == HealthLevel.WARNING
    assert worst([HealthLevel.HEALTHY, HealthLevel.CRITICAL, HealthLevel.WARNING]) == (
        HealthLevel.CRITICAL
    )
    assert worst([]) == HealthLevel.HEALTHY
