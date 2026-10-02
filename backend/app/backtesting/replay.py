"""Phase A: signal replay.

Walks each symbol's 5-minute bars in time order and, at every decision
time (the close of an entry-timeframe bar), runs the live analysis stack
on the bars that had closed by then:

    market intelligence -> regime -> multi-timeframe price action
    -> strategy router -> signal scoring

No look-ahead, by construction:

* `ClosedBarHistory.window(now)` refuses to return a bar that had not
  closed at `now`, and every input to the analysis comes from it.
* Higher timeframes (15m/30m/60m) are aggregated from 5-minute bars
  anchored to the session open, and a higher-timeframe bar exists only
  once all of its 5-minute bars have closed (no partial bars).
* The benchmark (NIFTY 50) window is cut at the same time, and the
  market context for scoring is computed from it at that time.

What a decision may use is therefore exactly what a live scan at that
moment could have used. Execution (Phase B) fills at the next bar's open.

The analysis is independent of strategy parameters, so one pass serves
every strategy parameter set in a grid. Results are cached on disk keyed
by every input, so walk-forward windows and reruns reuse them.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
from collections import deque
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.backtesting.calendar import IST, SESSION_OPEN
from app.backtesting.data import HistoricalDataSource
from app.backtesting.parameters import StrategyChoice, engine_config_for
from app.market_data.schemas import Candle, Timeframe
from app.market_data.universe import NIFTY_INDEX_SYMBOL
from app.price_action.analyzer import analyze_price_action
from app.price_action.config import MultiTimeframeConfig, PriceActionConfig
from app.signals.config import SignalEngineConfig
from app.signals.engine import SignalEngine, market_context_from
from app.signals.schemas import MarketContext
from app.strategies.context import build_context
from app.strategies.router import StrategyRouter

#: Bump when replay logic changes so stale cache entries are not reused.
REPLAY_VERSION = "1"
WINDOW = 300  # bars per timeframe given to the analysis (live: HISTORY_CANDLES)
DAY_WINDOW = 500  # setup-timeframe bars for previous-day levels (live: DAY_HISTORY_CANDLES)

ENTRY_TIMEFRAMES: dict[str, tuple[Timeframe, Timeframe, Timeframe]] = {
    # entry: (context, setup, entry)
    "5m": (Timeframe.M60, Timeframe.M15, Timeframe.M5),
    "15m": (Timeframe.M60, Timeframe.M30, Timeframe.M15),
}


class LookAheadError(RuntimeError):
    """A bar that had not closed was about to be used."""


def bar_close(candle: Candle) -> datetime:
    return candle.timestamp + timedelta(minutes=candle.timeframe.minutes)


class ClosedBarHistory:
    """Append-only bar history that only hands out closed bars."""

    def __init__(self, maxlen: int) -> None:
        self._bars: deque[Candle] = deque(maxlen=maxlen)

    def append(self, candle: Candle) -> None:
        if self._bars and candle.timestamp <= self._bars[-1].timestamp:
            raise ValueError("bars must arrive in time order")
        self._bars.append(candle)

    def window(self, now: datetime) -> list[Candle]:
        if self._bars and bar_close(self._bars[-1]) > now:
            raise LookAheadError(
                f"{self._bars[-1].symbol} {self._bars[-1].timeframe.value} bar "
                f"{self._bars[-1].timestamp.isoformat()} closes after {now.isoformat()}"
            )
        return list(self._bars)

    def __len__(self) -> int:
        return len(self._bars)


class SessionAggregator:
    """Builds `minutes` bars from 5-minute session bars, anchored to 09:15
    IST. Emits a bar only when its last 5-minute bar arrives; a bucket the
    session cuts short (e.g. 15:15-15:30 for 30m) is never emitted."""

    def __init__(self, timeframe: Timeframe) -> None:
        self.timeframe = timeframe
        self.per = timeframe.minutes // 5
        self._open: list[Candle] = []
        self._key: tuple[date, int] | None = None

    def add(self, bar: Candle) -> Candle | None:
        local = bar.timestamp.astimezone(IST)
        minutes_in = (local.hour * 60 + local.minute) - (
            SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
        )
        bucket = minutes_in // self.timeframe.minutes
        key = (local.date(), bucket)
        if key != self._key:
            self._open, self._key = [], key
        self._open.append(bar)
        if len(self._open) < self.per or (minutes_in % self.timeframe.minutes) // 5 != self.per - 1:
            return None
        first = self._open[0]
        out = Candle(
            symbol=bar.symbol,
            timeframe=self.timeframe,
            timestamp=first.timestamp,
            open=first.open,
            high=max(c.high for c in self._open),
            low=min(c.low for c in self._open),
            close=bar.close,
            volume=sum(c.volume for c in self._open),
            is_closed=True,
        )
        self._open, self._key = [], None
        return out


class MultiTimeframeHistory:
    """5m bars in, closed bars on every analysed timeframe out."""

    def __init__(self, timeframes: tuple[Timeframe, ...], setup: Timeframe) -> None:
        self.timeframes = timeframes
        self.setup = setup
        self.histories = {tf: ClosedBarHistory(WINDOW) for tf in timeframes}
        self.day_history = ClosedBarHistory(DAY_WINDOW)
        self.aggregators = {tf: SessionAggregator(tf) for tf in timeframes if tf != Timeframe.M5}
        if setup != Timeframe.M5 and setup not in self.aggregators:
            self.aggregators[setup] = SessionAggregator(setup)
        self.last: dict[Timeframe, Candle] = {}

    def add(self, bar: Candle) -> list[Timeframe]:
        """Returns the timeframes that closed a bar with this 5m bar."""

        closed: list[Timeframe] = []
        if Timeframe.M5 in self.histories:
            self.histories[Timeframe.M5].append(bar)
            closed.append(Timeframe.M5)
        if self.setup == Timeframe.M5:
            self.day_history.append(bar)
        for tf, agg in self.aggregators.items():
            out = agg.add(bar)
            if out is None:
                continue
            if tf in self.histories:
                self.histories[tf].append(out)
                closed.append(tf)
            if tf == self.setup:
                self.day_history.append(out)
        return closed

    def windows(self, now: datetime) -> dict[Timeframe, list[Candle]]:
        return {tf: h.window(now) for tf, h in self.histories.items()}


# --- records ---------------------------------------------------------------------------------


def signal_record(signal: Any, param_index: int, atr: float) -> dict[str, Any]:
    """The slice of a TradingSignal the execution replay and reports need."""

    return {
        "ps": param_index,
        "symbol": signal.symbol,
        "strategy": signal.strategy.value,
        "version": signal.strategy_version,
        "direction": signal.direction.value,
        "entry": signal.entry,
        "stop": signal.stop_loss,
        "target": signal.target,
        "rr": signal.risk_reward,
        "score": signal.score,
        "category": signal.category.value,
        "confidence": signal.confidence,
        "rs": signal.relative_strength,
        "tq": signal.trend_quality,
        "rv": signal.relative_volume,
        "liq": signal.liquidity,
        "regime": signal.market_regime.value,
        "regime_conf": signal.regime_confidence,
        "atr": round(atr, 4),
        "reasons": signal.reasons[:12],
    }


def sort_key(r: dict[str, Any]) -> tuple[Any, ...]:
    """Same ordering as the live CandidateRankingEngine."""

    rs = r.get("rs")
    side_rs = (rs if r["direction"] == "BUY" else 100.0 - rs) if rs is not None else 0.0
    return (
        -round(r["score"]),
        -r["rr"],
        -side_rs,
        -(r.get("liq") or 0.0),
        -(r.get("tq") or 0.0),
        -(r.get("rv") or 0.0),
        -r["confidence"],
        r["symbol"],
    )


# --- one symbol ------------------------------------------------------------------------------


@dataclass
class ReplayJob:
    symbol: str
    bars: list[Candle]
    benchmark_bars: list[Candle]  # empty when replaying the benchmark itself
    decision_start: date
    decision_end: date
    entry: str  # "5m" | "15m"
    last_entry_time: time
    strategy: StrategyChoice
    strategy_param_sets: list[dict[str, Any]]
    market: dict[str, dict[str, Any]] = field(default_factory=dict)  # decision iso -> MarketContext


@dataclass
class ReplayResult:
    symbol: str
    signals: dict[str, list[dict[str, Any]]]  # decision iso -> records
    market: dict[str, dict[str, Any]]  # benchmark only: decision iso -> MarketContext
    decisions: int
    errors: int


def _pa_config(entry: str) -> PriceActionConfig:
    context, setup, entry_tf = ENTRY_TIMEFRAMES[entry]
    return PriceActionConfig(
        multi_timeframe=MultiTimeframeConfig(
            context_timeframe=context, setup_timeframe=setup, entry_timeframe=entry_tf
        )
    )


def replay_symbol(job: ReplayJob) -> ReplayResult:
    """Run the analysis at every decision time for one symbol. Pure: safe
    to run in a worker process."""

    context_tf, setup_tf, entry_tf = ENTRY_TIMEFRAMES[job.entry]
    pa_config = _pa_config(job.entry)
    timeframes = tuple(sorted({context_tf, setup_tf, entry_tf}, key=lambda t: t.minutes))
    history = MultiTimeframeHistory(timeframes, setup_tf)
    bench = MultiTimeframeHistory((entry_tf,), entry_tf) if job.benchmark_bars else None
    bench_bars = iter(job.benchmark_bars)
    pending_bench: Candle | None = next(bench_bars, None)
    is_benchmark = not job.benchmark_bars

    routers = [StrategyRouter(engine_config_for(job.strategy, p)) for p in job.strategy_param_sets]
    engine = SignalEngine(SignalEngineConfig())
    signals: dict[str, list[dict[str, Any]]] = {}
    market_out: dict[str, dict[str, Any]] = {}
    decisions = errors = 0
    day_open: dict[date, float] = {}

    for bar in job.bars:
        closed = history.add(bar)
        now = bar_close(bar)
        local = now.astimezone(IST)
        day_open.setdefault(local.date(), bar.open)
        # keep the benchmark in lockstep: every benchmark bar that has closed by now
        while bench is not None and pending_bench is not None and bar_close(pending_bench) <= now:
            bench.add(pending_bench)
            pending_bench = next(bench_bars, None)
        if entry_tf not in closed:
            continue
        day = local.date()
        if day < job.decision_start or day > job.decision_end:
            continue
        if local.time() > job.last_entry_time:
            continue  # the risk engine would refuse entries now; skip the work
        if len(history.histories[entry_tf]) < 60:
            continue  # not enough history to analyse yet
        key = now.isoformat()
        try:
            by_tf = history.windows(now)
            pa = analyze_price_action(
                job.symbol,
                by_tf,
                day_candles=history.day_history.window(now),
                config=pa_config,
                now=now,
            )
            ctx = build_context(
                job.symbol,
                by_tf,
                benchmark_candles=bench.histories[entry_tf].window(now) if bench else None,
                price_action=pa,
                price_action_config=pa_config,
                now=now,
            )
        except Exception:  # noqa: BLE001 - one bad bar must not end the run
            errors += 1
            continue
        if ctx is None:
            continue
        decisions += 1
        if is_benchmark:
            change = (ctx.price / day_open[day] - 1) * 100 if day_open.get(day) else None
            market_out[key] = market_context_from({job.symbol: ctx}, job.symbol, change).model_dump(
                mode="json"
            )
            continue
        market = MarketContext.model_validate(
            job.market.get(key) or {"benchmark_symbol": NIFTY_INDEX_SYMBOL, "available": False}
        )
        records: list[dict[str, Any]] = []
        for index, router in enumerate(routers):
            evaluation = router.evaluate(ctx)
            if not evaluation.candidates:
                continue
            for result in evaluation.candidates:
                try:
                    sig = engine.build_signal(ctx, result, market, now=now)
                except Exception:  # noqa: BLE001
                    errors += 1
                    continue
                records.append(signal_record(sig, index, ctx.atr))
        if records:
            signals[key] = records
    return ReplayResult(
        symbol=job.symbol, signals=signals, market=market_out, decisions=decisions, errors=errors
    )


# --- orchestration ---------------------------------------------------------------------------


@dataclass
class ReplayOutput:
    signals: dict[str, list[dict[str, Any]]]  # decision iso -> records (all symbols)
    market: dict[str, dict[str, Any]]
    decisions: int
    errors: int
    cache_hits: int
    symbols: list[str]


def _cache_dir() -> Path:
    path = Path(os.environ.get("BACKTEST_CACHE_DIR", "/tmp/gainzalgo-backtest-cache"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key(parts: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _cache_get(key: str) -> dict[str, Any] | None:
    path = _cache_dir() / f"{key}.json.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt") as fh:
            data: dict[str, Any] = json.load(fh)
            return data
    except (OSError, ValueError):
        return None


def _cache_put(key: str, value: dict[str, Any]) -> None:
    path = _cache_dir() / f"{key}.json.gz"
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as fh:
        json.dump(value, fh)
    tmp.replace(path)


def warmup_start(source: HistoricalDataSource, start: date, warmup_days: int) -> date:
    first, _ = source.available_range()
    from app.backtesting.calendar import trading_days

    days = trading_days(first, start - timedelta(days=1), source.holidays())
    return days[-warmup_days] if len(days) >= warmup_days else first


def run_replay(
    source: HistoricalDataSource,
    *,
    symbols: list[str],
    start: date,
    end: date,
    warmup_days: int,
    entry: str,
    last_entry_time: time,
    strategy: StrategyChoice,
    strategy_param_sets: list[dict[str, Any]],
    workers: int | None = None,
    progress: Callable[[float, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ReplayOutput:
    """Signals for every symbol and decision time in [start, end]."""

    report = progress or (lambda _p, _m: None)
    data_start = warmup_start(source, start, warmup_days)
    base = {
        "v": REPLAY_VERSION,
        "data": source.fingerprint(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "data_start": data_start.isoformat(),
        "entry": entry,
        "last_entry": last_entry_time.isoformat(),
    }
    bench_key = cache_key({**base, "symbol": NIFTY_INDEX_SYMBOL, "role": "benchmark"})
    cached_bench = _cache_get(bench_key)
    hits = 0
    benchmark_bars = source.bars(NIFTY_INDEX_SYMBOL, data_start, end)
    if cached_bench is not None:
        market = cached_bench["market"]
        hits += 1
    else:
        report(0.02, "Replaying NIFTY 50 for market context")
        bench_result = replay_symbol(
            ReplayJob(
                symbol=NIFTY_INDEX_SYMBOL,
                bars=benchmark_bars,
                benchmark_bars=[],
                decision_start=start,
                decision_end=end,
                entry=entry,
                last_entry_time=last_entry_time,
                strategy=strategy,
                strategy_param_sets=[],
            )
        )
        market = bench_result.market
        _cache_put(bench_key, {"market": market})

    strat_key = {"strategy": strategy.value, "params": strategy_param_sets}
    todo: list[ReplayJob] = []
    results: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    for symbol in symbols:
        key = cache_key({**base, **strat_key, "symbol": symbol})
        keys[symbol] = key
        cached = _cache_get(key)
        if cached is not None:
            results[symbol] = cached
            hits += 1
            continue
        todo.append(
            ReplayJob(
                symbol=symbol,
                bars=source.bars(symbol, data_start, end),
                benchmark_bars=benchmark_bars,
                decision_start=start,
                decision_end=end,
                entry=entry,
                last_entry_time=last_entry_time,
                strategy=strategy,
                strategy_param_sets=strategy_param_sets,
                market=market,
            )
        )

    done = len(results)
    total = len(symbols)
    report(0.05 + 0.85 * done / max(total, 1), f"Signals: {done}/{total} symbols (cached {hits})")
    if todo:
        n = workers or max(1, min(len(todo), (os.cpu_count() or 2)))
        if n == 1:
            for job in todo:
                if should_stop and should_stop():
                    raise InterruptedError("cancelled")
                r = replay_symbol(job)
                results[r.symbol] = {
                    "signals": r.signals,
                    "decisions": r.decisions,
                    "errors": r.errors,
                }
                _cache_put(keys[r.symbol], results[r.symbol])
                done += 1
                report(0.05 + 0.85 * done / total, f"Signals: {done}/{total} symbols")
        else:
            # spawn, not fork: the server process is multi-threaded
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as pool:
                futures = {pool.submit(replay_symbol, job): job.symbol for job in todo}
                for fut in as_completed(futures):
                    if should_stop and should_stop():
                        for f in futures:
                            f.cancel()
                        raise InterruptedError("cancelled")
                    r = fut.result()
                    results[r.symbol] = {
                        "signals": r.signals,
                        "decisions": r.decisions,
                        "errors": r.errors,
                    }
                    _cache_put(keys[r.symbol], results[r.symbol])
                    done += 1
                    report(0.05 + 0.85 * done / total, f"Signals: {done}/{total} symbols")

    merged: dict[str, list[dict[str, Any]]] = {}
    decisions = errors = 0
    for symbol in symbols:
        stored = results[symbol]
        decisions += stored["decisions"]
        errors += stored["errors"]
        for t, records in stored["signals"].items():
            merged.setdefault(t, []).extend(records)
    return ReplayOutput(
        signals=merged,
        market=market,
        decisions=decisions,
        errors=errors,
        cache_hits=hits,
        symbols=symbols,
    )
