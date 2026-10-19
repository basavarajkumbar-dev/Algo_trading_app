"""Backtesting: data, look-ahead / leakage / survivorship guards, execution
replay, metrics, robustness, parameter handling and the three modes."""

from __future__ import annotations

import csv
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.backtesting.calendar import IST, bar_starts, trading_days
from app.backtesting.data import CsvHistorySource, SyntheticHistorySource, Universe
from app.backtesting.metrics import (
    compute_metrics,
    robustness,
    selection_objective,
    sharpe_sortino,
)
from app.backtesting.parameters import (
    MAX_GRID_COMBINATIONS,
    ParameterError,
    StrategyChoice,
    TrailingMode,
    engine_config_for,
    expand_grid,
    split_scopes,
    strategy_versions,
    validate_params,
)
from app.backtesting.replay import (
    ClosedBarHistory,
    LookAheadError,
    ReplayJob,
    SessionAggregator,
    replay_symbol,
)
from app.backtesting.runner import _neighbours, _select, _smooth, run_backtest
from app.backtesting.schemas import BacktestRequest
from app.backtesting.simulator import ExecutionParams, simulate
from app.execution.costs import CostModel
from app.execution.paper_broker import PaperBrokerConfig
from app.market_data.schemas import Candle, Timeframe
from app.risk.config import RiskLimits
from app.risk.schemas import TradeSide
from app.trading.schemas import ExitReason, PaperTrade, TradeStatus

DAY = date(2026, 8, 4)  # a Tuesday


# --- calendar & data ---------------------------------------------------------------------------


def test_session_calendar() -> None:
    starts = bar_starts(DAY)
    assert len(starts) == 75
    assert starts[0].astimezone(IST).time() == time(9, 15)
    assert starts[-1].astimezone(IST).time() == time(15, 25)
    days = trading_days(date(2026, 8, 1), date(2026, 8, 9), holidays=[date(2026, 8, 5)])
    assert days == [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 6), date(2026, 8, 7)]


def test_synthetic_history_is_deterministic_and_range_independent() -> None:
    a = SyntheticHistorySource(seed=3)
    b = SyntheticHistorySource(seed=3)
    long = a.bars("TCS", date(2026, 7, 27), date(2026, 8, 7))
    short = b.bars("TCS", date(2026, 8, 3), date(2026, 8, 4))
    assert [c.close for c in short] == [
        c.close
        for c in long
        if date(2026, 8, 3) <= c.timestamp.astimezone(IST).date() <= date(2026, 8, 4)
    ]
    assert len(short) == 150
    other = SyntheticHistorySource(seed=4).bars("TCS", date(2026, 8, 3), date(2026, 8, 4))
    assert [c.close for c in other] != [c.close for c in short]
    for c in long:
        assert c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
    assert SyntheticHistorySource(seed=3, null_model=True).fingerprint() != a.fingerprint()


def test_synthetic_universe_has_no_survivorship_filter() -> None:
    src = SyntheticHistorySource(seed=1)
    u = src.universe()
    assert u.point_in_time
    assert len(u.members(DAY)) == 50  # every generated symbol, whatever it "did"


def test_point_in_time_universe_membership() -> None:
    u = Universe(
        periods={"OLD": [(date(2026, 1, 1), date(2026, 8, 3))], "NEW": [(date(2026, 8, 4), None)]},
        point_in_time=True,
        source="test",
    )
    assert u.members(date(2026, 8, 3)) == {"OLD"}
    assert u.members(DAY) == {"NEW"}
    assert u.ever_members(date(2026, 8, 1), date(2026, 8, 10)) == {"OLD", "NEW"}


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(rows)


