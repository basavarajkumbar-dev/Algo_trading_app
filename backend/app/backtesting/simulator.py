"""Phase B: execution replay.

Takes the signals Phase A produced and trades them through the SAME
PaperTradingEngine, PaperBrokerAdapter, RiskManager and CostModel the
live paper engine uses, driven by historical bars and a simulated clock.

Timing (no look-ahead):

* A decision is taken at a bar's close, from signals computed on bars
  that had closed by then.
* The entry is a market order with a small execution delay, so it fills
  at the NEXT bar's open (with slippage), never at the close it was
  decided on.
* Inside a bar, prices are fed open -> adverse extreme -> favourable
  extreme -> close for an open position (low before high for a long,
  high before low for a short), passing through the stop and target
  levels where the move crosses them. When a bar's range touches both,
  the stop is assumed to fill first. A stop crossed inside the bar fills
  at its level plus slippage; a gap through a stop fills at the gap
  (open) price.
* Square-off, daily loss limit, max trades, consecutive losses and one
  active trade at a time are enforced by the RiskManager exactly as live.

Trailing stops (optional) are computed from closes that have happened and
applied to later bars: BREAKEVEN moves the stop to the entry price once
the best close is `activation` R in profit; ATR then trails it at
best close -/+ multiple x ATR (ATR at entry).
"""

from __future__ import annotations

import uuid
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.backtesting.calendar import IST
from app.backtesting.data import Universe
from app.backtesting.parameters import TrailingMode
from app.backtesting.replay import sort_key
from app.execution.paper_broker import PaperBrokerAdapter, PaperBrokerConfig
from app.market_data.schemas import Candle
from app.risk.config import RiskLimits
from app.risk.manager import RiskManager
from app.risk.schemas import MarketConditions, TradeIntent, TradeSide
from app.trading.engine import EngineConfig, PaperTradingEngine
from app.trading.schemas import ExitReason, PaperTrade, TradeStatus


class SimClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@dataclass
class ExecutionParams:
    min_score: float = 85.0
    trailing_mode: TrailingMode = TrailingMode.NONE
    trailing_activation_r: float = 1.0
    trailing_atr_multiple: float = 1.5

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> ExecutionParams:
        return cls(
            min_score=float(params.get("min_score", 85.0)),
            trailing_mode=TrailingMode(params.get("trailing_mode", TrailingMode.NONE.value)),
            trailing_activation_r=float(params.get("trailing_activation_r", 1.0)),
            trailing_atr_multiple=float(params.get("trailing_atr_multiple", 1.5)),
        )


@dataclass
class TradeInfo:
    """What the backtest knows about a trade beyond the PaperTrade itself."""

    decision_time: datetime
    regime: str
    version: str
    planned_rr: float
    atr: float
    reasons: list[str]


@dataclass
class SimulationResult:
    trades: list[PaperTrade]  # every attempt the engine recorded (closed, cancelled, rejected)
    info: dict[str, TradeInfo]  # trade id -> extra
    daily_equity: list[tuple[date, float]]
    start_equity: float
    end_equity: float
    decisions: int = 0
    executable_signals: int = 0
    attempts: int = 0
    rejections: Counter[str] = field(default_factory=Counter)
    not_in_universe: int = 0
    trails: int = 0


class Tape:
    """Bars indexed by time once, shared by every simulation of a run."""

    def __init__(self, bars: dict[str, list[Candle]]) -> None:
        self.bars = bars
        self.by_time: dict[datetime, dict[str, Candle]] = {}
        for symbol, series in bars.items():
            for b in series:
                self.by_time.setdefault(b.timestamp, {})[symbol] = b
        self.times = sorted(self.by_time)
        self.days = [t.astimezone(IST).date() for t in self.times]

    def between(self, start: date, end: date) -> list[datetime]:
        lo = bisect_left(self.days, start)
        hi = bisect_right(self.days, end)
        return self.times[lo:hi]


