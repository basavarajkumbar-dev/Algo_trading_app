"""Relative strength engine.

Compares a symbol's return with a benchmark's (normally the NIFTY 50
index) over the same time window. Candles are aligned by timestamp, so
a gap in either series can't silently compare different periods. A
positive relative return means the symbol outperformed over that
window; it says nothing about whether it will keep doing so.
"""

from __future__ import annotations

from datetime import datetime

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError
from app.market_intelligence.utils import percent_change, sigmoid_score

DEFAULT_LOOKBACK = 20
#: Score steepness per percentage point of relative return
#: (1pp outperformance -> ~73, 2pp -> ~88).
DEFAULT_STEEPNESS = 1.0


class RelativeStrengthResult(EngineResult):
    lookback: int
    window_start: datetime
    window_end: datetime
    symbol_return_percent: float
    benchmark_return_percent: float
    relative_return_percent: float
    relative_strength_score: float  # 0-100, 50 = in line with the benchmark
    is_outperforming: bool


def _close_at_or_before(candles: list[Candle], timestamp: datetime) -> float | None:
    for candle in reversed(candles):
        if candle.timestamp <= timestamp:
            return candle.close
    return None


def analyze_relative_strength(
    symbol_candles: list[Candle],
    benchmark_candles: list[Candle],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    steepness: float = DEFAULT_STEEPNESS,
) -> RelativeStrengthResult:
    if len(symbol_candles) < 2 or len(benchmark_candles) < 2:
        raise InsufficientDataError("Relative strength needs at least 2 candles for each series.")

    window = symbol_candles[-(lookback + 1) :]
    start, end = window[0], window[-1]
    bench_start = _close_at_or_before(benchmark_candles, start.timestamp)
    bench_end = _close_at_or_before(benchmark_candles, end.timestamp)
    if bench_start is None or bench_end is None:
        raise InsufficientDataError("Benchmark has no candles covering the symbol's window.")

    symbol_return = percent_change(end.close, start.close) or 0.0
    benchmark_return = percent_change(bench_end, bench_start) or 0.0
    relative = symbol_return - benchmark_return
    score = round(sigmoid_score(relative, steepness), 2)
    outperforming = relative > 0

    periods = len(window) - 1
    verb = "Outperforming" if outperforming else "Underperforming"
    reasons = [
        f"{verb} benchmark by {abs(relative):.2f}pp over {periods} candles "
        f"({symbol_return:+.2f}% vs {benchmark_return:+.2f}%)"
    ]

    return RelativeStrengthResult(
        lookback=periods,
        window_start=start.timestamp,
        window_end=end.timestamp,
        symbol_return_percent=round(symbol_return, 4),
        benchmark_return_percent=round(benchmark_return, 4),
        relative_return_percent=round(relative, 4),
        relative_strength_score=score,
        is_outperforming=outperforming,
        reasons=reasons,
    )