def test_csv_source_parses_validates_and_reports_quality(tmp_path: Path) -> None:
    rows: list[list[object]] = []
    for i, ts in enumerate(bar_starts(DAY)):
        local = ts.astimezone(IST).replace(tzinfo=None).isoformat()  # naive = IST
        rows.append([local, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000])
    rows.append(["2026-08-04T08:00:00", 1, 2, 1, 1, 1])  # before the session
    rows.append(["2026-08-04T10:00:00", 100, 90, 95, 96, 1])  # high < low
    rows.append([rows[3][0], 1, 2, 0.5, 1.5, 1])  # duplicate timestamp
    _write_csv(tmp_path / "ABC.csv", rows)
    _write_csv(tmp_path / "NIFTY50.csv", rows[:75])
    src = CsvHistorySource(tmp_path)
    bars = src.bars("ABC", DAY, DAY)
    assert len(bars) == 75
    assert bars[0].timestamp == datetime(2026, 8, 4, 3, 45, tzinfo=UTC)
    q = src.quality("ABC")
    assert (q.out_of_session_bars, q.invalid_bars, q.duplicate_bars) == (1, 1, 1)
    u = src.universe()
    assert not u.point_in_time and src.symbols() == ["ABC"]
    with (tmp_path / "universe.csv").open("w") as fh:
        fh.write("symbol,start_date,end_date\nABC,2026-01-01,2026-08-03\n")
    pit = CsvHistorySource(tmp_path).universe()
    assert pit.point_in_time and pit.members(DAY) == set()


# --- look-ahead guards ---------------------------------------------------------------------------


def _bar(
    symbol: str,
    ts: datetime,
    o: float,
    h: float,
    low: float,
    c: float,
    tf: Timeframe = Timeframe.M5,
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=tf,
        timestamp=ts,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000,
        is_closed=True,
    )


def test_closed_bar_history_refuses_an_unclosed_bar() -> None:
    h = ClosedBarHistory(10)
    ts = bar_starts(DAY)[0]
    h.append(_bar("X", ts, 1, 1, 1, 1))
    assert len(h.window(ts + timedelta(minutes=5))) == 1
    with pytest.raises(LookAheadError):
        h.window(ts + timedelta(minutes=4))
    with pytest.raises(ValueError):
        h.append(_bar("X", ts, 1, 1, 1, 1))  # out of order


def test_session_aggregator_emits_only_complete_session_buckets() -> None:
    agg = SessionAggregator(Timeframe.M60)
    out = []
    for i, ts in enumerate(bar_starts(DAY)):
        c = agg.add(_bar("X", ts, 100 + i, 102 + i, 99 + i, 101 + i))
        if c:
            out.append(c)
    # 09:15-10:15 ... 14:15-15:15; the 15:15-15:30 stub never completes
    assert [c.timestamp.astimezone(IST).time() for c in out] == [time(h, 15) for h in range(9, 15)]
    first = out[0]
    assert (first.open, first.high, first.low, first.close) == (100, 113, 99, 112)
    thirty = SessionAggregator(Timeframe.M30)
    emitted = [c for ts in bar_starts(DAY) if (c := thirty.add(_bar("X", ts, 1, 1, 1, 1)))]
    assert len(emitted) == 12  # 12 full half-hours; the last 15 minutes are dropped


