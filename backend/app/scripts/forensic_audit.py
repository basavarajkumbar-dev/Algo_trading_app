"""Strategy forensic audit: a READ-ONLY instrumented replay.

It changes nothing in the strategy, scoring, risk or execution code and
selects no parameters. It reruns the same backtest pipeline the app uses
and records everything that pipeline normally throws away:

* every decision (symbol x closed 5m bar): regime, router decision, each
  routed strategy's result and failed checks, hidden errors;
* every eligible candidate with its full score breakdown;
* what each strategy *would* have found with routing switched off
  (research view, `StrategyRouter.evaluate_all`), to separate router
  effects from detection effects;
* the official one-position-at-a-time simulation (same `simulate()` the
  runner uses), cross-checked against `run_backtest()`;
* an *unconstrained* bracket evaluation of every candidate episode
  (entry at the next bar's open, the signal's own stop/target, square-off
  15:15, configured costs). That is an analysis tool to get enough
  samples to test the score and long/short questions; it is not how the
  backtest trades.

Usage (inside the backend container, where the Zerodha cache lives):

    python -m app.scripts.forensic_audit \
        --source zerodha --data-dir /tmp/gainzalgo-backtest-cache/zerodha/<run dir> \
        --symbols M&M,TCS,JIOFIN,... --start 2026-08-24 --end 2026-10-02 \
        --out /tmp/forensic

    python -m app.scripts.forensic_audit --source synthetic --symbols ... (machinery check)

Outputs `<out>/forensic_audit.json` and `<out>/forensic_audit.md`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.backtesting.calendar import IST, trading_days
from app.backtesting.data import (
    CsvHistorySource,
    HistoricalDataSource,
    SyntheticHistorySource,
    ZerodhaHistorySource,
)
from app.backtesting.parameters import (
    StrategyChoice,
    engine_config_for,
    split_scopes,
    validate_params,
)
from app.backtesting.replay import (
    ENTRY_TIMEFRAMES,
    MultiTimeframeHistory,
    _pa_config,
    bar_close,
    run_replay,
    signal_record,
    warmup_start,
)
from app.backtesting.runner import LAST_ENTRY, _broker, _limits, run_backtest
from app.backtesting.schemas import (
    BacktestMode,
    BacktestRequest,
    DataSourceKind,
    DataSourceSpec,
)
from app.backtesting.simulator import ExecutionParams, Tape, simulate
from app.execution.costs import CostModel
from app.execution.models import Side
from app.market_data.schemas import Candle
from app.market_data.universe import NIFTY_INDEX_SYMBOL
from app.price_action.analyzer import analyze_price_action
from app.signals.config import SignalEngineConfig
from app.signals.engine import SignalEngine
from app.signals.schemas import MarketContext
from app.strategies.context import build_context
from app.strategies.router import StrategyRouter

SQUARE_OFF = time(15, 15)
BUCKETS = [(0, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 95), (95, 100.01)]
TC_FLOAT_FEATURES = (
    "alignment_score",
    "trend_quality",
    "relative_volume",
    "momentum_score",
    "relative_strength",
    "retracement_percent",
    "max_retracement_percent",
    "regime_confidence",
)


# --------------------------------------------------------------------------- replay


@dataclass
class SymbolAudit:
    symbol: str
    decisions: int = 0
    analysis_errors: int = 0
    analysis_error_samples: list[str] = field(default_factory=list)
    signal_build_errors: int = 0
    market_context_missing: int = 0
    regimes: Counter[str] = field(default_factory=Counter)
    regime_unknown_reasons: Counter[str] = field(default_factory=Counter)
    low_confidence: Counter[str] = field(default_factory=Counter)  # regime -> count < min conf
    routed: Counter[str] = field(default_factory=Counter)  # strategy -> decisions routed
    evaluated: Counter[str] = field(default_factory=Counter)  # strategy|direction attempted
    eligible: Counter[str] = field(default_factory=Counter)  # strategy|direction
    first_fail: Counter[str] = field(default_factory=Counter)  # strategy|check (first required)
    any_fail: Counter[str] = field(default_factory=Counter)  # strategy|check
    geometry: Counter[str] = field(default_factory=Counter)  # strategy|blocker kind
    strategy_errors: Counter[str] = field(default_factory=Counter)
    strategy_error_samples: list[str] = field(default_factory=list)
    unrouted_eligible: Counter[str] = field(default_factory=Counter)  # evaluate_all view
    unrouted_by_regime: Counter[str] = field(default_factory=Counter)
    feature_type_anomalies: Counter[str] = field(default_factory=Counter)
    scorer_errors: Counter[str] = field(default_factory=Counter)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    hour_regimes: Counter[str] = field(default_factory=Counter)


def _blocker_kind(text: str) -> str:
    if text.startswith("Stop is"):
        return "stop_too_wide"
    if text.startswith("Target offers"):
        return "reward_risk_too_low"
    return "other"


def audit_symbol(
    args: tuple[
        str,
        list[Candle],
        list[Candle],
        date,
        date,
        str,
        dict[str, Any],
        dict[str, dict[str, Any]],
        bool,
    ]
) -> SymbolAudit:
    symbol, bars, bench_bars, d0, d1, entry, strat_params, market, research = args
    context_tf, setup_tf, entry_tf = ENTRY_TIMEFRAMES[entry]
    pa_config = _pa_config(entry)
    tfs = tuple(sorted({context_tf, setup_tf, entry_tf}, key=lambda t: t.minutes))
    history = MultiTimeframeHistory(tfs, setup_tf)
    bench = MultiTimeframeHistory((entry_tf,), entry_tf)
    bench_iter = iter(bench_bars)
    pending = next(bench_iter, None)
    router = StrategyRouter(engine_config_for(StrategyChoice.ALL, strat_params))
    min_conf = router.config.router.min_regime_confidence
    engine = SignalEngine(SignalEngineConfig())
    out = SymbolAudit(symbol)

    for bar in bars:
        closed = history.add(bar)
        now = bar_close(bar)
        local = now.astimezone(IST)
        while pending is not None and bar_close(pending) <= now:
            bench.add(pending)
            pending = next(bench_iter, None)
        if entry_tf not in closed:
            continue
        day = local.date()
        if day < d0 or day > d1 or local.time() > LAST_ENTRY:
            continue
        if len(history.histories[entry_tf]) < 60:
            continue
        key = now.isoformat()
        try:
            by_tf = history.windows(now)
            pa = analyze_price_action(
                symbol,
                by_tf,
                day_candles=history.day_history.window(now),
                config=pa_config,
                now=now,
            )
            ctx = build_context(
                symbol,
                by_tf,
                benchmark_candles=bench.histories[entry_tf].window(now),
                price_action=pa,
                price_action_config=pa_config,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - counting what the replay swallows
            out.analysis_errors += 1
            if len(out.analysis_error_samples) < 5:
                out.analysis_error_samples.append(f"{key}: {type(exc).__name__}: {exc}")
            continue
        if ctx is None:
            continue
        out.decisions += 1
        reg = ctx.regime
        out.regimes[reg.regime.value] += 1
        out.hour_regimes[f"{local.hour:02d}|{reg.regime.value}"] += 1
        if reg.regime.value == "UNKNOWN":
            reason = (reg.reasons or ["?"])[0]
            out.regime_unknown_reasons[reason.split(":")[0].split(" (")[0][:60]] += 1
        elif reg.confidence < min_conf:
            out.low_confidence[reg.regime.value] += 1

        raw_market = market.get(key)
        if raw_market is None:
            out.market_context_missing += 1
        mkt = MarketContext.model_validate(
            raw_market or {"benchmark_symbol": NIFTY_INDEX_SYMBOL, "available": False}
        )

        evaluation = router.evaluate(ctx)
        for name in evaluation.decision.eligible_strategies:
            out.routed[name.value] += 1
        for res in evaluation.results:
            s = res.strategy.value
            if res.blockers and res.blockers[0].startswith("Strategy error"):
                out.strategy_errors[s] += 1
                if len(out.strategy_error_samples) < 5:
                    out.strategy_error_samples.append(f"{key} {s}: {res.blockers[0][:200]}")
                continue
            out.evaluated[f"{s}|{res.direction.value}"] += 1
            if s == "TREND_CONTINUATION":
                for fk in TC_FLOAT_FEATURES:
                    v = res.features.get(fk)
                    if v is not None and not isinstance(v, float):
                        out.feature_type_anomalies[f"{fk}:{type(v).__name__}"] += 1
            failed = [c.key for c in res.checks if c.required and not c.passed]
            if failed:
                out.first_fail[f"{s}|{failed[0]}"] += 1
                for k in failed:
                    out.any_fail[f"{s}|{k}"] += 1
            elif res.direction.value == "NONE":
                out.first_fail[f"{s}|no_direction"] += 1
            if not failed and not res.eligible:
                for b in res.blockers:
                    out.geometry[f"{s}|{_blocker_kind(b)}"] += 1
            if res.eligible:
                out.eligible[f"{s}|{res.direction.value}"] += 1

        for cand in evaluation.candidates:
            try:
                sig = engine.build_signal(ctx, cand, mkt, now=now)
            except Exception:  # noqa: BLE001
                out.signal_build_errors += 1
                continue
            rec = signal_record(sig, 0, ctx.atr)
            comps = {}
            for c in sig.breakdown.components:
                comps[c.key] = {
                    "applicable": c.applicable,
                    "score": c.score,
                    "points": c.points,
                    "effective_weight": c.effective_weight,
                }
                if "could not score" in c.detail:
                    out.scorer_errors[c.key] += 1
            rec["key"] = key
            rec["components"] = comps
            rec["market_available"] = mkt.available
            rec["market_bias"] = mkt.bias_score
            rec["market_regime"] = mkt.regime.value if mkt.regime else None
            rec["features"] = {
                k: cand.features.get(k)
                for k in (
                    "reference_range",
                    "breakout_direction",
                    "extension_atr",
                    "breakout_relative_volume",
                    "candles_since_breakout",
                    "trend_source",
                    "retracement_percent",
                    "max_retracement_percent",
                    "confirmation_patterns",
                    "momentum_returning",
                    "latest_closes_with_trend",
                    "context_direction",
                )
            }
            rec["checks"] = {c.key: c.passed for c in cand.checks}
            rec["entry_basis"] = cand.entry_basis
            rec["stop_basis"] = cand.stop_basis
            rec["target_basis"] = cand.target_basis
            out.candidates.append(rec)

        if research:
            for res in router.evaluate_all(ctx):
                if res.eligible:
                    tag = f"{res.strategy.value}|{res.direction.value}"
                    out.unrouted_eligible[tag] += 1
                    out.unrouted_by_regime[f"{tag}|{reg.regime.value}"] += 1
    return out


# --------------------------------------------------------------------------- bracket


def bracket(
    rec: dict[str, Any],
    day_bars: list[Candle],
    *,
    slippage_bps: float,
    costs: CostModel,
    capital: float = 100_000.0,
    risk_pct: float = 1.0,
    max_value: float = 50_000.0,
    stop_scale: float = 1.0,
    target_scale: float = 1.0,
) -> dict[str, Any] | None:
    """Same-day bracket from the signal record. Entry: next bar open +
    slippage (market). Stop: SL-M, crossed level + slippage, gap -> open.
    Target: limit at its price. Path: open -> adverse -> favourable ->
    close (stop before target on an ambiguous bar, like the simulator).
    Square-off: market at the 15:15 bar's open."""

    decision = datetime.fromisoformat(rec["key"])
    long = rec["direction"] == "BUY"
    sign = 1 if long else -1
    planned = rec["entry"]
    stop = planned - sign * abs(planned - rec["stop"]) * stop_scale
    target = planned + sign * abs(rec["target"] - planned) * target_scale
    after = [b for b in day_bars if b.timestamp >= decision]
    if not after:
        return None
    first = after[0]
    if first.timestamp.astimezone(IST).time() >= SQUARE_OFF:
        return None
    slip = lambda p: p * slippage_bps / 10_000  # noqa: E731
    fill = first.open + sign * slip(first.open)
    risk_unit = abs(fill - stop)
    if (fill - stop) * sign <= 0:
        return {"skipped": "fill beyond stop"}
    qty = min(math.floor(capital * risk_pct / 100 / risk_unit), math.floor(max_value / fill))
    if qty <= 0:
        return {"skipped": "zero quantity"}
    exit_price = exit_reason = exit_time = None
    mfe = 0.0
    for i, b in enumerate(after):
        if b.timestamp.astimezone(IST).time() >= SQUARE_OFF:
            exit_price, exit_reason, exit_time = (
                b.open - sign * slip(b.open),
                "SQUARE_OFF",
                b.timestamp,
            )
            break
        path = [b.open, b.low, b.high, b.close] if long else [b.open, b.high, b.low, b.close]
        if i == 0:
            path = path[1:]  # the fill is the open
            pts = [b.open, *path]
        else:
            pts = path
        prev = pts[0]
        # gap check on the open
        if i > 0:
            if (b.open - stop) * sign <= 0:
                exit_price, exit_reason, exit_time = (
                    b.open - sign * slip(b.open),
                    "STOP_LOSS",
                    b.timestamp,
                )
                break
            if (b.open - target) * sign >= 0:
                exit_price, exit_reason, exit_time = target, "TARGET", b.timestamp
                break
        for p in pts[1:]:
            lo, hi = min(prev, p), max(prev, p)
            # in path order, check the level price meets first
            hits = []
            if lo <= stop <= hi:
                hits.append((abs(stop - prev), "STOP_LOSS"))
            if lo <= target <= hi:
                hits.append((abs(target - prev), "TARGET"))
            if hits:
                hits.sort()
                what = hits[0][1]
                if what == "STOP_LOSS":
                    exit_price = stop - sign * slip(stop)
                else:
                    exit_price = target
                exit_reason, exit_time = what, b.timestamp
                break
            prev = p
        if exit_price is not None:
            break
        mfe = max(mfe, ((b.high if long else b.low) - fill) * sign)
    if exit_price is None:
        last = after[-1]
        exit_price, exit_reason, exit_time = (
            last.close - sign * slip(last.close),
            "END_OF_DATA",
            last.timestamp,
        )
    entry_side = Side.BUY if long else Side.SELL
    c_in = costs.fill_charges(side=entry_side, turnover=qty * fill).total
    c_out = costs.fill_charges(side=entry_side.opposite, turnover=qty * exit_price).total
    gross = (exit_price - fill) * sign * qty
    planned_risk = abs(planned - stop) * qty  # same R definition as the engine
    slip_cost = (
        abs(fill - first.open) + (slip(exit_price) if exit_reason != "TARGET" else 0)
    ) * qty
    return {
        "symbol": rec["symbol"],
        "strategy": rec["strategy"],
        "direction": rec["direction"],
        "score": rec["score"],
        "regime": rec["regime"],
        "decision": rec["key"],
        "entry_time": first.timestamp.isoformat(),
        "fill": round(fill, 4),
        "planned_entry": planned,
        "stop": round(stop, 4),
        "target": round(target, 4),
        "exit_time": exit_time.isoformat() if exit_time else None,
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "qty": qty,
        "gross_r": round(gross / planned_risk, 3),
        "r": round((gross - c_in - c_out) / planned_risk, 3),
        "cost_r": round((c_in + c_out) / planned_risk, 3),
        "slip_r_approx": round(slip_cost / planned_risk, 3),
        "stop_pct": round(abs(planned - stop) / planned * 100, 3),
        "stop_atr": round(abs(planned - stop) / rec["atr"], 2) if rec.get("atr") else None,
        "planned_rr": rec["rr"],
        "mfe_r": round(mfe / abs(planned - stop), 2),
    }


