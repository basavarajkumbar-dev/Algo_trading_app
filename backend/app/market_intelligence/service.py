"""MarketIntelligenceService.

Periodically (every `refresh_seconds`) takes the latest *closed*
candles for every watched symbol from MarketDataService, runs all the
intelligence engines and the regime detector, computes breadth and
relative-strength ranks, caches the results for the API, and
broadcasts updates over the market WebSocket:

- `market_intelligence`: compact market picture + per-symbol summary rows
- `market_regime`: emitted for a symbol when its regime changes
  (and for the benchmark on every refresh)

Only closed candles are analysed, so a result never "repaints" while a
candle is still forming. The analysis runs in a worker thread on a
copy of the candles, so the event loop (and tick processing) isn't
blocked. Nothing here places orders.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

from app.core.logging import get_logger
from app.market_data.base import utcnow
from app.market_data.schemas import Candle, Timeframe
from app.market_data.service import MarketDataService
from app.market_intelligence.analyzer import AnalysisConfig, SymbolAnalysis, analyze_candles
from app.market_intelligence.factors import build_factors
from app.market_intelligence.models import (
    IntelligenceServiceStatus,
    MarketBreadth,
    MarketIntelligenceResponse,
    MarketSnapshot,
    SymbolIntelligence,
    SymbolRegimeSummary,
)
from app.market_intelligence.schemas import Relationship, Trend
from app.regime.config import RegimeConfig
from app.regime.detector import classify_regime
from app.regime.schemas import MarketRegime, RegimeResult

logger = get_logger(__name__)

DEFAULT_HISTORY_CANDLES = 300
_BULLISH = {MarketRegime.STRONG_BULLISH}
_BEARISH = {MarketRegime.STRONG_BEARISH}


@dataclass
class _ComputeInput:
    symbol: str
    candles: list[Candle]
    price: float | None
    change_percent: float | None


@dataclass
class _ComputeOutput:
    analyses: dict[str, SymbolAnalysis]
    regimes: dict[str, RegimeResult]


class MarketIntelligenceService:
    def __init__(
        self,
        market_data: MarketDataService,
        *,
        benchmark_symbol: str,
        timeframe: Timeframe = Timeframe.M5,
        refresh_seconds: float = 5.0,
        history_candles: int = DEFAULT_HISTORY_CANDLES,
        analysis_config: AnalysisConfig | None = None,
        regime_config: RegimeConfig | None = None,
    ) -> None:
        self._market_data = market_data
        self._benchmark = benchmark_symbol
        self._timeframe = timeframe
        self._refresh_seconds = refresh_seconds
        self._history_candles = history_candles
        self._analysis_config = analysis_config or AnalysisConfig()
        self._regime_config = regime_config or RegimeConfig()

        self._names: dict[str, str] = {}
        self._prices: dict[str, tuple[float | None, float | None]] = {}
        self._analyses: dict[str, SymbolAnalysis] = {}
        self._regimes: dict[str, RegimeResult] = {}
        self._rs_ranks: dict[str, int] = {}
        self._snapshot: MarketSnapshot | None = None
        self._summaries: list[SymbolRegimeSummary] = []

        self._task: asyncio.Task[None] | None = None
        self._refresh_count = 0
        self._last_refresh_at: datetime | None = None
        self._last_compute_ms: float | None = None
        self._last_error: str | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            self._names = {i.symbol: i.name for i in await self._market_data.get_instruments()}
        except Exception:  # noqa: BLE001 - names are cosmetic; never block startup on them
            logger.warning("Could not load instrument names for market intelligence")
        await self.refresh()
        self._task = asyncio.create_task(self._run(), name="market-intelligence")
        logger.info(
            "MarketIntelligenceService started (timeframe=%s, every %.1fs)",
            self._timeframe.value,
            self._refresh_seconds,
        )

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
            await asyncio.sleep(self._refresh_seconds)
            await self.refresh()

    # --- computation -------------------------------------------------------

    async def refresh(self) -> None:
        """Recompute everything once. Errors are logged and recorded in
        status; the previous results stay available (and their
        `timestamp` shows how old they are) rather than being dropped."""

        inputs = []
        for symbol in self._market_data.get_watchlist():
            candles = self._market_data.get_candles(
                symbol, self._timeframe, self._history_candles, include_current=False
            )
            snapshot = self._market_data.get_snapshot(symbol)
            inputs.append(
                _ComputeInput(
                    symbol=symbol,
                    candles=list(candles),
                    price=snapshot.price if snapshot else None,
                    change_percent=snapshot.change_percent if snapshot else None,
                )
            )

        started = time.perf_counter()
        try:
            output = await asyncio.to_thread(self._compute, inputs)
        except Exception as exc:  # noqa: BLE001 - keep serving the last good results
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Market intelligence refresh failed")
            return

        previous_regimes = {s: r.regime for s, r in self._regimes.items()}
        self._analyses = output.analyses
        self._regimes = output.regimes
        self._prices = {i.symbol: (i.price, i.change_percent) for i in inputs}
        self._rs_ranks = self._rank_relative_strength()
        self._summaries = self._build_summaries()
        self._snapshot = self._build_snapshot()
        self._last_compute_ms = round((time.perf_counter() - started) * 1000, 1)
        self._last_refresh_at = utcnow()
        self._last_error = None
        self._refresh_count += 1

        await self._broadcast(previous_regimes)

    def _compute(self, inputs: list[_ComputeInput]) -> _ComputeOutput:
        benchmark = next((i.candles for i in inputs if i.symbol == self._benchmark), None)
        analyses: dict[str, SymbolAnalysis] = {}
        regimes: dict[str, RegimeResult] = {}
        now = utcnow()
        for item in inputs:
            analysis = analyze_candles(
                item.symbol,
                self._timeframe,
                item.candles,
                benchmark_candles=benchmark if item.symbol != self._benchmark else None,
                config=self._analysis_config,
            )
            analyses[item.symbol] = analysis
            regimes[item.symbol] = classify_regime(analysis, self._regime_config, now=now)
        return _ComputeOutput(analyses=analyses, regimes=regimes)

    def _rank_relative_strength(self) -> dict[str, int]:
        scored = [
            (symbol, a.relative_strength.relative_strength_score)
            for symbol, a in self._analyses.items()
            if a.relative_strength is not None
        ]
        scored.sort(key=lambda item: -item[1])
        return {symbol: rank for rank, (symbol, _) in enumerate(scored, start=1)}

    def _build_summaries(self) -> list[SymbolRegimeSummary]:
        rows: list[SymbolRegimeSummary] = []
        for symbol, regime in self._regimes.items():
            a = self._analyses[symbol]
            price, change = self._prices.get(symbol, (None, None))
            rows.append(
                SymbolRegimeSummary(
                    symbol=symbol,
                    name=self._names.get(symbol),
                    price=price,
                    change_percent=change,
                    regime=regime.regime,
                    confidence=regime.confidence,
                    top_reason=regime.reasons[0] if regime.reasons else None,
                    trend_quality_score=a.trend_quality.score if a.trend_quality else None,
                    trend_direction=a.trend_quality.direction if a.trend_quality else None,
                    momentum_score=a.momentum.momentum_score if a.momentum else None,
                    relative_strength_score=(
                        a.relative_strength.relative_strength_score if a.relative_strength else None
                    ),
                    relative_strength_rank=self._rs_ranks.get(symbol),
                    price_vs_vwap=a.vwap.price_vs_vwap if a.vwap else None,
                    volatility_level=a.volatility.volatility_level if a.volatility else None,
                    relative_volume=a.volume.relative_volume if a.volume else None,
                    data_as_of=a.data_as_of,
                )
            )
        # Benchmark first, then by relative-strength rank.
        rows.sort(
            key=lambda r: (r.symbol != self._benchmark, r.relative_strength_rank or 10_000, r.symbol)
        )
        return rows

    def _compute_breadth(self) -> MarketBreadth:
        constituents = [s for s in self._summaries if s.symbol != self._benchmark]
        advancing = sum(1 for s in constituents if (s.change_percent or 0) > 0)
        declining = sum(1 for s in constituents if (s.change_percent or 0) < 0)
        above = sum(1 for s in constituents if s.price_vs_vwap == Relationship.ABOVE)
        below = sum(1 for s in constituents if s.price_vs_vwap == Relationship.BELOW)
        with_vwap = above + below + sum(1 for s in constituents if s.price_vs_vwap == Relationship.AT)
        return MarketBreadth(
            symbols_counted=len(constituents),
            advancing=advancing,
            declining=declining,
            unchanged=len(constituents) - advancing - declining,
            above_vwap=above,
            below_vwap=below,
            percent_above_vwap=round(above / with_vwap * 100, 1) if with_vwap else None,
            advance_decline_ratio=round(advancing / declining, 2) if declining else None,
            bullish_regimes=sum(1 for s in constituents if s.regime in _BULLISH),
            bearish_regimes=sum(1 for s in constituents if s.regime in _BEARISH),
        )

    def _build_snapshot(self) -> MarketSnapshot | None:
        regime = self._regimes.get(self._benchmark)
        analysis = self._analyses.get(self._benchmark)
        if regime is None or analysis is None:
            return None

        breadth = self._compute_breadth()
        if regime.regime == MarketRegime.STRONG_BULLISH:
            direction = Trend.UP
        elif regime.regime == MarketRegime.STRONG_BEARISH:
            direction = Trend.DOWN
        elif regime.regime == MarketRegime.BREAKOUT and analysis.range is not None:
            direction = analysis.range.breakout_direction or Trend.SIDEWAYS
        else:
            direction = Trend.SIDEWAYS

        distribution = dict.fromkeys(MarketRegime, 0)
        for summary in self._summaries:
            if summary.symbol != self._benchmark:
                distribution[summary.regime] += 1

        return MarketSnapshot(
            benchmark_symbol=self._benchmark,
            timeframe=self._timeframe,
            market_direction=direction,
            market_regime=regime.regime,
            confidence=regime.confidence,
            volatility=analysis.volatility.volatility_level if analysis.volatility else None,
            trend_strength=analysis.trend_quality.score if analysis.trend_quality else None,
            momentum=analysis.momentum.momentum_score if analysis.momentum else None,
            breadth=breadth,
            regime_distribution=distribution,
            factors=build_factors(analysis, breadth),
            regime=regime,
            timestamp=regime.timestamp,
        )

    async def _broadcast(self, previous_regimes: dict[str, MarketRegime]) -> None:
        manager = self._market_data.connection_manager
        if manager.connection_count == 0:
            return
        try:
            response = self.get_intelligence()
            if response is not None:
                await manager.broadcast(
                    {"type": "market_intelligence", **response.model_dump(mode="json", exclude={"status"})}
                )
            for symbol, regime in self._regimes.items():
                changed = previous_regimes.get(symbol) != regime.regime
                if changed or symbol == self._benchmark:
                    await manager.broadcast(
                        {
                            "type": "market_regime",
                            "symbol": symbol,
                            "regime": regime.regime.value,
                            "previous_regime": (
                                previous_regimes[symbol].value if symbol in previous_regimes else None
                            ),
                            "changed": changed,
                            "confidence": regime.confidence,
                            "reasons": regime.reasons[:3],
                            "timestamp": regime.timestamp.isoformat(),
                        }
                    )
        except Exception:  # noqa: BLE001 - broadcasting must never break the refresh loop
            logger.exception("Failed to broadcast market intelligence")

    # --- read access for the API layer -------------------------------------

    @property
    def benchmark_symbol(self) -> str:
        return self._benchmark

    def status(self) -> IntelligenceServiceStatus:
        return IntelligenceServiceStatus(
            is_running=self._task is not None and not self._task.done(),
            timeframe=self._timeframe,
            refresh_seconds=self._refresh_seconds,
            refresh_count=self._refresh_count,
            last_refresh_at=self._last_refresh_at,
            last_compute_ms=self._last_compute_ms,
            last_error=self._last_error,
            symbols_analyzed=len(self._analyses),
        )

    def get_snapshot(self) -> MarketSnapshot | None:
        return self._snapshot

    def get_summaries(self) -> list[SymbolRegimeSummary]:
        return list(self._summaries)

    def get_intelligence(self) -> MarketIntelligenceResponse | None:
        if self._snapshot is None:
            return None
        return MarketIntelligenceResponse(
            snapshot=self._snapshot, symbols=self.get_summaries(), status=self.status()
        )

    def get_regime(self, symbol: str) -> RegimeResult | None:
        return self._regimes.get(symbol)

    def get_symbol(self, symbol: str) -> SymbolIntelligence | None:
        analysis = self._analyses.get(symbol)
        regime = self._regimes.get(symbol)
        if analysis is None or regime is None:
            return None
        price, change = self._prices.get(symbol, (None, None))
        breadth = self._snapshot.breadth if self._snapshot and symbol == self._benchmark else None
        return SymbolIntelligence(
            symbol=symbol,
            name=self._names.get(symbol),
            price=price,
            change_percent=change,
            regime=regime,
            factors=build_factors(analysis, breadth),
            relative_strength_rank=self._rs_ranks.get(symbol),
            analysis=analysis,
        )

    @property
    def analysis_config(self) -> AnalysisConfig:
        return self._analysis_config

    @property
    def regime_config(self) -> RegimeConfig:
        return self._regime_config