def test_signals_never_depend_on_future_bars() -> None:
    """Perturb every bar after a cut-off: every decision up to the cut-off
    must be byte-for-byte identical."""

    src = SyntheticHistorySource(seed=21)
    start = date(2026, 7, 13)
    end = date(2026, 8, 4)
    bars = src.bars("RELIANCE", start, end)
    bench = src.bars("NIFTY50", start, end)
    cut = datetime.combine(date(2026, 8, 4), time(11, 0), tzinfo=IST).astimezone(UTC)

    def job(b: list[Candle], nb: list[Candle]) -> ReplayJob:
        return ReplayJob(
            symbol="RELIANCE",
            bars=b,
            benchmark_bars=nb,
            decision_start=date(2026, 8, 4),
            decision_end=date(2026, 8, 4),
            entry="5m",
            last_entry_time=time(15, 0),
            strategy=StrategyChoice.ALL,
            strategy_param_sets=[{"min_regime_confidence": 0.0}],
        )

    def perturb(series: list[Candle]) -> list[Candle]:
        out = []
        for c in series:
            if c.timestamp >= cut:
                f = 1.07 if c.timestamp.minute % 10 else 0.93
                c = c.model_copy(
                    update={
                        "open": c.open * f,
                        "high": c.high * 1.1,
                        "low": c.low * 0.9,
                        "close": c.close * f,
                        "volume": c.volume * 5,
                    }
                )
            out.append(c)
        return out

    clean = replay_symbol(job(bars, bench))
    dirty = replay_symbol(job(perturb(bars), perturb(bench)))
    assert clean.decisions > 0 and clean.errors == 0
    before = {k: v for k, v in clean.signals.items() if datetime.fromisoformat(k) <= cut}
    before_dirty = {k: v for k, v in dirty.signals.items() if datetime.fromisoformat(k) <= cut}
    assert before == before_dirty
    # the perturbation is real: the bars after the cut differ
    assert perturb(bars)[-1].close != bars[-1].close


# --- execution replay ------------------------------------------------------------------------------


def _day_bars(
    symbol: str, day: date, path: dict[int, tuple[float, float, float, float]], base: float = 100.0
) -> list[Candle]:
    """75 flat bars at `base`, with given bar indexes overridden (o, h, l, c)."""

    out = []
    for i, ts in enumerate(bar_starts(day)):
        o, h, low, c = path.get(i, (base, base + 0.1, base - 0.1, base))
        out.append(_bar(symbol, ts, o, h, low, c))
    return out


def _record(symbol: str, decision_bar: int, day: date = DAY, **kw: object) -> tuple[str, dict]:
    t = bar_starts(day)[decision_bar] + timedelta(minutes=5)
    rec = {
        "ps": 0,
        "symbol": symbol,
        "strategy": "TREND_CONTINUATION",
        "version": "1.0.0+x",
        "direction": "BUY",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "rr": 2.0,
        "score": 90.0,
        "category": "ELITE",
        "confidence": 80.0,
        "rs": 60.0,
        "tq": 50.0,
        "rv": 1.0,
        "liq": 1e6,
        "regime": "STRONG_BULLISH",
        "regime_conf": 80.0,
        "atr": 0.5,
        "reasons": ["test"],
    }
    rec.update(kw)
    return t.isoformat(), rec


def _sim(bars: dict[str, list[Candle]], signals: dict, **kw):  # noqa: ANN202
    defaults: dict[str, Any] = dict(
        bars=bars,
        signals=signals,
        param_index=0,
        execution=ExecutionParams(min_score=80),
        limits=RiskLimits(max_position_value=1_000_000, max_exposure=1_000_000),
        broker_config=PaperBrokerConfig(
            slippage_bps=0,
            costs=CostModel(
                brokerage_rate=0,
                stt_sell_rate=0,
                exchange_rate=0,
                sebi_per_crore=0,
                stamp_buy_rate=0,
                gst_rate=0,
            ),
        ),
        capital=100_000,
        universe=Universe({s: [(date(2020, 1, 1), None)] for s in bars}, True, "test"),
        start=DAY,
        end=DAY,
    )
    defaults.update(kw)
    return simulate(**defaults)


def test_entry_fills_at_the_next_bar_open_not_the_decision_close() -> None:
    bars = {
        "AAA": _day_bars(
            "AAA",
            DAY,
            {
                10: (100, 100.2, 99.9, 100.0),
                11: (100.4, 100.6, 100.3, 100.5),
                20: (100.5, 102.5, 100.4, 102.2),
            },
        )
    }
    t, rec = _record("AAA", 10)
    r = _sim(bars, {t: [rec]})
    closed = [x for x in r.trades if x.status == TradeStatus.CLOSED]
    assert len(closed) == 1
    trade = closed[0]
    assert trade.entry_price == 100.4  # bar 11 open, not bar 10 close (100.0)
    assert trade.opened_at == bar_starts(DAY)[11] + timedelta(seconds=1)
    assert trade.exit_reason == ExitReason.TARGET and trade.exit_price == 102.0


