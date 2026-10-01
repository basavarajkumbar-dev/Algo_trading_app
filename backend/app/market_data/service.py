"""MarketDataService.

Orchestrates a MarketDataProvider, the tick validator, the candle
builder, and WebSocket broadcast into one background pipeline:

    provider.stream_ticks() -> validate -> candle_builder -> broadcast

This is the "WebSocket foundation for market updates" this phase asks
for. It intentionally does not touch the database (Phase 4 owns
`market_ticks`/`candles` persistence) and never places an order — this
module only ever reads/derives prices.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from app.core.logging import get_logger
from app.market_data.base import MarketDataProvider, MarketDataProviderError, utcnow
from app.market_data.candle_builder import CandleBuilder
from app.market_data.schemas import (
    Candle,
    InstrumentInfo,
    MarketDataProviderStatus,
    Tick,
    Timeframe,
)
from app.market_data.validation import TickValidator
from app.websocket.manager import ConnectionManager

logger = get_logger(__name__)


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    change: float
    change_percent: float
    volume: int
    timestamp: datetime


@dataclass
class MarketDataServiceStats:
    ticks_received: int = 0
    ticks_accepted: int = 0
    ticks_rejected: int = 0
    rejected_by_issue: dict[str, int] = field(default_factory=dict)
    started_at: datetime | None = None


class MarketDataService:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        watchlist: list[str],
        connection_manager: ConnectionManager | None = None,
        validator: TickValidator | None = None,
        candle_builder: CandleBuilder | None = None,
        warmup_candles: int = 0,
        warmup_timeframes: tuple[Timeframe, ...] = tuple(Timeframe),
    ) -> None:
        self._provider = provider
        self._watchlist = list(watchlist)
        self._warmup_candles = warmup_candles
        self._warmup_timeframes = warmup_timeframes
        self._connection_manager = connection_manager or ConnectionManager()
        self._validator = validator or TickValidator()
        self._candle_builder = candle_builder or CandleBuilder()

        self._latest_ticks: dict[str, Tick] = {}
        self._day_open: dict[str, float] = {}
        self._stats = MarketDataServiceStats()
        self._task: asyncio.Task[None] | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return  # already running — idempotent

        await self._provider.connect()
        await self._provider.subscribe(self._watchlist)

        started_at = utcnow()
        for symbol in self._watchlist:
            try:
                ltp = await self._provider.get_ltp(symbol)
            except MarketDataProviderError:
                logger.warning("Could not seed starting price for %s", symbol)
                continue
            self._day_open[symbol] = ltp
            # Seed a starting snapshot from the provider's LTP so every
            # watchlist symbol has a price immediately, rather than only
            # after the (randomly sampled) first tick for it arrives.
            self._latest_ticks.setdefault(
                symbol, Tick(symbol=symbol, price=ltp, volume=0, timestamp=started_at)
            )

        if self._warmup_candles > 0:
            await self._seed_warmup_history()

        self._stats = MarketDataServiceStats(started_at=started_at)
        self._task = asyncio.create_task(self._run(), name="market-data-stream")
        logger.info("MarketDataService started, watching %d symbols", len(self._watchlist))

    async def _seed_warmup_history(self) -> None:
        seeded = 0
        for symbol in self._watchlist:
            for timeframe in self._warmup_timeframes:
                try:
                    history = await self._provider.get_historical_data(
                        symbol, timeframe, self._warmup_candles
                    )
                except MarketDataProviderError:
                    logger.warning("No warm-up history for %s %s", symbol, timeframe.value)
                    continue
                seeded += self._candle_builder.seed_history(symbol, timeframe, history)
        logger.info("Seeded %d warm-up candles from provider history", seeded)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self._provider.disconnect()
        logger.info("MarketDataService stopped")

    async def _run(self) -> None:
        try:
            async for tick in self._provider.stream_ticks():
                await self._process_tick(tick)
        except asyncio.CancelledError:
            raise
        except (
            Exception
        ):  # noqa: BLE001 - the stream must not die silently, but also must not crash the app
            logger.exception("Market data stream terminated unexpectedly")

    async def _process_tick(self, tick: Tick) -> None:
        self._stats.ticks_received += 1
        result = self._validator.validate_and_update(tick)

        if not result.is_valid:
            self._stats.ticks_rejected += 1
            for issue in result.issues:
                self._stats.rejected_by_issue[issue] = (
                    self._stats.rejected_by_issue.get(issue, 0) + 1
                )
            logger.debug("Rejected tick for %s: %s", tick.symbol, result.issues)
            return

        self._stats.ticks_accepted += 1
        self._latest_ticks[tick.symbol] = tick
        self._day_open.setdefault(tick.symbol, tick.price)

        closed_candles = self._candle_builder.process_tick(tick)

        try:
            await self._connection_manager.broadcast(
                {
                    "type": "market_tick",
                    "symbol": tick.symbol,
                    "price": tick.price,
                    "volume": tick.volume,
                    "timestamp": tick.timestamp.isoformat(),
                }
            )
            for candle in closed_candles:
                await self._connection_manager.broadcast(
                    {
                        "type": "candle_closed",
                        "symbol": candle.symbol,
                        "timeframe": candle.timeframe.value,
                        "timestamp": candle.timestamp.isoformat(),
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                    }
                )
        except Exception:  # noqa: BLE001 - a broadcast failure must never kill the stream
            logger.exception("Failed to broadcast market update for %s", tick.symbol)

    # --- read access for the API layer -------------------------------------

    def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        tick = self._latest_ticks.get(symbol)
        if tick is None:
            return None
        open_price = self._day_open.get(symbol, tick.price)
        change = tick.price - open_price
        change_percent = (change / open_price * 100) if open_price else 0.0
        return MarketSnapshot(
            symbol=symbol,
            price=tick.price,
            change=round(change, 2),
            change_percent=round(change_percent, 2),
            volume=tick.volume,
            timestamp=tick.timestamp,
        )

    def get_watchlist(self) -> list[str]:
        return list(self._watchlist)

    async def get_instruments(self) -> list[InstrumentInfo]:
        return await self._provider.get_instruments()

    def get_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 100, include_current: bool = True
    ) -> list[Candle]:
        if include_current:
            return self._candle_builder.get_candles_including_current(symbol, timeframe, limit)
        return self._candle_builder.get_candles(symbol, timeframe, limit)

    async def get_provider_status(self) -> MarketDataProviderStatus:
        return await self._provider.health_check()

    @property
    def stats(self) -> MarketDataServiceStats:
        return self._stats

    @property
    def connection_manager(self) -> ConnectionManager:
        return self._connection_manager

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()
