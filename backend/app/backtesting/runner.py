"""Backtest orchestration: historical, out-of-sample and walk-forward.

HISTORICAL     One parameter set over the whole range. A grid, if given,
               is a sensitivity table only: the headline is always the
               parameters you chose, never the best row of the grid.
OUT_OF_SAMPLE  Days are split in time order. Parameters are chosen (from
               the grid, if any) on the in-sample days only, then run
               unchanged on the later out-of-sample days. The headline is
               the out-of-sample result.
WALK_FORWARD   Rolling (or anchored) train / test windows: choose on each
               train window, trade the following test window, move on.
               The headline is the stitched test windows, i.e. only days
               the chosen parameters had not seen.

Overfitting controls: grids are capped; selection maximises the lower
one-standard-error bound of the mean R multiple (consistency and sample
size, not peak profit), averaged with neighbouring grid points (a lone
spike next to poor neighbours is not chosen); sets with too few trades
are ineligible; the number of combinations tried is reported; selection
never sees test data. Data leakage controls: test days are never used to
choose parameters, warm-up bars are only ever earlier bars, and every
indicator is computed from closed bars at decision time (see replay).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import date, time
from pathlib import Path
from typing import Any

from app.analytics.performance import EquityPoint
from app.backtesting.calendar import trading_days
from app.backtesting.data import (
    CsvHistorySource,
    HistoricalDataSource,
    SyntheticHistorySource,
    ZerodhaHistorySource,
)
from app.backtesting.metrics import (
    BacktestMetrics,
    compute_metrics,
    robustness,
    selection_objective,
)
from app.backtesting.parameters import (
    expand_grid,
    params_hash,
    split_scopes,
    strategy_versions,
    validate_params,
)
from app.backtesting.replay import ENTRY_TIMEFRAMES, run_replay, warmup_start
from app.backtesting.schemas import (
    BacktestMode,
    BacktestOutcome,
    BacktestRequest,
    BacktestTrade,
    DailyEquity,
    DataSourceKind,
    GridResult,
    Segment,
    WalkForwardSummary,
)
from app.backtesting.simulator import ExecutionParams, SimulationResult, Tape, simulate
from app.execution.paper_broker import PaperBrokerConfig
from app.market_data.universe import NIFTY_INDEX_SYMBOL
from app.risk.config import MarketSessionConfig, RiskLimits

LAST_ENTRY = time(15, 0)

ASSUMPTIONS = [
    "Decisions use only bars closed at decision time; higher timeframes only complete bars.",
    "Entries are market orders filled at the next bar's open plus slippage.",
    "Inside a bar the stop is assumed to be hit before the target when both are touched.",
    "A stop crossed inside a bar fills at its level plus slippage; a gap through it fills at the open.",
    "Charges follow the configured cost model; slippage is applied on every fill.",
    "One trade at a time; daily loss limit, max trades and consecutive-loss stop as live.",
    "Positions are squared off at 15:15 IST; no new entries after 15:00 IST.",
    "Cross-symbol relative-strength rank is not used in backtests (it does not change scores).",
]


class BacktestError(ValueError):
    pass


ProgressFn = Callable[[float, str], None]


def make_source(req: BacktestRequest, data_dir: str | None) -> HistoricalDataSource:
    if req.data_source.kind == DataSourceKind.SYNTHETIC:
        return SyntheticHistorySource(seed=req.data_source.seed)
    if req.data_source.kind == DataSourceKind.ZERODHA:
        if not data_dir:
            raise BacktestError("Zerodha history has not been downloaded for this run")
        return ZerodhaHistorySource(Path(data_dir))
    if not data_dir:
        raise BacktestError("No CSV data directory configured (BACKTEST_DATA_DIR)")
    return CsvHistorySource(Path(data_dir))


def _limits(req: BacktestRequest, holidays: list[date]) -> RiskLimits:
    r = req.risk
    return RiskLimits(
        risk_per_trade_percent=r.risk_per_trade_percent,
        daily_loss_limit_percent=r.daily_loss_limit_percent,
        max_trades_per_day=r.max_trades_per_day,
        max_consecutive_losses=r.max_consecutive_losses,
        max_position_value=r.max_position_value,
        max_exposure=r.max_exposure,
        margin_multiplier=r.margin_multiplier,
        allow_short=r.allow_short,
        max_data_age_seconds=600,
        max_signal_age_seconds=600,
        session=MarketSessionConfig(holidays=holidays),
    )


def _broker(req: BacktestRequest) -> PaperBrokerConfig:
    return PaperBrokerConfig(
        slippage_bps=req.costs.slippage_bps,
        margin_multiplier=req.risk.margin_multiplier,
        costs=req.costs.cost_model(),
    )


def _neighbours(grid: dict[str, list[Any]], params: list[dict[str, Any]]) -> list[list[int]]:
    """Index pairs of parameter sets that differ by one step in one grid key."""

    keys = sorted(grid)
    pos = [tuple(grid[k].index(p[k]) if p[k] in grid[k] else -1 for k in keys) for p in params]
    out: list[list[int]] = [[] for _ in params]
    for i, a in enumerate(pos):
        for j, b in enumerate(pos):
            if i != j and sum(abs(x - y) for x, y in zip(a, b, strict=True)) == 1:
                out[i].append(j)
    return out


def _smooth(objectives: list[float | None], neighbours: list[list[int]]) -> list[float | None]:
    """Half the point's own score, half the mean of its grid neighbours: a
    lone spike between poor neighbours is discounted. A neighbour with too
    few trades counts as no better than zero."""

    out: list[float | None] = []
    for i, value in enumerate(objectives):
        if value is None:
            out.append(None)
            continue
        around = [objectives[j] for j in neighbours[i]]
        if not around:
            out.append(value)
            continue
        vals = [v if v is not None else min(value, 0.0) for v in around]
        out.append(round(0.5 * value + 0.5 * statistics.fmean(vals), 4))
    return out


def _select(
    smoothed: list[float | None],
    objectives: list[float | None],
    params: list[dict[str, Any]],
    defaults: dict[str, Any],
) -> int | None:
    """Highest smoothed objective; ties go to the higher raw objective, then
    to the set closer to the defaults (fewer changed knobs)."""

    def key(i: int) -> tuple[float, float, int]:
        changed = sum(1 for k in params[i] if params[i][k] != defaults.get(k))
        return (smoothed[i] or 0.0, objectives[i] or 0.0, -changed)

    eligible = [i for i, v in enumerate(smoothed) if v is not None]
    return max(eligible, key=key) if eligible else None


def preflight(
    req: BacktestRequest, data_dir: str | None = None
) -> tuple[HistoricalDataSource, list[date]]:
    """Everything that can be checked before the run starts. Raises
    BacktestError / ParameterError with a message meant for the user."""

    source = make_source(req, data_dir)
    first, last = source.available_range()
    if req.start_date < first or req.end_date > last:
        raise BacktestError(
            f"Data covers {first} to {last}; requested {req.start_date} to {req.end_date}"
        )
    known = set(source.symbols())
    unknown = [s for s in req.symbols if s not in known]
    if unknown:
        raise BacktestError(f"No data for: {', '.join(unknown)}")
    days = trading_days(req.start_date, req.end_date, source.holidays())
    if len(days) < 3:
        raise BacktestError("Fewer than 3 trading days in the range")
    if req.mode == BacktestMode.WALK_FORWARD:
        v = req.validation
        if len(days) < v.wf_train_days + v.wf_test_days:
            raise BacktestError(
                f"Walk-forward needs at least {v.wf_train_days + v.wf_test_days} trading days "
                f"(train {v.wf_train_days} + test {v.wf_test_days}); the range has {len(days)}"
            )
    if req.mode == BacktestMode.OUT_OF_SAMPLE and len(days) < 6:
        raise BacktestError("Out-of-sample testing needs at least 6 trading days")
    base = validate_params(req.strategy, req.parameters)
    if req.grid:
        expand_grid(req.strategy, base, req.grid)
    versions = strategy_versions(req.strategy, split_scopes(req.strategy, base)[0])
    if req.version and req.version not in {v.version_id for v in versions}:
        raise BacktestError(
            f"Version {req.version} is not what these parameters produce "
            f"({', '.join(v.version_id for v in versions)})"
        )
    return source, days


def estimate_seconds(req: BacktestRequest, days: int) -> float:
    """Rough run time: the signal replay dominates (about 16 ms per symbol
    per decision on one core)."""

    decisions = (len(req.symbols) + 1) * days * (69 if req.timeframe == "5m" else 23)
    replay = decisions * 0.016 / max(1, min(len(req.symbols), 2))
    return round(replay * (2 if req.validation.null_control else 1) + 5, 0)


def run_backtest(
    req: BacktestRequest,
    *,
    data_dir: str | None = None,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    workers: int | None = None,
) -> BacktestOutcome:
    report = progress or (lambda _p, _m: None)
    source, days = preflight(req, data_dir)
    if len(source.bars(NIFTY_INDEX_SYMBOL, req.start_date, req.end_date)) == 0:
        raise BacktestError("No benchmark (NIFTY 50) data in the range")

    base = validate_params(req.strategy, req.parameters)
    defaults = validate_params(req.strategy, {})
    # candidates[0] is always your parameter set; with a grid the selection
    # (OOS / walk-forward) only ever chooses among the grid combinations.
    combos = expand_grid(req.strategy, base, req.grid) if req.grid else []
    candidates = [base] + combos
    selectable = list(range(1, len(candidates))) if combos else [0]
    versions = strategy_versions(req.strategy, split_scopes(req.strategy, base)[0])

    strat_sets: list[dict[str, Any]] = []
    strat_index: list[int] = []
    for p in candidates:
        sp = split_scopes(req.strategy, p)[0]
        if sp not in strat_sets:
            strat_sets.append(sp)
        strat_index.append(strat_sets.index(sp))

    warnings: list[str] = []
    universe = source.universe()
    if source.synthetic:
        warnings.append(
            "Synthetic data: prices are simulated. A result here tests the machinery and the "
            "rules on a random process with built-in regimes, not a market edge."
        )
    if not universe.point_in_time:
        warnings.append(
            "Survivorship bias risk: the universe is today's list of files, not point-in-time "
            "index membership. Add universe.csv with membership dates."
        )
    for s in req.symbols:
        q = source.quality(s)
        if q.invalid_bars or q.missing_bars or q.duplicate_bars:
            warnings.append(
                f"{s}: {q.missing_bars} missing, {q.invalid_bars} invalid, "
                f"{q.duplicate_bars} duplicate bars"
            )

    # Phase A: signals over the whole range (every strategy parameter set at once)
    replay = run_replay(
        source,
        symbols=req.symbols,
        start=req.start_date,
        end=req.end_date,
        warmup_days=req.warmup_days,
        entry=req.timeframe,
        last_entry_time=LAST_ENTRY,
        strategy=req.strategy,
        strategy_param_sets=strat_sets,
        workers=workers,
        progress=lambda p, m: report(p * 0.8, m),
        should_stop=should_stop,
    )
    if replay.errors:
        warnings.append(f"{replay.errors} decision(s) failed to analyse and were skipped")

    # Phase B
    tape = Tape({s: source.bars(s, req.start_date, req.end_date) for s in req.symbols})
    limits = _limits(req, source.holidays())
    broker = _broker(req)
    entry_minutes = ENTRY_TIMEFRAMES[req.timeframe][2].minutes
    infos: dict[str, Any] = {}

    def sim(i: int, d0: date, d1: date, capital: float) -> SimulationResult:
        if should_stop and should_stop():
            raise InterruptedError("cancelled")
        result = simulate(
            bars=tape.bars,
            tape=tape,
            signals=replay.signals,
            param_index=strat_index[i],
            execution=ExecutionParams.from_params(candidates[i]),
            limits=limits,
            broker_config=broker,
            capital=capital,
            universe=universe,
            start=d0,
            end=d1,
            entry_minutes=entry_minutes,
        )
        infos.update(result.info)
        return result

    def metrics_of(r: SimulationResult, capital: float) -> BacktestMetrics:
        m, _ = compute_metrics(
            r.trades,
            r.daily_equity,
            starting_capital=capital,
            planned_rr={k: v.planned_rr for k, v in r.info.items()},
        )
        return m

    def choose(d0: date, d1: date, label: str) -> tuple[int, list[GridResult]]:
        if selectable == [0]:
            return 0, []
        runs = [sim(i, d0, d1, req.capital) for i in selectable]
        options = [candidates[i] for i in selectable]
        objectives = [
            selection_objective(r.trades, req.validation.min_trades_for_selection) for r in runs
        ]
        smoothed = _smooth(objectives, _neighbours(req.grid or {}, options))
        pick = _select(smoothed, objectives, options, defaults)
        # nothing qualified (too few trades everywhere): keep your parameters
        chosen = selectable[pick] if pick is not None else 0
        if pick is None:
            fallbacks.append(label)
        rows = []
        for n, r in enumerate(runs):
            i = selectable[n]
            m = metrics_of(r, req.capital)
            rows.append(
                GridResult(
                    params_hash=params_hash(candidates[i]),
                    params=candidates[i],
                    varied={k: candidates[i][k] for k in sorted(req.grid or {})},
                    objective=objectives[n],
                    smoothed=smoothed[n],
                    trades=m.total_trades,
                    net_pnl=m.net_pnl,
                    profit_factor=m.profit_factor,
                    expectancy_r=m.expectancy_r,
                    selected=i == chosen,
                    window=label,
                )
            )
        return chosen, rows

    fallbacks: list[str] = []
    segments: list[Segment] = []
    grid_rows: list[GridResult] = []
    all_trades: list[tuple[str, Any]] = []
    daily: list[tuple[date, float]] = []
    wf: WalkForwardSummary | None = None
    in_sample: BacktestMetrics | None = None
    degradation: float | None = None
    report(0.82, "Simulating execution")

    if req.mode == BacktestMode.HISTORICAL:
        chosen = 0
        primary = sim(0, days[0], days[-1], req.capital)
        if combos:
            _, grid_rows = choose(days[0], days[-1], "full range (sensitivity only)")
            for row in grid_rows:
                row.selected = False  # the headline is your parameters, not the best row
        m = metrics_of(primary, req.capital)
        segments.append(
            Segment(
                label="Full range",
                kind="FULL",
                start=days[0],
                end=days[-1],
                days=len(days),
                params_hash=params_hash(base),
                params=base,
                metrics=m,
            )
        )
        all_trades = [("FULL", t) for t in primary.trades]
        daily = primary.daily_equity
        label = "Full range"

    elif req.mode == BacktestMode.OUT_OF_SAMPLE:
        k = max(1, min(len(days) - 1, round(len(days) * (1 - req.validation.oos_fraction))))
        is_days, oos_days = days[:k], days[k:]
        chosen, grid_rows = choose(is_days[0], is_days[-1], "in-sample")
        is_run = sim(chosen, is_days[0], is_days[-1], req.capital)
        oos_run = sim(chosen, oos_days[0], oos_days[-1], req.capital)
        in_sample = metrics_of(is_run, req.capital)
        m = metrics_of(oos_run, req.capital)
        for kind, label_, ds, run, mm in (
            ("IN_SAMPLE", "In-sample", is_days, is_run, in_sample),
            ("OUT_OF_SAMPLE", "Out-of-sample", oos_days, oos_run, m),
        ):
            segments.append(
                Segment(
                    label=label_,
                    kind=kind,
                    start=ds[0],
                    end=ds[-1],
                    days=len(ds),
                    params_hash=params_hash(candidates[chosen]),
                    params=candidates[chosen],
                    objective=selection_objective(run.trades, 2),
                    metrics=mm,
                )
            )
        all_trades = [("IN_SAMPLE", t) for t in is_run.trades] + [
            ("OUT_OF_SAMPLE", t) for t in oos_run.trades
        ]
        primary = oos_run
        daily = oos_run.daily_equity
        if in_sample.expectancy_r and m.expectancy_r is not None and in_sample.expectancy_r > 0:
            degradation = round(m.expectancy_r / in_sample.expectancy_r, 2)
        label = "Out-of-sample"

    else:  # WALK_FORWARD
        v = req.validation
        capital = req.capital
        i = v.wf_train_days
        n_window = 0
        test_trades: list[Any] = []
        train_er: list[float] = []
        test_er: list[float] = []
        chosen_hashes: list[str] = []
        profitable = 0
        while i + 2 <= len(days):
            test = days[i : i + v.wf_test_days]
            if len(test) < 2:
                break
            train = days[:i] if v.wf_anchored else days[i - v.wf_train_days : i]
            n_window += 1
            report(0.82 + 0.15 * i / len(days), f"Walk-forward window {n_window}")
            chosen, rows = choose(train[0], train[-1], f"window {n_window} train")
            grid_rows.extend(rows)
            train_run = sim(chosen, train[0], train[-1], req.capital)
            test_run = sim(chosen, test[0], test[-1], capital)
            tm = metrics_of(train_run, req.capital)
            sm = metrics_of(test_run, capital)
            for kind, ds, mm in (("TRAIN", train, tm), ("TEST", test, sm)):
                segments.append(
                    Segment(
                        label=f"Window {n_window} {kind.lower()}",
                        kind=kind,
                        start=ds[0],
                        end=ds[-1],
                        days=len(ds),
                        params_hash=params_hash(candidates[chosen]),
                        params=candidates[chosen],
                        objective=(
                            selection_objective(train_run.trades, 2) if kind == "TRAIN" else None
                        ),
                        metrics=mm,
                    )
                )
            if tm.expectancy_r is not None:
                train_er.append(tm.expectancy_r)
            if sm.expectancy_r is not None:
                test_er.append(sm.expectancy_r)
            profitable += 1 if sm.net_pnl > 0 else 0
            chosen_hashes.append(params_hash(candidates[chosen]))
            test_trades.extend(test_run.trades)
            all_trades.extend((f"WF{n_window}", t) for t in test_run.trades)
            daily.extend(test_run.daily_equity)
            capital = test_run.end_equity
            i += v.wf_test_days
        changes = sum(1 for a, b in zip(chosen_hashes, chosen_hashes[1:], strict=False) if a != b)
        mean_train = statistics.fmean(train_er) if train_er else None
        mean_test = statistics.fmean(test_er) if test_er else None
        efficiency = (
            round(mean_test / mean_train, 2)
            if mean_train and mean_train > 0 and mean_test is not None
            else None
        )
        wf = WalkForwardSummary(
            windows=n_window,
            profitable_windows=profitable,
            efficiency=efficiency,
            parameter_changes=changes,
            note=(
                "Efficiency = mean test expectancy (R) / mean train expectancy (R); near 1 means "
                "results held up on unseen days, near 0 or negative means they did not."
            ),
        )
        primary = SimulationResult(test_trades, {}, daily, req.capital, capital)
        chosen = (
            candidates.index(next(c for c in candidates if params_hash(c) == chosen_hashes[-1]))
            if chosen_hashes
            else 0
        )
        label = "Walk-forward test windows (stitched)"

    metrics, perf = compute_metrics(
        primary.trades,
        daily,
        starting_capital=req.capital,
        planned_rr={k: v.planned_rr for k, v in infos.items()},
    )
    regime_of = {k: v.regime for k, v in infos.items()}
    rob = robustness(primary.trades, starting_capital=req.capital, regime_of=regime_of)
    if degradation is not None and degradation < 0.5:
        rob.flags.append(
            f"Out-of-sample expectancy is {degradation:.0%} of in-sample: the in-sample result did not hold up."
        )
    if wf is not None:
        if wf.efficiency is not None and wf.efficiency < 0.5:
            rob.flags.append(
                f"Walk-forward efficiency {wf.efficiency:.2f}: results weaken on unseen days."
            )
        if wf.windows and wf.profitable_windows / wf.windows < 0.5:
            rob.flags.append(
                f"Only {wf.profitable_windows} of {wf.windows} test windows were profitable."
            )
        if wf.parameter_changes > wf.windows / 2 and combos:
            rob.flags.append(
                "The selected parameters changed in most windows: the choice is unstable."
            )
    if combos:
        sel = [r for r in grid_rows if r.selected]
        if sel and req.grid:
            for key, values in req.grid.items():
                if len(values) > 2 and sel[-1].params.get(key) in (values[0], values[-1]):
                    rob.flags.append(
                        f"Selected {key}={sel[-1].params.get(key)} sits at the edge of the grid; "
                        "the best region may lie outside it (or it is fitting noise)."
                    )
        rob.flags.append(
            f"{len(combos)} parameter combinations were tried: the more you try, the more "
            "a good-looking result can be luck."
        )

    if fallbacks:
        rob.flags.append(
            f"No grid combination had {req.validation.min_trades_for_selection}+ trades in "
            f"{', '.join(sorted(set(fallbacks)))}; your own parameters were used there instead."
        )

    null_metrics: BacktestMetrics | None = None
    if req.validation.null_control:
        report(0.97, "Null-data control")
        null_source = SyntheticHistorySource(seed=req.data_source.seed, null_model=True)
        null_replay = run_replay(
            null_source,
            symbols=req.symbols,
            start=req.start_date,
            end=req.end_date,
            warmup_days=req.warmup_days,
            entry=req.timeframe,
            last_entry_time=LAST_ENTRY,
            strategy=req.strategy,
            strategy_param_sets=[split_scopes(req.strategy, candidates[chosen])[0]],
            workers=workers,
            should_stop=should_stop,
        )
        null_tape = Tape(
            {s: null_source.bars(s, req.start_date, req.end_date) for s in req.symbols}
        )
        null_run = simulate(
            bars=null_tape.bars,
            tape=null_tape,
            signals=null_replay.signals,
            param_index=0,
            execution=ExecutionParams.from_params(candidates[chosen]),
            limits=limits,
            broker_config=broker,
            capital=req.capital,
            universe=null_source.universe(),
            start=days[0],
            end=days[-1],
            entry_minutes=entry_minutes,
        )
        null_metrics = metrics_of(null_run, req.capital)
        if null_metrics.net_pnl > 0 and null_metrics.total_trades >= 10:
            rob.flags.append(
                "The rules also made money on the no-edge control data: suspect a bias or luck."
            )

    trades_out: list[BacktestTrade] = []
    for seg, t in all_trades:
        if t.filled_quantity <= 0 or t.status.value != "CLOSED":
            continue
        info = infos.get(str(t.id))
        trades_out.append(
            BacktestTrade(
                id=str(t.id),
                segment=seg,
                symbol=t.symbol,
                side=t.side.value,
                strategy=t.strategy,
                strategy_version=info.version if info else None,
                regime=info.regime if info else None,
                score=t.score,
                decision_time=info.decision_time if info else None,
                opened_at=t.opened_at,
                closed_at=t.closed_at,
                quantity=t.filled_quantity,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                stop_loss=t.stop_loss,
                trailing_stop=t.trailing_stop,
                target=t.target,
                exit_reason=t.exit_reason.value if t.exit_reason else None,
                gross_pnl=t.gross_pnl,
                charges=t.charges,
                net_pnl=t.net_pnl,
                r_multiple=t.r_multiple,
                planned_rr=info.planned_rr if info else None,
                reasons=info.reasons if info else [],
            )
        )

    report(1.0, "Done")
    return BacktestOutcome(
        mode=req.mode,
        primary_label=label,
        metrics=metrics,
        robustness=rob,
        equity_curve=[EquityPoint.model_validate(p.model_dump()) for p in perf.equity_curve],
        daily_equity=[DailyEquity(date=d, equity=round(e, 2)) for d, e in daily],
        trades=trades_out,
        segments=segments,
        grid=grid_rows,
        combinations_tested=max(1, len(combos)),
        selected_params=candidates[chosen],
        selected_params_hash=params_hash(candidates[chosen]),
        walk_forward=wf,
        in_sample_metrics=in_sample,
        degradation=degradation,
        null_control=null_metrics,
        strategy_versions=[v.model_dump() for v in versions],
        data={
            "kind": source.kind,
            "synthetic": source.synthetic,
            "fingerprint": source.fingerprint(),
            "universe": universe.source,
            "point_in_time_universe": universe.point_in_time,
            "trading_days": len(days),
            "warmup_from": warmup_start(source, req.start_date, req.warmup_days).isoformat(),
        },
        replay={
            "decisions": replay.decisions,
            "errors": replay.errors,
            "cache_hits": replay.cache_hits,
            "decision_times_with_signals": len(replay.signals),
        },
        execution={
            "entry_timeframe": req.timeframe,
            "slippage_bps": req.costs.slippage_bps,
            "attempts": primary.attempts if hasattr(primary, "attempts") else None,
        },
        warnings=warnings,
        assumptions=ASSUMPTIONS,
    )