def test_stop_is_assumed_first_when_a_bar_touches_both() -> None:
    bars = {
        "AAA": _day_bars(
            "AAA", DAY, {11: (100.0, 100.1, 99.9, 100.0), 15: (100.0, 103.0, 98.5, 101.0)}
        )
    }
    t, rec = _record("AAA", 10)
    r = _sim(bars, {t: [rec]})
    trade = next(x for x in r.trades if x.status == TradeStatus.CLOSED)
    # the stop triggers where price crosses it (99.0), not at the bar low (98.5)
    assert trade.exit_reason == ExitReason.STOP_LOSS and trade.exit_price == 99.0


def test_a_gap_through_the_stop_fills_at_the_gap_price() -> None:
    bars = {"AAA": _day_bars("AAA", DAY, {15: (97.0, 97.2, 96.8, 97.1)})}
    t, rec = _record("AAA", 10)
    r = _sim(bars, {t: [rec]})
    trade = next(x for x in r.trades if x.status == TradeStatus.CLOSED)
    assert trade.exit_price == 97.0 and trade.r_multiple is not None and trade.r_multiple < -2.5


def test_r_is_measured_against_the_fill_to_stop_risk() -> None:
    # planned long 100.0, stop 99.0; the entry fills lower at 99.6, then the stop hits.
    # Realised R uses the risk actually taken (fill 99.6 to stop 99.0 = 0.6 a share),
    # so a full stop-out is about -1R plus slippage and charges. (Until the 2026-10
    # audit fix F2 this measured planned risk and reported about -0.6R, understating
    # the loss relative to the risk actually carried.)
    bars = {
        "AAA": _day_bars("AAA", DAY, {11: (99.6, 99.7, 99.5, 99.6), 15: (99.5, 99.5, 98.9, 99.0)})
    }
    t, rec = _record("AAA", 10)
    r = _sim(bars, {t: [rec]})
    trade = next(x for x in r.trades if x.status == TradeStatus.CLOSED)
    assert trade.entry_price == 99.6 and trade.exit_reason == ExitReason.STOP_LOSS
    assert trade.r_multiple is not None and -1.25 < trade.r_multiple <= -1.0


def test_positions_are_squared_off_at_1515() -> None:
    bars = {"AAA": _day_bars("AAA", DAY, {})}
    t, rec = _record("AAA", 60)  # decided 14:20 IST
    r = _sim(bars, {t: [rec]})
    trade = next(x for x in r.trades if x.status == TradeStatus.CLOSED)
    assert trade.exit_reason == ExitReason.TIME_EXIT
    assert trade.closed_at is not None and trade.closed_at.astimezone(IST).time() >= time(15, 15)


def test_no_entries_after_the_last_entry_time() -> None:
    bars = {"AAA": _day_bars("AAA", DAY, {})}
    t, rec = _record("AAA", 70)  # decided 15:15 IST
    r = _sim(bars, {t: [rec]})
    assert not [x for x in r.trades if x.status == TradeStatus.CLOSED]
    assert r.rejections["MARKET_CLOSED"] == 1


def test_breakeven_trailing_stop() -> None:
    path = {12: (100.0, 101.2, 99.95, 101.1), 13: (101.1, 101.15, 99.9, 100.0)}
    bars = {"AAA": _day_bars("AAA", DAY, path)}
    t, rec = _record("AAA", 10)
    r = _sim(
        bars,
        {t: [rec]},
        execution=ExecutionParams(
            min_score=80, trailing_mode=TrailingMode.BREAKEVEN, trailing_activation_r=1.0
        ),
    )
    trade = next(x for x in r.trades if x.status == TradeStatus.CLOSED)
    assert r.trails == 1 and trade.trailing_stop == 100.0
    assert trade.exit_reason == ExitReason.TRAILING_STOP and trade.exit_price == 100.0
    assert trade.stop_loss == 99.0  # the initial stop is kept for R accounting