def _path(
    bar: Candle, side: TradeSide | None, levels: list[float] | None = None
) -> list[tuple[timedelta, float]]:
    """Intrabar price path: open -> adverse extreme -> favourable extreme ->
    close for whoever holds the position (low first for a long, high first
    for a short). Working order levels the path crosses are inserted where
    price passes them, so a stop inside the bar triggers at its level
    (plus slippage) rather than at the bar's extreme; a gap beyond a stop
    still triggers on the open."""

    if side == TradeSide.LONG:
        mid = [bar.low, bar.high]
    elif side == TradeSide.SHORT:
        mid = [bar.high, bar.low]
    else:
        mid = [bar.low, bar.high] if bar.close >= bar.open else [bar.high, bar.low]
    anchors = [bar.open, mid[0], mid[1], bar.close]
    points: list[float] = [bar.open]
    for a, b in zip(anchors, anchors[1:], strict=False):
        lo, hi = min(a, b), max(a, b)
        crossed = sorted(
            {lv for lv in (levels or []) if lo < lv < hi},
            reverse=b < a,  # in the order price meets them
        )
        points.extend(crossed)
        points.append(b)
    # spread the points over the bar: first at +1 s, last at +299 s
    step = 298 / max(len(points) - 1, 1)
    return [(timedelta(seconds=1 + round(i * step)), p) for i, p in enumerate(points)]


