"""StrategyService.

After each price action refresh, evaluates every watched symbol: builds
a StrategyContext from the same candles the price action engine used,
asks the StrategyRouter which strategies the symbol's regime allows,
runs those, and caches the results for the API.

This service only produces candidates for inspection. It has no
dependency on, and no path to, any broker or order code: candidates
would go to the Signal and Risk engines (later phases), never directly
to execution.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.market_data.base import utcnow
from app.market_data.schemas import Candle, Timeframe
from app.market_data.service import MarketDataService
from app.price_action.analyzer import PriceActionAnalysis
from app.price_action.service import PriceActionService
from app.regime.schemas import MarketRegime
from app.strategies.config import StrategyEngineConfig
from app.strategies.context import StrategyContext, build_context
from app.strategies.router import StrategyRouter, SymbolStrategyEvaluation
from app.strategies.schemas import (
    CANDIDATE_NOTE,
    StrategyName,
    StrategyResult,
    TradeDirection,
)

logger = get_logger(__name__)


class CandidateSummary(BaseModel):
    strategy: StrategyName
    direction: TradeDirection
    entry_candidate: float | None
    stop_candidate: float | None
    target_candidate: float | None
    reward_risk: float | None
    confidence: float


class StrategySummary(BaseModel):
    symbol: str
    name: str | None
    price: float | None
    regime: MarketRegime
    regime_confidence: float
    eligible_strategies: list[StrategyName]
    candidates: list[CandidateSummary] = Field(default_factory=list)
    stages: dict[str, str | None] = Field(default_factory=dict)  # strategy -> furthest stage
    data_as_of: datetime | None


class StrategyServiceStatus(BaseModel):
    is_running: bool
    refresh_count: int
    last_refresh_at: datetime | None
    last_compute_ms: float | None
    last_error: str | None
    symbols_evaluated: int
    candidates: int
    price_action_refresh: int


class StrategyOverview(BaseModel):
    symbols: list[StrategySummary]
    status: StrategyServiceStatus
    note: str = CANDIDATE_NOTE


class SymbolStrategies(BaseModel):
    symbol: str
    name: str | None
    evaluation: SymbolStrategyEvaluation
    unrouted: list[StrategyResult] | None = (
        None  # every strategy, ignoring the router (research view)
    )


def _summarize(
    ev: SymbolStrategyEvaluation, ctx: StrategyContext, name: str | None
) -> StrategySummary:
    return StrategySummary(
        symbol=ev.symbol,
        name=name,
        price=ctx.price,
        regime=ev.decision.regime,
        regime_confidence=ev.decision.regime_confidence,
        eligible_strategies=ev.decision.eligible_strategies,
        candidates=[
            CandidateSummary(
                strategy=r.strategy,
                direction=r.direction,
                entry_candidate=r.entry_candidate,
                stop_candidate=r.stop_candidate,
                target_candidate=r.target_candidate,
                reward_risk=r.reward_risk,
                confidence=r.confidence,
            )
            for r in ev.candidates
        ],
        stages={r.strategy.value: r.stage for r in ev.results},
        data_as_of=ev.data_as_of,
    )


class StrategyService:
    def __init__(
        self,
        market_data: MarketDataService,
        price_action: PriceActionService,
        *,
        benchmark_symbol: str,
        config: StrategyEngineConfig | None = None,
        poll_seconds: float = 2.0,
    ) -> None:
        self._market_data = market_data
        self._price_action = price_action
        self._benchmark = benchmark_symbol
        self._router = StrategyRouter(config)
        self._poll_seconds = poll_seconds

        self._names: dict[str, str] = {}
        self._contexts: dict[str, StrategyContext] = {}
        self._evaluations: dict[str, SymbolStrategyEvaluation] = {}
        self._summaries: list[StrategySummary] = []
        self._seen_price_action_refresh = -1

        self._task: asyncio.Task[None] | None = None
        self._refresh_count = 0
        self._last_refresh_at: datetime | None = None
        self._last_compute_ms: float | None = None
        self._last_error: str | None = None

    @property
    def config(self) -> StrategyEngineConfig:
        return self._router.config

    @property
    def router(self) -> StrategyRouter:
        return self._router

    @property
    def refresh_count(self) -> int:
        return self._refresh_count

    def get_state(
        self,
    ) -> tuple[dict[str, StrategyContext], dict[str, SymbolStrategyEvaluation]]:
        """Contexts and evaluations from the latest refresh (one snapshot)."""

        return self._contexts, self._evaluations

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            self._names = {i.symbol: i.name for i in await self._market_data.get_instruments()}
        except Exception:  # noqa: BLE001 - names are cosmetic
            logger.warning("Could not load instrument names for strategies")
        await self.refresh()
        self._task = asyncio.create_task(self._run(), name="strategies")
        logger.info("StrategyService started (follows price action refreshes)")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            await self.refresh()

    # --- computation -------------------------------------------------------

    async def refresh(self, *, force: bool = False) -> None:
        """Re-evaluate when the price action service has new results."""

        current = self._price_action.refresh_count
        if not force and current == self._seen_price_action_refresh:
            return
        snapshots: dict[str, tuple[dict[Timeframe, list[Candle]], PriceActionAnalysis]] = {}
        for symbol in self._market_data.get_watchlist():
            snapshot = self._price_action.get_snapshot(symbol)
            if snapshot is not None:
                snapshots[symbol] = snapshot
        if not snapshots:
            return

        started = time.perf_counter()
        try:
            contexts, evaluations = await asyncio.to_thread(self._compute, snapshots)
        except Exception as exc:  # noqa: BLE001 - keep serving the last good results
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Strategy refresh failed")
            return

        self._contexts = contexts
        self._evaluations = evaluations
        self._summaries = [
            _summarize(ev, contexts[s], self._names.get(s)) for s, ev in evaluations.items()
        ]
        self._seen_price_action_refresh = current
        self._last_compute_ms = round((time.perf_counter() - started) * 1000, 1)
        self._last_refresh_at = utcnow()
        self._last_error = None
        self._refresh_count += 1

    def _compute(
        self, snapshots: dict[str, tuple[dict[Timeframe, list[Candle]], PriceActionAnalysis]]
    ) -> tuple[dict[str, StrategyContext], dict[str, SymbolStrategyEvaluation]]:
        now = utcnow()
        entry_tf = self._price_action.config.multi_timeframe.entry_timeframe
        benchmark = snapshots.get(self._benchmark)
        benchmark_candles = benchmark[0].get(entry_tf) if benchmark else None

        contexts: dict[str, StrategyContext] = {}
        for symbol, (by_timeframe, analysis) in snapshots.items():
            ctx = build_context(
                symbol,
                by_timeframe,
                benchmark_candles=benchmark_candles if symbol != self._benchmark else None,
                price_action=analysis,
                price_action_config=self._price_action.config,
                now=now,
            )
            if ctx is not None:
                contexts[symbol] = ctx

        scored = sorted(
            (
                (s, c.analysis.relative_strength.relative_strength_score)
                for s, c in contexts.items()
                if c.analysis.relative_strength is not None
            ),
            key=lambda item: -item[1],
        )
        for rank, (symbol, _) in enumerate(scored, start=1):
            contexts[symbol].relative_strength_rank = rank
            contexts[symbol].universe_size = len(scored)

        evaluations = {s: self._router.evaluate(c) for s, c in contexts.items()}
        return contexts, evaluations

    # --- read access ---------------------------------------------------------

    def status(self) -> StrategyServiceStatus:
        return StrategyServiceStatus(
            is_running=self._task is not None and not self._task.done(),
            refresh_count=self._refresh_count,
            last_refresh_at=self._last_refresh_at,
            last_compute_ms=self._last_compute_ms,
            last_error=self._last_error,
            symbols_evaluated=len(self._evaluations),
            candidates=sum(len(ev.candidates) for ev in self._evaluations.values()),
            price_action_refresh=self._seen_price_action_refresh,
        )

    def get_overview(self) -> StrategyOverview | None:
        if not self._evaluations:
            return None
        return StrategyOverview(symbols=self._summaries, status=self.status())

    def get_symbol(self, symbol: str, *, include_unrouted: bool = False) -> SymbolStrategies | None:
        evaluation = self._evaluations.get(symbol)
        ctx = self._contexts.get(symbol)
        if evaluation is None or ctx is None:
            return None
        return SymbolStrategies(
            symbol=symbol,
            name=self._names.get(symbol),
            evaluation=evaluation,
            unrouted=self._router.evaluate_all(ctx) if include_unrouted else None,
        )