def test_score_threshold_and_universe_membership_filter_entries() -> None:
    bars = {"AAA": _day_bars("AAA", DAY, {}), "BBB": _day_bars("BBB", DAY, {})}
    t, low = _record("AAA", 10, score=70.0)
    _, gone = _record("BBB", 10)
    universe = Universe(
        {"AAA": [(date(2020, 1, 1), None)], "BBB": [(date(2020, 1, 1), date(2026, 8, 3))]},
        True,
        "t",
    )
    r = _sim(bars, {t: [low, gone]}, universe=universe)
    assert r.attempts == 0 and r.not_in_universe == 1  # BBB had left the index


def test_the_same_setup_is_not_re_entered_after_a_stop_out() -> None:
    path = {11: (100.0, 100.1, 98.0, 98.5)}
    bars = {"AAA": _day_bars("AAA", DAY, path)}
    signals = {}
    for i in range(10, 20):  # the setup keeps appearing bar after bar
        t, rec = _record("AAA", i)
        signals[t] = [rec]
    r = _sim(bars, signals)
    assert len([x for x in r.trades if x.status == TradeStatus.CLOSED]) == 1
    assert r.attempts == 1


def test_costs_and_slippage_are_charged() -> None:
    bars = {"AAA": _day_bars("AAA", DAY, {20: (100.0, 102.5, 99.95, 102.2)})}
    t, rec = _record("AAA", 10)
    # default limits: a ₹50,000 position leaves room for the estimated charges
    r = _sim(
        bars, {t: [rec]}, broker_config=PaperBrokerConfig(slippage_bps=10), limits=RiskLimits()
    )
    trade = next(x for x in r.trades if x.status == TradeStatus.CLOSED)
    assert trade.entry_price == 100.1  # 10 bps worse
    assert trade.charges > 0 and trade.net_pnl < trade.gross_pnl


# --- metrics & robustness --------------------------------------------------------------------------


def _trade(net: float, r: float, i: int, strategy: str = "TREND_CONTINUATION") -> PaperTrade:
    t0 = datetime(2026, 8, 4, 4, 0, tzinfo=UTC) + timedelta(hours=i)
    return PaperTrade(
        id=uuid.uuid4(),
        symbol="AAA",
        side=TradeSide.LONG,
        strategy=strategy,
        status=TradeStatus.CLOSED,
        planned_entry=100,
        stop_loss=99,
        target=102,
        quantity=10,
        filled_quantity=10,
        entry_price=100,
        exit_price=100 + net / 10,
        initial_risk=10,
        net_pnl=net,
        gross_pnl=net + 2,
        charges=2,
        r_multiple=r,
        exit_reason=ExitReason.TARGET if net > 0 else ExitReason.STOP_LOSS,
        created_at=t0,
        opened_at=t0,
        closed_at=t0 + timedelta(minutes=30),
    )


def test_metrics_cover_the_requested_figures() -> None:
    trades = [
        _trade(100, 1, 0),
        _trade(-50, -0.5, 1),
        _trade(200, 2, 2),
        _trade(-50, -0.5, 3),
        _trade(-50, -0.5, 4),
    ]
    days = [(date(2026, 8, d), 100_000.0 + d) for d in range(3, 6)]
    m, _ = compute_metrics(
        trades, days, starting_capital=100_000, planned_rr={str(trades[0].id): 2.0}
    )
    assert m.total_trades == 5 and m.win_rate == 40.0
    assert m.profit_factor == 2.0 and m.net_pnl == 150 and m.expectancy == 30
    assert m.expectancy_r == 0.3
    assert m.average_win == 150 and m.average_loss == -50 and m.payoff_ratio == 3.0
    assert m.max_consecutive_wins == 1 and m.max_consecutive_losses == 2
    assert m.max_drawdown == -100
    assert m.average_planned_rr == 2.0
    assert m.sharpe is None and "trading days" in m.sharpe_note
    assert m.average_holding_minutes == 30


