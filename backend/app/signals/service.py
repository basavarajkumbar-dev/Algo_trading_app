"""SignalService: the scan loop that connects everything.

After each strategy refresh (which itself follows each price action
refresh), on one consistent snapshot:

1. SignalEngine          eligible candidates -> scored TradingSignals
2. CandidateRankingEngine signals -> ranked TradeCandidates
3. persistence            stored signals: new / still active / expired
4. Nifty50Scanner         one row per NIFTY 50 stock

If the database is unavailable the scan still runs and the scanner
still works; signals simply are not stored, and the error is reported
in status. Nothing here places or proposes an order to a broker.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.market_data.base import utcnow
from app.market_data.service import MarketDataService, MarketSnapshot
from app.scanner.nifty50 import Nifty50Scanner, ScanResult
from app.signals.config import SignalEngineConfig
from app.signals.engine import SignalEngine, market_context_from
from app.signals.ranking import CandidateRankingEngine
from app.signals.repository import sync_scan
from app.signals.schemas import TradeCandidate, TradingSignal
from app.strategies.service import StrategyService

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


class SignalServiceStatus(BaseModel):
    is_running: bool
    scan_count: int
    last_scan_at: datetime | None
    last_compute_ms: float | None
    last_error: str | None
    persistence_ok: bool
    persistence_error: str | None
    last_persisted: dict[str, int] | None  # created / updated / expired in the last scan
    signals_in_scan: int
    strategy_refresh: int
    executable_threshold: float


class SignalService:
    def __init__(
        self,
        market_data: MarketDataService,
        strategies: StrategyService,
        *,
        benchmark_symbol: str,
        session_factory: SessionFactory | None = None,
        config: SignalEngineConfig | None = None,
        poll_seconds: float = 2.0,
    ) -> None:
        self._market_data = market_data
        self._strategies = strategies
        self._benchmark = benchmark_symbol
        self._session_factory = session_factory
        self.config = config or SignalEngineConfig()
        self.engine = SignalEngine(self.config)
        self.ranking = CandidateRankingEngine(self.config.ranking)
        self.scanner = Nifty50Scanner(benchmark_symbol)
        self._poll_seconds = poll_seconds

        self._names: dict[str, str] = {}
        self._scan: ScanResult | None = None
        self._signals: list[TradingSignal] = []
        self._seen_strategy_refresh = -1

        self._task: asyncio.Task[None] | None = None
        self._scan_count = 0
        self._last_scan_at: datetime | None = None
        self._last_compute_ms: float | None = None
        self._last_error: str | None = None
        self._persistence_error: str | None = None
        self._last_persisted: dict[str, int] | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            self._names = {i.symbol: i.name for i in await self._market_data.get_instruments()}
        except Exception:  # noqa: BLE001 - names are cosmetic
            logger.warning("Could not load instrument names for the scanner")
        await self.refresh()
        self._task = asyncio.create_task(self._run(), name="signals")
        logger.info("SignalService started (follows strategy refreshes)")

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

    # --- scan --------------------------------------------------------------

    async def refresh(self, *, force: bool = False) -> None:
        current = self._strategies.refresh_count
        if not force and current == self._seen_strategy_refresh:
            return
        contexts, evaluations = self._strategies.get_state()
        if not contexts:
            return
        watchlist = list(self._market_data.get_watchlist())
        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in watchlist:
            snapshot = self._market_data.get_snapshot(symbol)
            if snapshot is not None:
                snapshots[symbol] = snapshot

        started = time.perf_counter()
        now = utcnow()
        try:
            benchmark_snapshot = snapshots.get(self._benchmark)
            market = market_context_from(
                contexts,
                self._benchmark,
                benchmark_snapshot.change_percent if benchmark_snapshot else None,
            )
            signals = await asyncio.to_thread(
                self.engine.build, evaluations, contexts, market, now=now
            )
            # The index is market context, not a tradable candidate here.
            signals = [s for s in signals if s.symbol != self._benchmark]
            ranked = self.ranking.rank(signals)
        except Exception as exc:  # noqa: BLE001 - keep the last good scan
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Signal scan failed")
            return

        ranked = await self._persist(signals, ranked, now)
        top = self.ranking.top(ranked)
        self._scan = self.scanner.scan(
            watchlist=watchlist,
            contexts=contexts,
            evaluations=evaluations,
            ranked=ranked,
            top=top,
            market=market,
            snapshots=snapshots,
            names=self._names,
            now=now,
            signals=signals,
        )
        self._signals = signals
        self._seen_strategy_refresh = current
        self._last_compute_ms = round((time.perf_counter() - started) * 1000, 1)
        self._last_scan_at = now
        self._last_error = None
        self._scan_count += 1

    async def _persist(
        self, signals: list[TradingSignal], ranked: list[TradeCandidate], now: datetime
    ) -> list[TradeCandidate]:
        if self._session_factory is None:
            self._persistence_error = "No database session configured"
            return ranked
        ranks = {(c.symbol, c.strategy.value, c.direction.value): c.rank for c in ranked}
        try:
            async with self._session_factory() as session:
                result = await sync_scan(
                    session,
                    signals,
                    now=now,
                    store_min_score=self.config.store_min_score,
                    ranks=ranks,
                )
        except Exception as exc:  # noqa: BLE001 - the scanner must keep working without a database
            self._persistence_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Signals not stored: %s", self._persistence_error)
            return ranked
        self._persistence_error = None
        self._last_persisted = {
            "created": result.created,
            "updated": result.updated,
            "expired": result.expired,
        }
        for signal in signals:
            signal.id = result.ids.get(signal.key)
        return [
            c.model_copy(
                update={
                    "signal_id": result.ids.get((c.symbol, c.strategy.value, c.direction.value))
                }
            )
            for c in ranked
        ]

    # --- read access -------------------------------------------------------

    def status(self) -> SignalServiceStatus:
        return SignalServiceStatus(
            is_running=self._task is not None and not self._task.done(),
            scan_count=self._scan_count,
            last_scan_at=self._last_scan_at,
            last_compute_ms=self._last_compute_ms,
            last_error=self._last_error,
            persistence_ok=self._persistence_error is None and self._last_persisted is not None,
            persistence_error=self._persistence_error,
            last_persisted=self._last_persisted,
            signals_in_scan=len(self._signals),
            strategy_refresh=self._seen_strategy_refresh,
            executable_threshold=self.config.categories.executable_threshold,
        )

    def get_scan(self) -> ScanResult | None:
        return self._scan

    def current_signals(self) -> list[TradingSignal]:
        return list(self._signals)
