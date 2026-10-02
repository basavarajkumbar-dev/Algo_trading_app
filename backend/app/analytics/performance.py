"""Performance analytics for closed paper trades.

Pure functions over PaperTrade records: no I/O, no trading decisions.
Every figure describes simulated paper results under the paper broker's
fill and cost assumptions; none of it predicts future results.

Definitions (all P&L net of charges):

    win rate       winning trades / closed trades (break-even counts as
                   neither win nor loss in the rate's numerator)
    profit factor  gross profit / |gross loss|; null when there are no
                   losing trades (undefined, not infinite)
    expectancy     average net P&L per closed trade
    equity         starting capital + cumulative net P&L, per trade
    drawdown       equity - running peak equity (<= 0), per trade
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.risk.manager import IST
from app.trading.schemas import PaperTrade, TradeStatus

ANALYTICS_NOTE = (
    "Paper trading results on simulated market data, under configurable fill, "
    "slippage and cost assumptions. Past paper performance does not predict "
    "future results and is not evidence of profitability."
)


class EquityPoint(BaseModel):
    trade_id: str
    symbol: str
    strategy: str | None
    closed_at: datetime
    net_pnl: float
    r_multiple: float | None
    cumulative_pnl: float
    equity: float
    drawdown: float  # <= 0
    drawdown_percent: float  # <= 0, of the running peak


class DailyPnl(BaseModel):
    date: date  # IST trading day
    net_pnl: float
    trades: int
    wins: int
    losses: int


class GroupStats(BaseModel):
    key: str
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    average_r: float | None


class PerformanceSummary(BaseModel):
    closed_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float | None  # percent
    net_pnl: float
    gross_profit: float
    gross_loss: float  # <= 0
    profit_factor: float | None
    charges: float
    average_win: float | None
    average_loss: float | None
    expectancy: float | None
    average_r: float | None
    best_trade: float | None
    worst_trade: float | None
    max_drawdown: float  # <= 0
    max_drawdown_percent: float  # <= 0
    longest_win_streak: int
    longest_loss_streak: int
    starting_capital: float
    ending_equity: float
    return_percent: float


class PerformanceReport(BaseModel):
    summary: PerformanceSummary
    equity_curve: list[EquityPoint]
    daily: list[DailyPnl]
    by_strategy: list[GroupStats]
    by_exit_reason: list[GroupStats]
    other_trades: dict[str, int] = Field(default_factory=dict)  # status -> count, not in the stats
    note: str = ANALYTICS_NOTE


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 2) if denominator else None


def _group(key: str, trades: list[PaperTrade]) -> GroupStats:
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    profit = sum(t.net_pnl for t in wins)
    loss = sum(t.net_pnl for t in losses)
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    return GroupStats(
        key=key,
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / len(trades) * 100, 1) if trades else None,
        net_pnl=round(sum(t.net_pnl for t in trades), 2),
        gross_profit=round(profit, 2),
        gross_loss=round(loss, 2),
        profit_factor=_ratio(profit, abs(loss)),
        average_r=round(sum(rs) / len(rs), 2) if rs else None,
    )


def _streaks(trades: list[PaperTrade]) -> tuple[int, int]:
    best_win = best_loss = win = loss = 0
    for t in trades:
        if t.net_pnl > 0:
            win, loss = win + 1, 0
        elif t.net_pnl < 0:
            win, loss = 0, loss + 1
        else:
            win = loss = 0
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return best_win, best_loss


def compute_performance(trades: list[PaperTrade], *, starting_capital: float) -> PerformanceReport:
    closed = sorted(
        (
            t
            for t in trades
            if t.status == TradeStatus.CLOSED and t.filled_quantity > 0 and t.closed_at
        ),
        key=lambda t: t.closed_at,  # type: ignore[arg-type, return-value]
    )
    closed_ids = {t.id for t in closed}
    other: dict[str, int] = defaultdict(int)
    for t in trades:
        if t.id not in closed_ids:
            other[t.status.value] += 1

    curve: list[EquityPoint] = []
    cumulative = 0.0
    peak = starting_capital
    max_dd = max_dd_pct = 0.0
    for t in closed:
        assert t.closed_at is not None
        cumulative += t.net_pnl
        equity = starting_capital + cumulative
        peak = max(peak, equity)
        dd = equity - peak
        dd_pct = dd / peak * 100 if peak else 0.0
        max_dd, max_dd_pct = min(max_dd, dd), min(max_dd_pct, dd_pct)
        curve.append(
            EquityPoint(
                trade_id=str(t.id),
                symbol=t.symbol,
                strategy=t.strategy,
                closed_at=t.closed_at,
                net_pnl=t.net_pnl,
                r_multiple=t.r_multiple,
                cumulative_pnl=round(cumulative, 2),
                equity=round(equity, 2),
                drawdown=round(dd, 2),
                drawdown_percent=round(dd_pct, 3),
            )
        )

    days: dict[date, list[PaperTrade]] = defaultdict(list)
    for t in closed:
        assert t.closed_at is not None
        days[t.closed_at.astimezone(IST).date()].append(t)
    daily = [
        DailyPnl(
            date=d,
            net_pnl=round(sum(t.net_pnl for t in ts), 2),
            trades=len(ts),
            wins=sum(1 for t in ts if t.net_pnl > 0),
            losses=sum(1 for t in ts if t.net_pnl < 0),
        )
        for d, ts in sorted(days.items())
    ]

    overall = _group("all", closed)
    wins = [t.net_pnl for t in closed if t.net_pnl > 0]
    losses = [t.net_pnl for t in closed if t.net_pnl < 0]
    win_streak, loss_streak = _streaks(closed)
    net = round(cumulative, 2)
    by_strategy: dict[str, list[PaperTrade]] = defaultdict(list)
    by_exit: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in closed:
        by_strategy[t.strategy or "UNKNOWN"].append(t)
        by_exit[t.exit_reason.value if t.exit_reason else "UNKNOWN"].append(t)

    summary = PerformanceSummary(
        closed_trades=len(closed),
        wins=overall.wins,
        losses=overall.losses,
        breakeven=len(closed) - overall.wins - overall.losses,
        win_rate=overall.win_rate,
        net_pnl=net,
        gross_profit=overall.gross_profit,
        gross_loss=overall.gross_loss,
        profit_factor=overall.profit_factor,
        charges=round(sum(t.charges for t in closed), 2),
        average_win=round(sum(wins) / len(wins), 2) if wins else None,
        average_loss=round(sum(losses) / len(losses), 2) if losses else None,
        expectancy=round(net / len(closed), 2) if closed else None,
        average_r=overall.average_r,
        best_trade=max((t.net_pnl for t in closed), default=None),
        worst_trade=min((t.net_pnl for t in closed), default=None),
        max_drawdown=round(max_dd, 2),
        max_drawdown_percent=round(max_dd_pct, 3),
        longest_win_streak=win_streak,
        longest_loss_streak=loss_streak,
        starting_capital=round(starting_capital, 2),
        ending_equity=round(starting_capital + net, 2),
        return_percent=round(net / starting_capital * 100, 3) if starting_capital else 0.0,
    )
    return PerformanceReport(
        summary=summary,
        equity_curve=curve,
        daily=daily,
        by_strategy=sorted((_group(k, v) for k, v in by_strategy.items()), key=lambda g: -g.trades),
        by_exit_reason=sorted((_group(k, v) for k, v in by_exit.items()), key=lambda g: -g.trades),
        other_trades=dict(other),
    )
