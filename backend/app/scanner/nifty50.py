"""Nifty50Scanner.

Pipeline (from the brief): NIFTY 50 stocks -> market data -> filter ->
analysis -> scores -> candidates -> ranking.

The analysis is not recomputed here: the scanner reads the latest
strategy contexts (Phase 4 intelligence + Phase 5 price action on the
same candles) and the scored, ranked signals, and lays out one row per
stock with every dimension the scan covers: market context, regime,
60m / 15m / 5m, structure, VWAP, trend, retracement, support /
resistance, price action, volume, momentum, relative strength and
strategy. The NIFTY 50 index itself is market context, not a row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.market_data.service import MarketSnapshot
from app.regime.schemas import MarketRegime
from app.signals.ranking import RANKING_NOTE
from app.signals.schemas import MarketContext, TradeCandidate, TradingSignal
from app.strategies.context import StrategyContext
from app.strategies.router import SymbolStrategyEvaluation

SCANNER_NOTE = (
    "Scanner results describe simulated market data. A rank orders the "
    "current evidence; it is not a forecast and not proof of profitability."
)


class ScanStatus(str, Enum):
    READY = "READY"  # a signal at or above the executable threshold
    WATCH = "WATCH"  # a signal below the threshold but not IGNORE
    IGNORE = "IGNORE"  # a signal scoring in the IGNORE band
    NO_SETUP = "NO_SETUP"  # strategies routed, none eligible
    NO_ROUTE = "NO_ROUTE"  # the regime routes no strategy (e.g. high volatility)
    NO_DATA = "NO_DATA"  # not enough data to analyse yet


class LevelRef(BaseModel):
    label: str
    price: float
    strength: float


class PatternRef(BaseModel):
    pattern: str
    direction: str
    confidence: float


class ScanRow(BaseModel):
    rank: int | None = None
    symbol: str
    name: str | None = None
    price: float | None = None
    change_percent: float | None = None
    status: ScanStatus
    # best signal for the stock, if any
    signal_id: uuid.UUID | None = None
    signal_direction: str | None = None
    signal_strategy: str | None = None
    score: float | None = None
    category: str | None = None
    # analysis
    regime: MarketRegime | None = None
    regime_confidence: float | None = None
    context_direction: str | None = None  # 60m
    context_bias: float | None = None
    setup_direction: str | None = None  # 15m
    setup_bias: float | None = None
    entry_direction: str | None = None  # 5m
    entry_bias: float | None = None
    overall_bias: str | None = None
    alignment_score: float | None = None
    setup_state: str | None = None
    structure_trend: str | None = None
    structure_state: str | None = None
    price_vs_vwap: str | None = None
    trend_quality: float | None = None
    trend_direction: str | None = None
    retracement_direction: str | None = None
    retracement_percentage: float | None = None
    retracement_zone: str | None = None
    nearest_support: LevelRef | None = None
    nearest_resistance: LevelRef | None = None
    patterns: list[PatternRef] = Field(default_factory=list)
    relative_volume: float | None = None
    momentum_score: float | None = None
    relative_strength: float | None = None
    relative_strength_rank: int | None = None
    strategies: list[str] = Field(default_factory=list)  # routed for the regime
    strategy_stages: dict[str, str | None] = Field(default_factory=dict)
    data_as_of: datetime | None = None


class ScanResult(BaseModel):
    market_context: MarketContext
    rows: list[ScanRow]
    candidates: list[TradeCandidate]  # every scored candidate, ranked
    signals: list[TradingSignal] = Field(
        default_factory=list
    )  # this scan's scored signals, in rank order
    top_candidates: list[TradeCandidate]
    status_counts: dict[str, int]
    symbols_scanned: int
    filtered_out: dict[str, str]  # symbol -> reason it was not scanned
    scanned_at: datetime
    # Run record (set by SignalService): the universe it was asked to cover
    # with every count, timing and the strategy versions that ran.
    universe: dict[str, Any] | None = None
    scan_started_at: datetime | None = None
    scan_finished_at: datetime | None = None
    duration_ms: float | None = None
    strategy_versions: dict[str, str] = Field(default_factory=dict)
    note: str = SCANNER_NOTE
    ranking_note: str = RANKING_NOTE


def _row(
    symbol: str,
    ctx: StrategyContext,
    evaluation: SymbolStrategyEvaluation,
    best: TradeCandidate | None,
    snapshot: MarketSnapshot | None,
    name: str | None,
) -> ScanRow:
    a, pa = ctx.analysis, ctx.price_action
    mtf = pa.multi_timeframe
    sr = pa.support_resistance
    r = pa.retracement

    if best is not None:
        status = ScanStatus(best.status)
    elif not evaluation.decision.eligible_strategies:
        status = ScanStatus.NO_ROUTE
    else:
        status = ScanStatus.NO_SETUP

    def level(lvl: object) -> LevelRef | None:
        if lvl is None:
            return None
        return LevelRef(label=lvl.label, price=lvl.price, strength=lvl.strength)  # type: ignore[attr-defined]

    return ScanRow(
        rank=best.rank if best else None,
        symbol=symbol,
        name=name,
        price=snapshot.price if snapshot else ctx.price,
        change_percent=snapshot.change_percent if snapshot else None,
        status=status,
        signal_id=best.signal_id if best else None,
        signal_direction=best.direction.value if best else None,
        signal_strategy=best.strategy.value if best else None,
        score=best.score if best else None,
        category=best.category.value if best else None,
        regime=ctx.regime.regime,
        regime_confidence=ctx.regime.confidence,
        context_direction=(
            mtf.context_analysis.direction.value if mtf and mtf.context_analysis.available else None
        ),
        context_bias=mtf.context_analysis.bias if mtf and mtf.context_analysis.available else None,
        setup_direction=(
            mtf.setup_analysis.direction.value if mtf and mtf.setup_analysis.available else None
        ),
        setup_bias=mtf.setup_analysis.bias if mtf and mtf.setup_analysis.available else None,
        entry_direction=(
            mtf.entry_analysis.direction.value if mtf and mtf.entry_analysis.available else None
        ),
        entry_bias=mtf.entry_analysis.bias if mtf and mtf.entry_analysis.available else None,
        overall_bias=mtf.overall_bias.value if mtf else None,
        alignment_score=mtf.alignment_score if mtf else None,
        setup_state=(
            mtf.setup_analysis.setup_state.value if mtf and mtf.setup_analysis.setup_state else None
        ),
        structure_trend=a.structure.trend.value if a.structure else None,
        structure_state=a.structure.structure_state.value if a.structure else None,
        price_vs_vwap=a.vwap.price_vs_vwap.value if a.vwap else None,
        trend_quality=round(a.trend_quality.score, 1) if a.trend_quality else None,
        trend_direction=a.trend_quality.direction.value if a.trend_quality else None,
        retracement_direction=r.direction.value if r and r.detected and r.direction else None,
        retracement_percentage=r.retracement_percentage if r and r.detected else None,
        retracement_zone=r.zone.value if r and r.detected and r.zone else None,
        nearest_support=level(sr.nearest_support) if sr else None,
        nearest_resistance=level(sr.nearest_resistance) if sr else None,
        patterns=[
            PatternRef(
                pattern=p.pattern.value, direction=p.direction.value, confidence=p.confidence
            )
            for p in pa.patterns
            if p.detected
        ],
        relative_volume=round(a.volume.relative_volume, 2) if a.volume else None,
        momentum_score=round(a.momentum.momentum_score, 1) if a.momentum else None,
        relative_strength=(
            round(a.relative_strength.relative_strength_score, 1) if a.relative_strength else None
        ),
        relative_strength_rank=ctx.relative_strength_rank,
        strategies=[s.value for s in evaluation.decision.eligible_strategies],
        strategy_stages={res.strategy.value: res.stage for res in evaluation.results},
        data_as_of=ctx.latest.timestamp,
    )


class Nifty50Scanner:
    def __init__(self, benchmark_symbol: str) -> None:
        self.benchmark_symbol = benchmark_symbol

    def scan(
        self,
        *,
        watchlist: list[str],
        contexts: dict[str, StrategyContext],
        evaluations: dict[str, SymbolStrategyEvaluation],
        ranked: list[TradeCandidate],
        top: list[TradeCandidate],
        market: MarketContext,
        snapshots: dict[str, MarketSnapshot],
        names: dict[str, str],
        now: datetime,
        signals: list[TradingSignal] | None = None,
    ) -> ScanResult:
        best: dict[str, TradeCandidate] = {}
        for candidate in ranked:  # already in rank order
            best.setdefault(candidate.symbol, candidate)

        rows: list[ScanRow] = []
        filtered: dict[str, str] = {}
        for symbol in watchlist:
            if symbol == self.benchmark_symbol:
                filtered[symbol] = "index: used as market context, not scanned"
                continue
            ctx = contexts.get(symbol)
            evaluation = evaluations.get(symbol)
            if ctx is None or evaluation is None:
                filtered[symbol] = "not enough data yet"
                rows.append(
                    ScanRow(symbol=symbol, name=names.get(symbol), status=ScanStatus.NO_DATA)
                )
                continue
            rows.append(
                _row(
                    symbol,
                    ctx,
                    evaluation,
                    best.get(symbol),
                    snapshots.get(symbol),
                    names.get(symbol),
                )
            )

        rows.sort(
            key=lambda r: (
                r.rank is None,
                r.rank or 0,
                -(r.relative_strength or 0.0),
                r.symbol,
            )
        )
        order = {(c.symbol, c.strategy.value, c.direction.value): c.rank for c in ranked}
        counts: dict[str, int] = {s.value: 0 for s in ScanStatus}
        for row in rows:
            counts[row.status.value] += 1
        return ScanResult(
            market_context=market,
            rows=rows,
            candidates=[c for c in ranked if c.symbol != self.benchmark_symbol],
            signals=sorted(
                (s for s in (signals or []) if s.symbol != self.benchmark_symbol),
                key=lambda s: order.get(s.key, len(order)),
            ),
            top_candidates=[c for c in top if c.symbol != self.benchmark_symbol],
            status_counts=counts,
            symbols_scanned=sum(1 for r in rows if r.status != ScanStatus.NO_DATA),
            filtered_out=filtered,
            scanned_at=now,
        )
