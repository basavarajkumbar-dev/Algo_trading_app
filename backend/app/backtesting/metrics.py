"""Backtest metrics and robustness checks.

Headline metrics reuse the paper-trading analytics (same definitions as
the Analytics page). Robustness checks ask whether the result is likely
to be more than luck and fit:

* trade count and a confidence interval on the mean R multiple
* Monte Carlo resampling of the trade sequence: spread of net P&L and
  maximum drawdown, and the probability the total is negative
* cost stress: the same trades with doubled charges and extra slippage
* concentration: how much of the result comes from the best few trades
* breakdown by regime, strategy and exit reason

Sharpe and Sortino are computed on daily equity returns only when there
are at least MIN_SHARPE_DAYS trading days and the returns vary; otherwise
they are null with the reason. None of these numbers predicts future
performance.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.analytics.performance import GroupStats, PerformanceReport, compute_performance
from app.trading.schemas import PaperTrade, TradeStatus

MIN_SHARPE_DAYS = 20
MIN_TRADES_FOR_CONFIDENCE = 30
BOOTSTRAP_SAMPLES = 1000

BACKTEST_NOTE = (
    "Backtest results describe how these rules behaved on this history under the "
    "stated fill, slippage and cost assumptions. They do not predict or guarantee "
    "future performance."
)


class BacktestMetrics(BaseModel):
    total_trades: int
    wins: int
    losses: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None  # rupees per trade, net
    expectancy_r: float | None  # R per trade
    net_pnl: float
    gross_profit: float
    gross_loss: float
    charges: float
    return_percent: float
    max_drawdown: float
    max_drawdown_percent: float
    average_win: float | None
    average_loss: float | None
    payoff_ratio: float | None  # realised risk/reward: average win / |average loss|
    average_planned_rr: float | None  # the reward:risk the signals planned
    max_consecutive_wins: int
    max_consecutive_losses: int
    sharpe: float | None
    sortino: float | None
    sharpe_note: str
    trading_days: int
    trades_per_day: float | None
    average_holding_minutes: float | None
    exit_reasons: dict[str, int]
    starting_capital: float
    ending_equity: float


class Distribution(BaseModel):
    p5: float
    p50: float
    p95: float


class RobustnessReport(BaseModel):
    trades: int
    enough_trades: bool
    mean_r: float | None
    r_std: float | None
    r_ci95: tuple[float, float] | None
    t_stat: float | None
    mc_net_pnl: Distribution | None
    mc_max_drawdown: Distribution | None
    mc_probability_of_loss: float | None
    net_pnl_double_costs: float | None
    net_pnl_extra_slippage: float | None
    extra_slippage_bps: float
    top3_share_of_profit: float | None  # share of gross profit from the 3 best trades
    net_without_best_trade: float | None
    by_regime: list[GroupStats] = Field(default_factory=list)
    by_strategy: list[GroupStats] = Field(default_factory=list)
    by_exit_reason: list[GroupStats] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)  # plain-English warnings


def closed_trades(trades: list[PaperTrade]) -> list[PaperTrade]:
    return sorted(
        (
            t
            for t in trades
            if t.status == TradeStatus.CLOSED and t.filled_quantity > 0 and t.closed_at
        ),
        key=lambda t: t.closed_at,  # type: ignore[arg-type, return-value]
    )


def _daily_returns(daily_equity: list[tuple[date, float]], start: float) -> list[float]:
    out = []
    prev = start
    for _, eq in daily_equity:
        if prev > 0:
            out.append(eq / prev - 1)
        prev = eq
    return out


def sharpe_sortino(
    daily_equity: list[tuple[date, float]], start: float
) -> tuple[float | None, float | None, str]:
    returns = _daily_returns(daily_equity, start)
    if len(returns) < MIN_SHARPE_DAYS:
        return None, None, f"not computed: {len(returns)} trading days (needs {MIN_SHARPE_DAYS}+)"
    sd = statistics.pstdev(returns)
    if sd == 0:
        return None, None, "not computed: daily returns do not vary"
    mean = statistics.fmean(returns)
    sharpe = mean / sd * math.sqrt(252)
    downside = [min(0.0, r) for r in returns]
    dsd = math.sqrt(sum(d * d for d in downside) / len(downside))
    sortino = mean / dsd * math.sqrt(252) if dsd > 0 else None
    return (
        round(sharpe, 2),
        round(sortino, 2) if sortino is not None else None,
        f"daily returns over {len(returns)} days, annualised with sqrt(252), risk-free rate 0",
    )


def compute_metrics(
    trades: list[PaperTrade],
    daily_equity: list[tuple[date, float]],
    *,
    starting_capital: float,
    planned_rr: dict[str, float] | None = None,
) -> tuple[BacktestMetrics, PerformanceReport]:
    report = compute_performance(trades, starting_capital=starting_capital)
    s = report.summary
    closed = closed_trades(trades)
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    holds = [
        (t.closed_at - t.opened_at).total_seconds() / 60
        for t in closed
        if t.closed_at and t.opened_at
    ]
    exits: dict[str, int] = defaultdict(int)
    for t in closed:
        exits[t.exit_reason.value if t.exit_reason else "UNKNOWN"] += 1
    planned = [planned_rr[str(t.id)] for t in closed if planned_rr and str(t.id) in planned_rr]
    sharpe, sortino, note = sharpe_sortino(daily_equity, starting_capital)
    days = len(daily_equity)
    metrics = BacktestMetrics(
        total_trades=s.closed_trades,
        wins=s.wins,
        losses=s.losses,
        win_rate=s.win_rate,
        profit_factor=s.profit_factor,
        expectancy=s.expectancy,
        expectancy_r=round(statistics.fmean(rs), 3) if rs else None,
        net_pnl=s.net_pnl,
        gross_profit=s.gross_profit,
        gross_loss=s.gross_loss,
        charges=s.charges,
        return_percent=s.return_percent,
        max_drawdown=s.max_drawdown,
        max_drawdown_percent=s.max_drawdown_percent,
        average_win=s.average_win,
        average_loss=s.average_loss,
        payoff_ratio=(
            round(s.average_win / abs(s.average_loss), 2)
            if s.average_win is not None and s.average_loss
            else None
        ),
        average_planned_rr=round(statistics.fmean(planned), 2) if planned else None,
        max_consecutive_wins=s.longest_win_streak,
        max_consecutive_losses=s.longest_loss_streak,
        sharpe=sharpe,
        sortino=sortino,
        sharpe_note=note,
        trading_days=days,
        trades_per_day=round(s.closed_trades / days, 2) if days else None,
        average_holding_minutes=round(statistics.fmean(holds), 1) if holds else None,
        exit_reasons=dict(exits),
        starting_capital=round(starting_capital, 2),
        ending_equity=round(starting_capital + s.net_pnl, 2),
    )
    return metrics, report


def _max_drawdown(pnls: list[float], start: float) -> float:
    equity = peak = start
    worst = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return round(sorted_values[idx], 2)


def _group(key: str, trades: list[PaperTrade]) -> GroupStats:
    s = compute_performance(trades, starting_capital=1.0).summary
    return GroupStats(
        key=key,
        trades=s.closed_trades,
        wins=s.wins,
        losses=s.losses,
        win_rate=s.win_rate,
        net_pnl=s.net_pnl,
        gross_profit=s.gross_profit,
        gross_loss=s.gross_loss,
        profit_factor=s.profit_factor,
        average_r=s.average_r,
    )


def robustness(
    trades: list[PaperTrade],
    *,
    starting_capital: float,
    regime_of: dict[str, str] | None = None,
    extra_slippage_bps: float = 5.0,
    seed: int = 7,
) -> RobustnessReport:
    closed = closed_trades(trades)
    n = len(closed)
    pnls = [t.net_pnl for t in closed]
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    flags: list[str] = []

    mean_r = statistics.fmean(rs) if rs else None
    sd_r = statistics.stdev(rs) if len(rs) > 1 else None
    ci = t_stat = None
    if mean_r is not None and sd_r is not None and sd_r > 0:
        half = 1.96 * sd_r / math.sqrt(len(rs))
        ci = (round(mean_r - half, 3), round(mean_r + half, 3))
        t_stat = round(mean_r / (sd_r / math.sqrt(len(rs))), 2)

    mc_net = mc_dd = None
    p_loss = None
    if n >= 2:
        rng = random.Random(seed)
        nets, dds = [], []
        for _ in range(BOOTSTRAP_SAMPLES):
            sample = [pnls[rng.randrange(n)] for _ in range(n)]
            nets.append(sum(sample))
            dds.append(_max_drawdown(sample, starting_capital))
        nets.sort()
        dds.sort()
        mc_net = Distribution(p5=_pct(nets, 0.05), p50=_pct(nets, 0.5), p95=_pct(nets, 0.95))
        mc_dd = Distribution(p5=_pct(dds, 0.05), p50=_pct(dds, 0.5), p95=_pct(dds, 0.95))
        p_loss = round(sum(1 for x in nets if x < 0) / len(nets), 3)

    net = sum(pnls)
    double_costs = round(net - sum(t.charges for t in closed), 2) if closed else None
    extra_slip = None
    if closed:
        turnover = sum(
            t.filled_quantity * ((t.entry_price or 0) + (t.exit_price or 0)) for t in closed
        )
        extra_slip = round(net - turnover * extra_slippage_bps / 10_000, 2)

    winners = sorted((p for p in pnls if p > 0), reverse=True)
    gross_profit = sum(winners)
    top3 = round(sum(winners[:3]) / gross_profit, 3) if gross_profit > 0 else None
    without_best = round(net - max(pnls), 2) if pnls else None

    by_regime: dict[str, list[PaperTrade]] = defaultdict(list)
    by_strategy: dict[str, list[PaperTrade]] = defaultdict(list)
    by_exit: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in closed:
        by_regime[(regime_of or {}).get(str(t.id), "UNKNOWN")].append(t)
        by_strategy[t.strategy or "UNKNOWN"].append(t)
        by_exit[t.exit_reason.value if t.exit_reason else "UNKNOWN"].append(t)

    if n < MIN_TRADES_FOR_CONFIDENCE:
        flags.append(
            f"Only {n} closed trades: too few to tell skill from luck "
            f"(aim for {MIN_TRADES_FOR_CONFIDENCE}+)."
        )
    if ci is not None and ci[0] <= 0 <= ci[1]:
        flags.append("The 95% interval of the mean R includes zero: no evidence of an edge.")
    if p_loss is not None and p_loss > 0.2:
        flags.append(f"Resampling the trades gives a {p_loss:.0%} chance of a net loss.")
    if double_costs is not None and net > 0 and double_costs <= 0:
        flags.append("The profit disappears when charges are doubled.")
    if extra_slip is not None and net > 0 and extra_slip <= 0:
        flags.append(f"The profit disappears with {extra_slippage_bps:g} bps of extra slippage.")
    if top3 is not None and top3 > 0.5 and n >= 5:
        flags.append(f"{top3:.0%} of the gross profit comes from the 3 best trades.")
    if without_best is not None and net > 0 and without_best <= 0:
        flags.append("Without its single best trade the result is a loss.")

    return RobustnessReport(
        trades=n,
        enough_trades=n >= MIN_TRADES_FOR_CONFIDENCE,
        mean_r=round(mean_r, 3) if mean_r is not None else None,
        r_std=round(sd_r, 3) if sd_r is not None else None,
        r_ci95=ci,
        t_stat=t_stat,
        mc_net_pnl=mc_net,
        mc_max_drawdown=mc_dd,
        mc_probability_of_loss=p_loss,
        net_pnl_double_costs=double_costs,
        net_pnl_extra_slippage=extra_slip,
        extra_slippage_bps=extra_slippage_bps,
        top3_share_of_profit=top3,
        net_without_best_trade=without_best,
        by_regime=sorted((_group(k, v) for k, v in by_regime.items()), key=lambda g: -g.trades),
        by_strategy=sorted((_group(k, v) for k, v in by_strategy.items()), key=lambda g: -g.trades),
        by_exit_reason=sorted((_group(k, v) for k, v in by_exit.items()), key=lambda g: -g.trades),
        flags=flags,
    )


def selection_objective(trades: list[PaperTrade], min_trades: int) -> float | None:
    """What parameter selection maximises: the lower one-standard-error bound
    of the mean R multiple. It rewards consistency and sample size, not the
    largest historical profit. None (ineligible) with too few trades."""

    rs = [t.r_multiple for t in closed_trades(trades) if t.r_multiple is not None]
    if len(rs) < max(min_trades, 2):
        return None
    sd = statistics.stdev(rs)
    return round(statistics.fmean(rs) - sd / math.sqrt(len(rs)), 4)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