# --------------------------------------------------------------------------- stats


def stats(rs: list[float]) -> dict[str, Any]:
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    streak_w = streak_l = cur_w = cur_l = 0
    for r in rs:
        if r > 0:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        streak_w, streak_l = max(streak_w, cur_w), max(streak_l, cur_l)
    gp, gl = sum(wins), -sum(losses)
    total = sum(rs)
    top = sorted(rs, reverse=True)
    return {
        "n": len(rs),
        "win_rate": round(len(wins) / len(rs) * 100, 1),
        "avg_r": round(statistics.mean(rs), 3),
        "median_r": round(statistics.median(rs), 3),
        "expectancy_r": round(total / len(rs), 3),
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "max_dd_r": round(dd, 2),
        "avg_win_r": round(statistics.mean(wins), 3) if wins else None,
        "avg_loss_r": round(statistics.mean(losses), 3) if losses else None,
        "largest_win_r": round(max(rs), 3),
        "largest_loss_r": round(min(rs), 3),
        "max_consec_wins": streak_w,
        "max_consec_losses": streak_l,
        "total_r": round(total, 2),
        "top1_share_pct": round(top[0] / gp * 100, 1) if gp > 0 else None,
        "top3_share_pct": round(sum(r for r in top[:3] if r > 0) / gp * 100, 1) if gp > 0 else None,
        "sd_r": round(statistics.pstdev(rs), 3),
        "p10_r": round(sorted(rs)[int(0.1 * (len(rs) - 1))], 3),
        "p90_r": round(sorted(rs)[int(0.9 * (len(rs) - 1))], 3),
    }


