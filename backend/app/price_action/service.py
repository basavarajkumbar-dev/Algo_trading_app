"""PriceActionService.

Every `refresh_seconds`, takes the latest *closed* candles for each
watched symbol on the configured context / setup / entry timeframes,
runs the price action engine (patterns, swings, retracement,
support/resistance, multi-timeframe) in a worker thread, and caches
the results for the API.

Only closed candles are used, so a detected pattern never appears and
disappears while a candle is still forming. Nothing here places,
proposes or sizes a trade.
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
from app.market_intelligence.analyzer import AnalysisConfig
from app.market_intelligence.schemas import Trend
from app.price_action.analyzer import PriceActionAnalysis, analyze_price_action
from app.price_action.config import PriceActionConfig
from app.price_action.schemas import PATTERN_DISCLAIMER, PatternDirection, PatternType
from app.price_action.support_resistance import PriceLevel
from app.regime.config import RegimeConfig

logger = get_logger(__name__)

HISTORY_CANDLES = 300  # per analysed timeframe
DAY_HISTORY_CANDLES = 500  # setup-timeframe candles used for previous-day levels
CHART_CANDLES = 150  # entry-timeframe candles returned for charting


class DetectedPattern(BaseModel):
    pattern: PatternType
    direction: PatternDirection
    confidence: float
    strength: float


class LevelSummary(BaseModel):
    label: str
    price: float
    strength: float
    distance_atr: float

    @classmethod
    def of(cls, level: PriceLevel | None) -> LevelSummary | None:
        if level is None:
            return None
        return cls(
            label=level.label,
            price=level.price,
            strength=level.strength,
            distance_atr=level.distance_atr,
        )


class PriceActionSummary(BaseModel):
    symbol: str
    name: str | None
    price: float | None
    data_as_of: datetime | None
    patterns: list[DetectedPattern] = Field(default_factory=list)  # on the latest candle
    recent_pattern_count: int = 0
    retracement_direction: Trend | None = None
    retracement_percentage: float | None = None
    retracement_zone: str | None = None
    nearest_support: LevelSummary | None = None
    nearest_resistance: LevelSummary | None = None
    overall_bias: PatternDirection | None = None
    bias_score: float | None = None
    alignment_score: float | None = None
    setup_state: str | None = None
    entry_confirmations: int | None = None


class PriceActionServiceStatus(BaseModel):
    is_running: bool
    context_timeframe: Timeframe
    setup_timeframe: Timeframe
    entry_timeframe: Timeframe
    refresh_seconds: float
    refresh_count: int
    last_refresh_at: datetime | None
    last_compute_ms: float | None
    last_error: str | None
    symbols_analyzed: int


class PriceActionOverview(BaseModel):
    symbols: list[PriceActionSummary]
    status: PriceActionServiceStatus
    note: str = PATTERN_DISCLAIMER


class SymbolPriceAction(BaseModel):
    symbol: str
    name: str | None
    analysis: PriceActionAnalysis
    candles: list[Candle]  # entry-timeframe candles the analysis used (latest CHART_CANDLES)


def summarize(analysis: PriceActionAnalysis, name: str | None) -> PriceActionSummary:
    summary = PriceActionSummary(
        symbol=analysis.symbol,
        name=name,
        price=analysis.price,
        data_as_of=analysis.data_as_of,
        patterns=[
            DetectedPattern(
                pattern=p.pattern,
                direction=p.direction,
                confidence=p.confidence,
                strength=p.strength,
            )
            for p in analysis.patterns
            if p.detected
        ],
        recent_pattern_count=len(analysis.recent_patterns),
    )
    retracement = analysis.retracement
    if retracement is not None and retracement.detected:
        summary.retracement_direction = retracement.direction
        summary.retracement_percentage = retracement.retracement_percentage
        summary.retracement_zone = retracement.zone.value if retracement.zone else None
    sr = analysis.support_resistance
    if sr is not None:
        summary.nearest_support = LevelSummary.of(sr.nearest_support)
        summary.nearest_resistance = LevelSummary.of(sr.nearest_resistance)
    mtf = analysis.multi_timeframe
    if mtf is not None:
        summary.overall_bias = mtf.overall_bias
        summary.bias_score = mtf.bias_score
        summary.alignment_score = mtf.alignment_score
        state = mtf.setup_analysis.setup_state
        summary.setup_state = state.value if state else None
        confirmation = mtf.entry_analysis.confirmation
        summary.entry_confirmations = confirmation.confirmations if confirmation else None
    return summary


class PriceActionService:
    def __init__(
        self,
        market_data: MarketDataService,
        *,
        config: PriceActionConfig | None = None,
        refresh_seconds: float = 15.0,
        analysis_config: AnalysisConfig | None = None,
        regime_config: RegimeConfig | None = None,
    ) -> None:
        self._market_data = market_data
        self._config = config or PriceActionConfig()
        self._refresh_seconds = refresh_seconds
        self._analysis_config = analysis_config or AnalysisConfig()
        self._regime_config = regime_config or RegimeConfig()

        self._names: dict[str, str] = {}
        self._results: dict[str, PriceActionAnalysis] = {}
        self._chart_candles: dict[str, list[Candle]] = {}
        self._summaries: list[PriceActionSummary] = []

        self._task: asyncio.Task[None] | None = None
        self._refresh_count = 0
        self._last_refresh_at: datetime | None = None
        self._last_compute_ms: float | None = None
        self._last_error: str | None = None

    @property
    def config(self) -> PriceActionConfig:
        return self._config

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            self._names = {i.symbol: i.name for i in await self._market_data.get_instruments()}
        except Exception:  # noqa: BLE001 - names are cosmetic; never block startup on them
            logger.warning("Could not load instrument names for price action")
        await self.refresh()
        self._task = asyncio.create_task(self._run(), name="price-action")
        mtf = self._config.multi_timeframe
        logger.info(
            "PriceActionService started (%s/%s/%s, every %.1fs)",
            mtf.context_timeframe.value,
            mtf.setup_timeframe.value,
            mtf.entry_timeframe.value,
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

    def _timeframes(self) -> tuple[Timeframe, ...]:
        mtf = self._config.multi_timeframe
        return (mtf.context_timeframe, mtf.setup_timeframe, mtf.entry_timeframe)

    async def refresh(self) -> None:
        """Recompute every symbol once. On error the previous results
        stay available and the error is reported in status."""

        setup_tf = self._config.multi_timeframe.setup_timeframe
        inputs: list[tuple[str, dict[Timeframe, list[Candle]], list[Candle]]] = []
        for symbol in self._market_data.get_watchlist():
            by_timeframe = {
                tf: list(
                    self._market_data.get_candles(
                        symbol, tf, HISTORY_CANDLES, include_current=False
                    )
                )
                for tf in self._timeframes()
            }
            days = list(
                self._market_data.get_candles(
                    symbol, setup_tf, DAY_HISTORY_CANDLES, include_current=False
                )
            )
            inputs.append((symbol, by_timeframe, days))

        started = time.perf_counter()
        try:
            results = await asyncio.to_thread(self._compute, inputs)
        except Exception as exc:  # noqa: BLE001 - keep serving the last good results
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Price action refresh failed")
            return

        entry_tf = self._config.multi_timeframe.entry_timeframe
        self._results = results
        self._chart_candles = {
            symbol: by_timeframe[entry_tf][-CHART_CANDLES:] for symbol, by_timeframe, _ in inputs
        }
        self._summaries = [summarize(r, self._names.get(s)) for s, r in results.items()]
        self._last_compute_ms = round((time.perf_counter() - started) * 1000, 1)
        self._last_refresh_at = utcnow()
        self._last_error = None
        self._refresh_count += 1

    def _compute(
        self, inputs: list[tuple[str, dict[Timeframe, list[Candle]], list[Candle]]]
    ) -> dict[str, PriceActionAnalysis]:
        now = utcnow()
        return {
            symbol: analyze_price_action(
                symbol,
                by_timeframe,
                day_candles=days,
                config=self._config,
                analysis_config=self._analysis_config,
                regime_config=self._regime_config,
                now=now,
            )
            for symbol, by_timeframe, days in inputs
        }

    # --- read access ---------------------------------------------------------

    def status(self) -> PriceActionServiceStatus:
        mtf = self._config.multi_timeframe
        return PriceActionServiceStatus(
            is_running=self._task is not None and not self._task.done(),
            context_timeframe=mtf.context_timeframe,
            setup_timeframe=mtf.setup_timeframe,
            entry_timeframe=mtf.entry_timeframe,
            refresh_seconds=self._refresh_seconds,
            refresh_count=self._refresh_count,
            last_refresh_at=self._last_refresh_at,
            last_compute_ms=self._last_compute_ms,
            last_error=self._last_error,
            symbols_analyzed=len(self._results),
        )

    def get_overview(self) -> PriceActionOverview | None:
        if not self._results:
            return None
        return PriceActionOverview(symbols=self._summaries, status=self.status())

    def get_symbol(self, symbol: str) -> SymbolPriceAction | None:
        analysis = self._results.get(symbol)
        if analysis is None:
            return None
        return SymbolPriceAction(
            symbol=symbol,
            name=self._names.get(symbol),
            analysis=analysis,
            candles=self._chart_candles.get(symbol, []),
        )