def test_sharpe_only_when_meaningful() -> None:
    flat = [(date(2026, 1, 1) + timedelta(days=i), 100_000.0) for i in range(30)]
    assert sharpe_sortino(flat, 100_000)[0] is None
    wavy = [
        (date(2026, 1, 1) + timedelta(days=i), 100_000.0 + (50 if i % 2 else -20) + i * 10)
        for i in range(30)
    ]
    sharpe, sortino, note = sharpe_sortino(wavy, 100_000)
    assert sharpe is not None and sortino is not None and "sqrt(252)" in note


def test_robustness_flags_weak_evidence() -> None:
    trades = [
        _trade(1000, 3, 0),
        _trade(-50, -0.5, 1),
        _trade(-60, -0.6, 2),
        _trade(20, 0.2, 3),
        _trade(-40, -0.4, 4),
    ]
    rob = robustness(trades, starting_capital=100_000)
    text = " ".join(rob.flags)
    assert "Only 5 closed trades" in text
    assert "includes zero" in text
    assert "3 best trades" in text
    assert "single best trade" in text
    assert (
        rob.mc_net_pnl is not None and rob.mc_net_pnl.p5 <= rob.mc_net_pnl.p50 <= rob.mc_net_pnl.p95
    )
    assert robustness(trades, starting_capital=100_000) == rob  # seeded: reproducible
    assert rob.net_pnl_double_costs == round(sum(t.net_pnl for t in trades) - 10, 2)


def test_selection_objective_rewards_consistency_and_needs_trades() -> None:
    steady = [_trade(10, 0.5, i) for i in range(10)]
    spiky = [_trade(500 if i == 0 else -10, 5 if i == 0 else -0.1, i) for i in range(10)]
    assert selection_objective(steady[:3], min_trades=5) is None
    s1 = selection_objective(steady, 5)
    s2 = selection_objective(spiky, 5)
    assert s1 is not None and s2 is not None and s1 > s2  # despite spiky's larger total


def test_neighbour_smoothing_discounts_a_lone_spike() -> None:
    grid = {"a": [1, 2, 3, 4, 5]}
    params = [{"a": v} for v in grid["a"]]
    objectives: list[float | None] = [
        0.1,
        0.12,
        0.11,
        0.0,
        0.6,
    ]  # spike at the edge next to a poor point
    smoothed = _smooth(objectives, _neighbours(grid, params))
    assert _select(smoothed, objectives, params, {"a": 1}) == 4 or (smoothed[4] or 0) < 0.6
    # a broad plateau beats a narrow peak of similar height
    objectives = [0.0, 0.3, 0.32, 0.3, 0.0]
    smoothed = _smooth(objectives, _neighbours(grid, params))
    assert _select(smoothed, objectives, params, {"a": 1}) == 2


# --- parameters ----------------------------------------------------------------------------------


def test_parameters_are_validated_and_grids_capped() -> None:
    p = validate_params(StrategyChoice.TREND_CONTINUATION, {"min_pullback_percent": 25})
    assert p["min_pullback_percent"] == 25 and p["min_score"] == 85
    with pytest.raises(ParameterError):
        validate_params(StrategyChoice.TREND_CONTINUATION, {"min_pullback_percent": 5})
    with pytest.raises(ParameterError):
        validate_params(StrategyChoice.TREND_CONTINUATION, {"nope": 1})
    with pytest.raises(ParameterError):
        validate_params(StrategyChoice.ALL, {"trailing_mode": "SOMETIMES"})
    big: dict[str, list[Any]] = {
        "min_score": [60, 70, 80, 90],
        "trailing_activation_r": [0.5, 1, 1.5, 2],
        "trailing_atr_multiple": [1, 2],
    }
    with pytest.raises(ParameterError, match=str(MAX_GRID_COMBINATIONS)):
        expand_grid(StrategyChoice.ALL, {}, big)
    assert (
        len(
            expand_grid(
                StrategyChoice.ALL, {}, {"min_score": [70, 80], "trailing_mode": ["NONE", "ATR"]}
            )
        )
        == 4
    )