def bucket_of(score: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= score < hi:
            return f"{lo}-{min(hi, 100):g}".replace("100.01", "100")
    return "?"


# --------------------------------------------------------------------------- data quality


def data_quality(
    source: HistoricalDataSource, symbols: list[str], d0: date, d1: date
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    days = trading_days(d0, d1, source.holidays())
    for s in symbols:
        bars = source.bars(s, d0, d1)
        per_day: Counter[date] = Counter(b.timestamp.astimezone(IST).date() for b in bars)
        ts = [b.timestamp for b in bars]
        q = source.quality(s)
        bad_ohlc = sum(
            1
            for b in bars
            if b.high < max(b.open, b.close) or b.low > min(b.open, b.close) or b.low <= 0
        )
        out_of_session = sum(
            1
            for b in bars
            if not (time(9, 15) <= b.timestamp.astimezone(IST).time() < time(15, 30))
        )
        zero_vol = sum(1 for b in bars if b.volume <= 0) if s != NIFTY_INDEX_SYMBOL else None
        jumps = []
        for a, b in zip(bars, bars[1:], strict=False):
            if a.timestamp.astimezone(IST).date() != b.timestamp.astimezone(IST).date():
                gap = abs(b.open / a.close - 1) * 100
                if gap > 8:
                    jumps.append(f"{b.timestamp.astimezone(IST).date()}: {gap:.1f}% overnight")
        out[s] = {
            "bars": len(bars),
            "days_with_data": len(per_day),
            "expected_days": len(days),
            "missing_days": [str(d) for d in days if d not in per_day][:10],
            "short_days": {str(d): n for d, n in per_day.items() if n < 75},
            "duplicates": len(ts) - len(set(ts)),
            "bad_ohlc": bad_ohlc,
            "out_of_session": out_of_session,
            "zero_volume": zero_vol,
            "tz": str(bars[0].timestamp.tzinfo) if bars else None,
            "large_overnight_gaps": jumps[:5],
            "source_quality": {
                "missing_bars": q.missing_bars,
                "invalid_bars": q.invalid_bars,
                "duplicate_bars": q.duplicate_bars,
                "out_of_session_bars": q.out_of_session_bars,
                "notes": q.notes[:5],
            },
        }
    return out


# --------------------------------------------------------------------------- main


def make_source(kind: str, data_dir: str | None, seed: int) -> HistoricalDataSource:
    if kind == "synthetic":
        return SyntheticHistorySource(seed=seed)
    if not data_dir:
        raise SystemExit("--data-dir is required for csv/zerodha")
    if kind == "zerodha":
        return ZerodhaHistorySource(Path(data_dir))
    return CsvHistorySource(Path(data_dir))


def _cache_root() -> Path:
    return Path(os.environ.get("BACKTEST_CACHE_DIR", "/tmp/gainzalgo-backtest-cache")) / "zerodha"


def list_caches() -> int:
    root = _cache_root()
    if not root.is_dir():
        print(f"No Zerodha cache under {root}")
        return 1
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        cov = d / "coverage.json"
        syms = sorted(p.stem for p in d.glob("*.csv") if p.stem != NIFTY_INDEX_SYMBOL)
        print(
            f"{d}\n  coverage: {cov.read_text().strip() if cov.exists() else 'INCOMPLETE'}\n"
            f"  symbols ({len(syms)}): {','.join(syms)}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--list-caches", action="store_true", help="show cached Zerodha runs and exit")
    ap.add_argument("--source", choices=["synthetic", "csv", "zerodha"], default="synthetic")
    ap.add_argument("--data-dir", help="CSV / Zerodha cache directory (see --list-caches)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--symbols", default="auto", help="comma separated, or 'auto' = every CSV")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--warmup-days", type=int, default=15)
    ap.add_argument("--out", default="forensic_out")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--no-research", action="store_true", help="skip the unrouted view")
    ap.add_argument("--skip-official", action="store_true", help="skip run_backtest cross-check")
    a = ap.parse_args(argv)
    if a.list_caches:
        return list_caches()
    if not a.start or not a.end:
        ap.error("--start and --end are required")

    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    source = make_source(a.source, a.data_dir, a.seed)
    if a.symbols == "auto":
        if source.synthetic:
            ap.error("--symbols is required for synthetic data")
        symbols = [s for s in source.symbols() if s != NIFTY_INDEX_SYMBOL]
    else:
        symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"source={source.kind} synthetic={source.synthetic} symbols={symbols} {d0}..{d1}",
        flush=True,
    )

    req = BacktestRequest(
        name="forensic",
        mode=BacktestMode.HISTORICAL,
        strategy=StrategyChoice.ALL,
        symbols=symbols,
        start_date=d0,
        end_date=d1,
        timeframe="5m",
        data_source=DataSourceSpec(kind=DataSourceKind(a.source)),
        warmup_days=a.warmup_days,
    )
    base = validate_params(StrategyChoice.ALL, {})
    strat_params, exec_params = split_scopes(StrategyChoice.ALL, base)
    report: dict[str, Any] = {
        "source": source.kind,
        "synthetic": source.synthetic,
        "symbols": symbols,
        "start": str(d0),
        "end": str(d1),
        "default_params": base,
        "universe_point_in_time": source.universe().point_in_time,
        "universe_source": source.universe().source,
    }

    # 1. data quality
    report["data_quality"] = data_quality(source, [*symbols, NIFTY_INDEX_SYMBOL], d0, d1)

    # 2. official pipeline (same functions as run_backtest)
    replay = run_replay(
        source,
        symbols=symbols,
        start=d0,
        end=d1,
        warmup_days=a.warmup_days,
        entry="5m",
        last_entry_time=LAST_ENTRY,
        strategy=StrategyChoice.ALL,
        strategy_param_sets=[strat_params],
        workers=a.workers or None,
    )
    tape = Tape({s: source.bars(s, d0, d1) for s in symbols})
    limits = _limits(req, source.holidays())
    broker = _broker(req)

    def official(min_score: float) -> Any:
        return simulate(
            bars=tape.bars,
            tape=tape,
            signals=replay.signals,
            param_index=0,
            execution=ExecutionParams.from_params({**exec_params, "min_score": min_score}),
            limits=limits,
            broker_config=broker,
            capital=req.capital,
            universe=source.universe(),
            start=d0,
            end=d1,
            entry_minutes=5,
        )

    sims = {ms: official(ms) for ms in (80.0, 85.0, 90.0)}
    sim = sims[85.0]
    closed = [t for t in sim.trades if t.filled_quantity > 0]
    trades = []
    for t in closed:
        info = sim.info.get(str(t.id))
        trades.append(
            {
                "symbol": t.symbol,
                "side": t.side.value,
                "strategy": t.strategy,
                "score": t.score,
                "regime": info.regime if info else None,
                "decision_time": info.decision_time.isoformat() if info else None,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "planned_entry": t.planned_entry,
                "entry_price": t.entry_price,
                "stop": t.stop_loss,
                "target": t.target,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason.value if t.exit_reason else None,
                "qty": t.filled_quantity,
                "gross": t.gross_pnl,
                "charges": t.charges,
                "net": t.net_pnl,
                "r": t.r_multiple,
                "charges_r": (
                    round(t.charges / (abs(t.planned_entry - t.stop_loss) * t.filled_quantity), 3)
                    if t.filled_quantity and t.planned_entry != t.stop_loss
                    else None
                ),
                "stop_pct": round(abs(t.planned_entry - t.stop_loss) / t.planned_entry * 100, 3),
                "planned_rr": info.planned_rr if info else None,
            }
        )
    report["official"] = {
        "replay_decisions": replay.decisions,
        "replay_errors": replay.errors,
        "signal_records": sum(len(v) for v in replay.signals.values()),
        "decision_bars_with_records": sim.decisions,
        "executable_signals_seen": sim.executable_signals,
        "not_in_universe": sim.not_in_universe,
        "attempts": sim.attempts,
        "risk_rejections": dict(sim.rejections),
        "engine_trades_recorded": len(sim.trades),
        "trades": trades,
        "stats": stats([t["r"] for t in trades if t["r"] is not None]),
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "sensitivity_min_score": {
            str(ms): {
                "trades": len([t for t in s.trades if t.filled_quantity > 0]),
                "stats": stats(
                    [
                        t.r_multiple
                        for t in s.trades
                        if t.filled_quantity > 0 and t.r_multiple is not None
                    ]
                ),
            }
            for ms, s in sims.items()
        },
    }
    if not a.skip_official:
        outcome = run_backtest(req, data_dir=a.data_dir)
        report["official"]["run_backtest_trades"] = len(outcome.trades)
        report["official"]["run_backtest_matches"] = len(outcome.trades) == len(
            [t for t in sim.trades if t.filled_quantity > 0]
        ) or len(outcome.trades) == len(sim.trades)

    # 3. instrumented replay
    data_start = warmup_start(source, d0, a.warmup_days)
    bench_bars = source.bars(NIFTY_INDEX_SYMBOL, data_start, d1)
    jobs = [
        (
            s,
            source.bars(s, data_start, d1),
            bench_bars,
            d0,
            d1,
            "5m",
            strat_params,
            replay.market,
            not a.no_research,
        )
        for s in symbols
    ]
    workers = a.workers or min(len(jobs), os.cpu_count() or 2)
    if workers > 1:
        import multiprocessing

        with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            audits = list(pool.map(audit_symbol, jobs))
    else:
        audits = [audit_symbol(j) for j in jobs]

    def merge(attr: str) -> dict[str, int]:
        c: Counter[str] = Counter()
        for x in audits:
            c.update(getattr(x, attr))
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    candidates = [c for x in audits for c in x.candidates]
    decisions = sum(x.decisions for x in audits)
    report["instrumented"] = {
        "decisions": decisions,
        "matches_official_decisions": decisions == replay.decisions,
        "candidates": len(candidates),
        "matches_official_records": len(candidates) == report["official"]["signal_records"],
        "analysis_errors": sum(x.analysis_errors for x in audits),
        "analysis_error_samples": [s for x in audits for s in x.analysis_error_samples][:10],
        "signal_build_errors": sum(x.signal_build_errors for x in audits),
        "market_context_missing": sum(x.market_context_missing for x in audits),
        "regimes": merge("regimes"),
        "regimes_by_symbol": {x.symbol: dict(x.regimes) for x in audits},
        "regime_unknown_reasons": merge("regime_unknown_reasons"),
        "regime_low_confidence": merge("low_confidence"),
        "hour_regimes": merge("hour_regimes"),
        "routed": merge("routed"),
        "evaluated": merge("evaluated"),
        "eligible": merge("eligible"),
        "first_fail": merge("first_fail"),
        "any_fail": merge("any_fail"),
        "geometry_blockers": merge("geometry"),
        "strategy_errors": merge("strategy_errors"),
        "strategy_error_samples": [s for x in audits for s in x.strategy_error_samples][:10],
        "scorer_errors": merge("scorer_errors"),
        "feature_type_anomalies": merge("feature_type_anomalies"),
        "unrouted_eligible": merge("unrouted_eligible"),
        "unrouted_by_regime": merge("unrouted_by_regime"),
    }

    # candidate distributions
    by = lambda f: dict(Counter(f(c) for c in candidates))  # noqa: E731
    report["candidates"] = {
        "by_strategy_direction": by(lambda c: f"{c['strategy']}|{c['direction']}"),
        "by_regime_direction": by(lambda c: f"{c['regime']}|{c['direction']}"),
        "by_score_bucket": by(lambda c: bucket_of(c["score"])),
        "by_score_bucket_direction": by(lambda c: f"{bucket_of(c['score'])}|{c['direction']}"),
        "market_unavailable": sum(1 for c in candidates if not c["market_available"]),
        "component_means_by_direction": {},
    }
    comp_means: dict[str, dict[str, float]] = {}
    for d in ("BUY", "SELL"):
        rows = [c for c in candidates if c["direction"] == d]
        if not rows:
            continue
        comp_means[d] = {
            k: round(statistics.mean(r["components"][k]["points"] for r in rows), 2)
            for k in rows[0]["components"]
        }
        comp_means[d]["total"] = round(statistics.mean(r["score"] for r in rows), 2)
    report["candidates"]["component_means_by_direction"] = comp_means

    # 4. episodes and unconstrained brackets
    candidates.sort(key=lambda c: (c["key"], c["symbol"]))
    keys_by_symbol: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for c in candidates:
        keys_by_symbol[(c["symbol"], c["strategy"], c["direction"])].append(c["key"])
    entry_minutes = timedelta(minutes=5)
    first_of_episode: list[dict[str, Any]] = []
    first_exec_of_episode: list[dict[str, Any]] = []
    for k, keys in keys_by_symbol.items():
        recs = {
            c["key"]: c for c in candidates if (c["symbol"], c["strategy"], c["direction"]) == k
        }
        episode: list[dict[str, Any]] = []
        prev: datetime | None = None

        def flush(ep: list[dict[str, Any]]) -> None:
            if not ep:
                return
            first_of_episode.append(ep[0])
            ex = next((r for r in ep if r["score"] >= 85), None)
            if ex is not None:
                first_exec_of_episode.append(ex)

        for key in sorted(set(keys)):
            t = datetime.fromisoformat(key)
            if prev is not None and t - prev != entry_minutes:
                flush(episode)
                episode = []
            episode.append(recs[key])
            prev = t
        flush(episode)

    day_bars: dict[tuple[str, date], list[Candle]] = defaultdict(list)
    for s in symbols:
        for b in source.bars(s, d0, d1):
            day_bars[(s, b.timestamp.astimezone(IST).date())].append(b)
    costs = req.costs.cost_model()

    def run_brackets(rows: list[dict[str, Any]], **kw: float) -> list[dict[str, Any]]:
        res = []
        for r in rows:
            day = datetime.fromisoformat(r["key"]).astimezone(IST).date()
            b = bracket(
                r,
                day_bars[(r["symbol"], day)],
                slippage_bps=req.costs.slippage_bps,
                costs=costs,
                **kw,
            )
            if b and "skipped" not in b:
                res.append(b)
        return res

    eps = run_brackets(first_of_episode)
    exe = run_brackets(first_exec_of_episode)
    group = lambda rows, f: {  # noqa: E731
        g: stats([r["r"] for r in rows if f(r) == g]) for g in sorted({f(r) for r in rows})
    }
    report["unconstrained"] = {
        "note": "Every candidate episode taken independently (no one-position limit, no risk "
        "engine). Analysis only: not the backtest's trades.",
        "episodes": len(first_of_episode),
        "episodes_reaching_85": len(first_exec_of_episode),
        "all_episodes": stats([r["r"] for r in eps]),
        "all_episodes_gross": stats([r["gross_r"] for r in eps]),
        "executable_episodes": stats([r["r"] for r in exe]),
        "executable_episodes_gross": stats([r["gross_r"] for r in exe]),
        "by_score_bucket": group(eps, lambda r: bucket_of(r["score"])),
        "by_direction": group(eps, lambda r: r["direction"]),
        "by_strategy": group(eps, lambda r: r["strategy"]),
        "by_regime": group(eps, lambda r: r["regime"]),
        "by_strategy_direction": group(eps, lambda r: f"{r['strategy']}|{r['direction']}"),
        "exit_reasons": dict(Counter(r["exit_reason"] for r in eps)),
        "exit_reasons_executable": dict(Counter(r["exit_reason"] for r in exe)),
        "cost_r": {
            "median": round(statistics.median(r["cost_r"] for r in eps), 3) if eps else None,
            "mean": round(statistics.mean(r["cost_r"] for r in eps), 3) if eps else None,
            "p90": (
                round(sorted(r["cost_r"] for r in eps)[int(0.9 * (len(eps) - 1))], 3)
                if eps
                else None
            ),
        },
        "stop_pct": {
            "median": round(statistics.median(r["stop_pct"] for r in eps), 3) if eps else None,
            "p10": (
                round(sorted(r["stop_pct"] for r in eps)[int(0.1 * (len(eps) - 1))], 3)
                if eps
                else None
            ),
        },
        "stop_atr_median": (
            round(statistics.median(r["stop_atr"] for r in eps if r["stop_atr"]), 2)
            if eps
            else None
        ),
        "score_r_spearman": spearman([r["score"] for r in eps], [r["r"] for r in eps]),
        "score_r_spearman_by_strategy": {
            s: spearman(
                [r["score"] for r in eps if r["strategy"] == s],
                [r["r"] for r in eps if r["strategy"] == s],
            )
            for s in sorted({r["strategy"] for r in eps})
        },
    }

    # sensitivity (no selection): stop and target distance +-10 %
    sens = {}
    for name, kw in {
        "baseline": {},
        "stop_x0.9": {"stop_scale": 0.9},
        "stop_x1.1": {"stop_scale": 1.1},
        "target_x0.9": {"target_scale": 0.9},
        "target_x1.1": {"target_scale": 1.1},
    }.items():
        sens[name] = stats([r["r"] for r in run_brackets(first_exec_of_episode, **kw)])
    report["sensitivity_unconstrained_executable"] = sens

    # walk-forward style chronological thirds (no parameter is selected)
    all_days = trading_days(d0, d1, source.holidays())
    n = len(all_days)
    cuts = [all_days[0], all_days[n // 3], all_days[2 * n // 3], all_days[-1] + timedelta(days=1)]
    names = ["train", "validation", "out_of_sample"]

    def third(ts: str) -> str:
        day = datetime.fromisoformat(ts).astimezone(IST).date()
        for i in range(3):
            if cuts[i] <= day < cuts[i + 1]:
                return names[i]
        return "?"

    report["chronological_split"] = {
        "ranges": {names[i]: f"{cuts[i]}..{cuts[i + 1] - timedelta(days=1)}" for i in range(3)},
        "official_trades": group(
            [
                {"r": t["r"], "k": third(t["decision_time"])}
                for t in trades
                if t["r"] is not None and t["decision_time"]
            ],
            lambda r: r["k"],
        ),
        "unconstrained_executable": group(exe, lambda r: third(r["decision"])),
        "unconstrained_all": group(eps, lambda r: third(r["decision"])),
    }
    report["samples"] = {
        "executable_brackets": exe[:200],
        "official_trade_candidates": [
            c
            for c in candidates
            if any(t["decision_time"] == c["key"] and t["symbol"] == c["symbol"] for t in trades)
        ],
    }

    (out_dir / "forensic_audit.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "forensic_audit.md").write_text(to_markdown(report))
    print(f"wrote {out_dir / 'forensic_audit.json'} and forensic_audit.md", flush=True)
    return 0


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 5:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return round(num / den, 3) if den else None


def to_markdown(r: dict[str, Any]) -> str:
    lines = [
        "# Forensic audit output",
        "",
        f"Source: **{r['source']}** (synthetic: {r['synthetic']}), symbols {', '.join(r['symbols'])}, "
        f"{r['start']}..{r['end']}. Universe point-in-time: {r['universe_point_in_time']}.",
        "",
    ]
    for section in (
        "official",
        "instrumented",
        "candidates",
        "unconstrained",
        "sensitivity_unconstrained_executable",
        "chronological_split",
        "data_quality",
    ):
        lines += [
            f"## {section}",
            "",
            "```json",
            json.dumps(
                {k: v for k, v in r[section].items() if k != "trades"}, indent=1, default=str
            )[:20000],
            "```",
            "",
        ]
    lines += [
        "## official trades",
        "",
        "```json",
        json.dumps(r["official"]["trades"], indent=1),
        "```",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