def simulate(
    *,
    bars: dict[str, list[Candle]],
    signals: dict[str, list[dict[str, Any]]],
    param_index: int,
    execution: ExecutionParams,
    limits: RiskLimits,
    broker_config: PaperBrokerConfig,
    capital: float,
    universe: Universe,
    start: date,
    end: date,
    entry_minutes: int = 5,
    progress: Callable[[float], None] | None = None,
    tape: Tape | None = None,
) -> SimulationResult:
    tape = tape or Tape(bars)
    times = tape.between(start, end)
    if not times:
        return SimulationResult([], {}, [], capital, capital)
    by_time = tape.by_time

    clock = SimClock(times[0] - timedelta(minutes=1))
    broker = PaperBrokerAdapter(
        starting_capital=capital,
        config=broker_config.model_copy(update={"execution_delay_seconds": 0.5}),
        clock=clock,
    )
    # the execution parameter is the executable threshold for this run
    limits = limits.model_copy(update={"min_signal_score": execution.min_score})
    risk = RiskManager(limits, capital=capital, now=clock())
    engine = PaperTradingEngine(
        broker,
        risk,
        config=EngineConfig(entry_timeout_seconds=600.0, history_size=1_000_000),
        clock=clock,
    )
    engine.set_auto_trading(True)
    engine.drain()

    result = SimulationResult([], {}, [], capital, capital)
    episodes: dict[tuple[str, str, str], uuid.UUID] = {}
    tried: set[uuid.UUID] = set()
    record_of: dict[uuid.UUID, dict[str, Any]] = {}
    decision_of: dict[uuid.UUID, datetime] = {}
    best_close: float | None = None
    current_day: date | None = None
    last_equity = capital

    for n, t in enumerate(times):
        day = t.astimezone(IST).date()
        if current_day is not None and day != current_day:
            result.daily_equity.append((current_day, last_equity))
        current_day = day
        row = by_time.get(t, {})

        # 1. prices for whatever the engine has in play
        active = engine.active
        symbols = {o.symbol for o in broker.working_orders()}
        if active is not None:
            symbols.add(active.symbol)
        for symbol in sorted(symbols):
            bar = row.get(symbol)
            if bar is None:
                continue
            side = engine.active.side if engine.active and engine.active.symbol == symbol else None
            levels = [
                lv
                for o in broker.working_orders()
                if o.symbol == symbol
                for lv in (o.price, None if o.triggered else o.trigger_price)
                if lv is not None
            ]
            for offset, price in _path(bar, side, levels):
                clock.now = t + offset
                _run(engine.on_price(symbol, price, clock.now))
        decision_time = t + timedelta(minutes=entry_minutes)
        clock.now = decision_time
        _run(engine.on_clock())

        # 2. trailing stop from closes seen so far
        trade = engine.active
        if trade is not None and trade.status == TradeStatus.OPEN and trade.entry_price:
            bar = row.get(trade.symbol)
            if bar is not None:
                long = trade.side == TradeSide.LONG
                best_close = (
                    bar.close
                    if best_close is None
                    else (max(best_close, bar.close) if long else min(best_close, bar.close))
                )
                if execution.trailing_mode != TrailingMode.NONE:
                    info = result.info.get(str(trade.id))
                    r_unit = abs(trade.entry_price - trade.stop_loss)
                    gained = (best_close - trade.entry_price) * (1 if long else -1)
                    if r_unit > 0 and gained >= execution.trailing_activation_r * r_unit:
                        level = trade.entry_price
                        if execution.trailing_mode == TrailingMode.ATR and info is not None:
                            trail = execution.trailing_atr_multiple * info.atr
                            candidate = best_close - trail if long else best_close + trail
                            level = max(level, candidate) if long else min(level, candidate)
                        if engine.trail_stop(level):
                            result.trails += 1
        else:
            best_close = None

        # 3. decision at this bar's close
        key = decision_time.isoformat()
        records = [r for r in signals.get(key, []) if r["ps"] == param_index]
        current_keys = {(r["symbol"], r["strategy"], r["direction"]) for r in records}
        for k in list(episodes):
            if k not in current_keys:
                del episodes[k]  # the setup disappeared: a later one is a new episode
        for r in records:
            k = (r["symbol"], r["strategy"], r["direction"])
            if k not in episodes:
                episodes[k] = uuid.uuid4()
        if records:
            result.decisions += 1
        if engine.active is None and engine.state.is_resting and records:
            members = universe.members(day)
            ready = []
            for r in sorted(records, key=sort_key):
                if r["score"] < execution.min_score:
                    continue
                result.executable_signals += 1
                if r["symbol"] not in members:
                    result.not_in_universe += 1
                    continue
                sid = episodes[(r["symbol"], r["strategy"], r["direction"])]
                if sid in tried:
                    continue
                ready.append((r, sid))
            if ready:
                r, sid = ready[0]
                tried.add(sid)
                bar = row.get(r["symbol"])
                if bar is not None:
                    clock.now = t + timedelta(seconds=299)
                    _run(engine.on_price(r["symbol"], bar.close, clock.now))
                    clock.now = decision_time
                    intent = TradeIntent(
                        signal_id=sid,
                        symbol=r["symbol"],
                        side=TradeSide.LONG if r["direction"] == "BUY" else TradeSide.SHORT,
                        entry=r["entry"],
                        entry_type=r.get("et", "MARKET"),
                        entry_valid_seconds=(
                            float(r["evb"]) * entry_minutes * 60.0 if r.get("evb") else None
                        ),
                        stop_loss=r["stop"],
                        target=r["target"],
                        score=r["score"],
                        strategy=r["strategy"],
                        signal_last_seen_at=decision_time,
                        signal_active=True,
                    )
                    conditions = MarketConditions(
                        now=decision_time,
                        last_price=bar.close,
                        price_timestamp=t + timedelta(seconds=299),
                        system_healthy=True,
                        health_detail="backtest",
                    )
                    result.attempts += 1
                    decision = _run(engine.execute(intent, conditions))
                    if decision.approved and engine.active is not None:
                        record_of[sid] = r
                        decision_of[sid] = decision_time
                        best_close = None
                        result.info[str(engine.active.id)] = TradeInfo(
                            decision_time=decision_time,
                            regime=r["regime"],
                            version=r.get("version", ""),
                            planned_rr=r["rr"],
                            atr=r["atr"],
                            reasons=r.get("reasons", []),
                        )
                    else:
                        for reason in decision.rejection_reasons:
                            result.rejections[reason.value] += 1
        engine.drain()
        last_equity = broker.balance().equity
        if progress and n % 200 == 0:
            progress(n / len(times))

    # close anything still open at the end of the test
    if engine.active is not None:
        last_bar = bars.get(engine.active.symbol, [])
        price = last_bar[-1].close if last_bar else broker.last_price(engine.active.symbol) or 0.0
        clock.now = clock.now + timedelta(seconds=1)
        if engine.state.name == "POSITION_ACTIVE":
            _run(engine.exit_position(ExitReason.MANUAL))
        clock.now = clock.now + timedelta(seconds=1)
        _run(engine.on_price(engine.active.symbol if engine.active else "", price, clock.now))
    if current_day is not None:
        result.daily_equity.append((current_day, broker.balance().equity))
    result.trades = list(engine.closed)
    result.end_equity = broker.balance().equity
    return result


def _run(coro: Any) -> Any:
    """Drive one of the engine's coroutines to completion without an event
    loop: they never await real I/O (the broker is in-process)."""

    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("engine coroutine awaited real I/O inside the backtest")