def test_strategy_choice_isolates_one_strategy_and_versions_follow_parameters() -> None:
    cfg = engine_config_for(StrategyChoice.BREAKOUT_MOMENTUM, {"min_relative_volume": 2.0})
    assert cfg.breakout_momentum.enabled and not cfg.trend_continuation.enabled
    assert cfg.breakout_momentum.min_relative_volume == 2.0
    default = strategy_versions(StrategyChoice.TREND_CONTINUATION, {})[0].version_id
    tuned = strategy_versions(StrategyChoice.TREND_CONTINUATION, {"min_pullback_percent": 25.0})[
        0
    ].version_id
    assert default.startswith("1.1.0+") and tuned != default  # TC 1.1.0: STOP entry
    strat, execu = split_scopes(
        StrategyChoice.TREND_CONTINUATION, validate_params(StrategyChoice.TREND_CONTINUATION, {})
    )
    assert "min_score" in execu and "min_pullback_percent" in strat


# --- the three modes (synthetic data, small) ---------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _cache(tmp_path_factory: pytest.TempPathFactory) -> None:
    import os

    os.environ["BACKTEST_CACHE_DIR"] = str(tmp_path_factory.mktemp("btcache"))


def _req(**kw: object) -> BacktestRequest:
    base: dict[str, object] = dict(
        symbols=["RELIANCE"],
        start_date=date(2026, 7, 27),
        end_date=date(2026, 8, 4),
        parameters={"min_score": 60},
        warmup_days=10,
    )
    base.update(kw)
    return BacktestRequest(**base)  # type: ignore[arg-type]


def test_historical_mode_reports_your_parameters_not_the_best_grid_row() -> None:
    out = run_backtest(_req(grid={"min_score": [55, 65, 75]}), workers=1)
    assert out.primary_label == "Full range"
    assert out.selected_params["min_score"] == 60  # yours, not a grid row
    assert out.grid and not any(r.selected for r in out.grid)
    assert out.combinations_tested == 3
    assert any("Synthetic data" in w for w in out.warnings)
    assert out.metrics.trading_days == 7
    assert out.data["synthetic"] is True and out.replay["errors"] == 0
    assert out.note.startswith("Backtest results describe")


def test_out_of_sample_mode_chooses_on_in_sample_only() -> None:
    out = run_backtest(_req(mode="OUT_OF_SAMPLE", grid={"min_score": [55, 65]}), workers=1)
    kinds = [s.kind for s in out.segments]
    assert kinds == ["IN_SAMPLE", "OUT_OF_SAMPLE"]
    is_seg, oos = out.segments
    assert is_seg.end < oos.start  # time-ordered, no overlap
    assert all(r.window == "in-sample" for r in out.grid)
    assert out.primary_label == "Out-of-sample"
    assert {t.segment for t in out.trades} <= {"IN_SAMPLE", "OUT_OF_SAMPLE"}


def test_walk_forward_mode_trades_only_unseen_windows() -> None:
    out = run_backtest(
        _req(mode="WALK_FORWARD", validation={"wf_train_days": 5, "wf_test_days": 2}),
        workers=1,
    )
    assert out.walk_forward is not None and out.walk_forward.windows >= 1
    trains = [s for s in out.segments if s.kind == "TRAIN"]
    tests = [s for s in out.segments if s.kind == "TEST"]
    for tr, te in zip(trains, tests, strict=True):
        assert tr.end < te.start
    assert all(t.segment.startswith("WF") for t in out.trades)
    for t in out.trades:
        assert t.opened_at is not None and t.opened_at.astimezone(IST).date() >= tests[0].start


def test_null_control_runs_on_no_edge_data() -> None:
    out = run_backtest(_req(validation={"null_control": True}), workers=1)
    assert out.null_control is not None
