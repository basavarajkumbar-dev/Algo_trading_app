"""MarketDataService.

Orchestrates a MarketDataProvider, the tick validator, the candle
builder, and WebSocket broadcast into one background pipeline:

    provider.stream_ticks() -> validate -> candle_builder -> broadcast

This is the "WebSocket foundation for market updates" this phase asks
for. It never places an order — this module only ever reads/derives prices.

Phase 13 (real data):
  * source / simulated: where the data comes from (MOCK or ZERODHA), so
    nothing simulated is ever presented as real.
  * readiness: a broker feed is not ready until a Kite login; warm-up
    history is (re)loaded each time the feed becomes ready after a login,
    in the background for rate-limited REST history.
  * finalize loop: once a second, candles whose period has ended close
    even if the instrument did not tick again.
  * freshness: `data_status()` is LIVE / STALE / WAITING / DISCONNECTED
    against DATA_STALE_THRESHOLD_SECONDS; `is_stale(symbol)` is what the
    signal and risk layers use to refuse executable signals and orders.
  * listeners: closed candles go to registered sinks (Redis state, the
    candle store) without the engines knowing about them.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.market_data.base import MarketDataProvider, MarketDataProviderError, utcnow
from app.market_data.candle_builder import NSE_SESSION, CandleBuilder, aggregate_candles
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


CandleListener = Callable[[list[Candle]], Awaitable[None] | None]


class DataStatus:
    LIVE = "LIVE"
    STALE = "STALE"
    WAITING = "WAITING"  # connected / starting, no tick yet
    DISCONNECTED = "DISCONNECTED"  # broker feed not ready (no login, socket down)


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
        session_minutes: int = 375,
        stale_threshold_seconds: float = 10.0,
        finalize_interval_seconds: float = 1.0,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._clock = clock
        self._provider = provider
        self._watchlist = list(watchlist)
        self._warmup_candles = warmup_candles
        self._warmup_timeframes = warmup_timeframes
        self._session_minutes = session_minutes
        self._connection_manager = connection_manager or ConnectionManager()
        self._validator = validator or TickValidator(clock=clock)
        # Real NSE data: candles anchored to the 09:15 IST session open and
        # clipped at 15:30, like Zerodha's own. The 24/7 simulator stays
        # hour-aligned.
        self._session = NSE_SESSION if provider.source == "ZERODHA" else None
        self._candle_builder = candle_builder or CandleBuilder(session=self._session)
        self.stale_threshold = timedelta(seconds=stale_threshold_seconds)
        self._finalize_interval = finalize_interval_seconds
        self._candle_listeners: list[CandleListener] = []
        self.last_tick_at: datetime | None = None
        self._symbol_tick_at: dict[str, datetime] = {}
        self._warmed_generation = 0
        self.warmup_in_progress = False
        self.last_warmup_at: datetime | None = None
        self._aux_tasks: list[asyncio.Task[None]] = []

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

        started_at = self._clock()
        if self._provider.is_ready() and not self._provider.background_warmup:
            await self._seed_from_provider(started_at)
            self._warmed_generation = self._provider.ready_generation

        self._stats = MarketDataServiceStats(started_at=started_at)
        self._task = asyncio.create_task(self._run(), name="market-data-stream")
        self._aux_tasks = [
            asyncio.create_task(self._readiness_loop(), name="market-data-readiness"),
            asyncio.create_task(self._finalize_loop(), name="market-data-finalize"),
        ]
        logger.info(
            "MarketDataService started (%s), watching %d symbols",
            self._provider.source,
            len(self._watchlist),
        )

    async def _seed_from_provider(self, started_at: datetime) -> None:
        """Starting prices and warm-up history from the provider."""

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
        self.last_warmup_at = self._clock()

    async def _readiness_loop(self) -> None:
        """(Re)load starting prices and warm-up history each time the feed
        becomes ready after a login (a broker feed: once per trading day)."""

        while True:
            try:
                generation = await self._provider.wait_ready(self._warmed_generation)
                self._warmed_generation = generation
                self.warmup_in_progress = True
                logger.info("Market data feed ready (generation %d): loading warm-up", generation)
                await self._seed_from_provider(self._clock())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - retried on the next ready transition
                logger.exception("Warm-up after the feed became ready failed")
            finally:
                self.warmup_in_progress = False

    async def _finalize_loop(self) -> None:
        """Close candles whose period has ended even without a later tick."""

        while True:
            await asyncio.sleep(self._finalize_interval)
            try:
                closed = self._candle_builder.finalize_due(self._clock())
                if closed:
                    await self._emit_closed(closed)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the loop die
                logger.exception("Candle finalize failed")

    async def _seed_warmup_history(self) -> None:
        """Seed every timeframe with simulated closed-candle history.

        Timeframes that are whole multiples of 5m (5m/15m/30m/60m) are
        all aggregated from one 5m path per symbol, so they describe the
        same simulated price history: a 60m trend, the 15m pullback inside
        it and the 5m candles at the end are mutually consistent, and
        previous-day levels agree with intraday ones. The 5m path is long
        enough to give `warmup_candles` candles on the largest of those
        timeframes. 1m and 3m cannot be built from 5m and get their own
        shorter histories.
        """

        base = Timeframe.M5
        aggregated = [
            tf
            for tf in self._warmup_timeframes
            if tf.minutes >= base.minutes and tf.minutes % base.minutes == 0
        ]
        independent = [tf for tf in self._warmup_timeframes if tf not in aggregated]
        base_count = (
            self._warmup_candles * max(tf.minutes for tf in aggregated) // base.minutes
            if aggregated
            else 0
        )

        seeded = 0
        for symbol in self._watchlist:
            if aggregated:
                try:
                    base_history = await self._provider.get_historical_data(
                        symbol, base, base_count
                    )
                except MarketDataProviderError:
                    logger.warning("No warm-up history for %s", symbol)
                    base_history = []
                for timeframe in aggregated:
                    history = (
                        base_history
                        if timeframe == base
                        else aggregate_candles(base_history, timeframe, self._session)
                    )
                    seeded += self._candle_builder.seed_history(symbol, timeframe, history)

            for timeframe in independent:
                try:
                    history = await self._provider.get_historical_data(
                        symbol, timeframe, self._warmup_candles
                    )
                except MarketDataProviderError:
                    logger.warning("No warm-up history for %s %s", symbol, timeframe.value)
                    continue
                seeded += self._candle_builder.seed_history(symbol, timeframe, history)
                if timeframe == Timeframe.M1 and self._session is not None:
                    # real data: complete the in-progress higher candles
                    self._candle_builder.seed_partial(symbol, history, self._clock())

            # With warm-up history available, measure change from a
            # simulated session open (one NSE session, 375 minutes, ago)
            # instead of from the moment the app started. The simulated
            # feed has no real sessions; this keeps "change" and the
            # session-anchored VWAP on the same reference window.
            if self._session is not None:
                # Real data: change is measured from today's session open
                # (or the last session's, before today's first candle).
                recent = self._candle_builder.get_candles(symbol, Timeframe.M5, 80)
                if recent:
                    zone = self._session.zone
                    last_day = recent[-1].timestamp.astimezone(zone).date()
                    today = [c for c in recent if c.timestamp.astimezone(zone).date() == last_day]
                    self._day_open[symbol] = today[0].open
                continue
            session = self._candle_builder.get_candles(
                symbol, Timeframe.M5, max(1, self._session_minutes // 5)
            )
            if session:
                self._day_open[symbol] = session[0].open
        logger.info("Seeded %d warm-up candles from provider history", seeded)

    async def stop(self) -> None:
        for task in self._aux_tasks:
            task.cancel()
        for task in self._aux_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._aux_tasks = []
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
        now = self._clock()
        self.last_tick_at = now
        self._symbol_tick_at[tick.symbol] = now

        closed_candles = self._candle_builder.process_tick(tick)

        try:
            await self._connection_manager.broadcast(
                {
                    "type": "market_tick",
                    "symbol": tick.symbol,
                    "price": tick.price,
                    "volume": tick.volume,
                    "timestamp": tick.timestamp.isoformat(),
                    "source": self._provider.source,
                }
            )
        except Exception:  # noqa: BLE001 - a broadcast failure must never kill the stream
            logger.exception("Failed to broadcast market update for %s", tick.symbol)
        if closed_candles:
            await self._emit_closed(closed_candles)

    def add_candle_listener(self, listener: CandleListener) -> None:
        self._candle_listeners.append(listener)

    async def _emit_closed(self, closed_candles: list[Candle]) -> None:
        for candle in closed_candles:
            if candle.timeframe in (Timeframe.M5, Timeframe.M15, Timeframe.M60):
                logger.debug(
                    "CANDLE_CLOSED %s %s %s",
                    candle.symbol,
                    candle.timeframe.value,
                    candle.timestamp.isoformat(),
                )
        for listener in list(self._candle_listeners):
            try:
                out = listener(closed_candles)
                if inspect.isawaitable(out):
                    await out
            except Exception:  # noqa: BLE001 - a sink failure must not stop candles
                logger.exception("Candle listener failed")
        try:
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
                        "source": self._provider.source,
                    }
                )
        except Exception:  # noqa: BLE001 - a broadcast failure must never kill the stream
            logger.exception("Failed to broadcast closed candles")

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

    # --- source and freshness ------------------------------------------------

    @property
    def source(self) -> str:
        return self._provider.source

    @property
    def simulated(self) -> bool:
        return self._provider.simulated

    @property
    def provider(self) -> MarketDataProvider:
        return self._provider

    @property
    def candle_builder(self) -> CandleBuilder:
        return self._candle_builder

    def data_status(self, now: datetime | None = None) -> str:
        if not self._provider.is_ready():
            return DataStatus.DISCONNECTED
        if self.last_tick_at is None:
            return DataStatus.WAITING
        now = now or self._clock()
        if now - self.last_tick_at > self.stale_threshold:
            return DataStatus.STALE
        return DataStatus.LIVE

    def is_stale(self, symbol: str | None = None, now: datetime | None = None) -> bool:
        """True when data is too old to act on: the feed is not LIVE or, for
        real data, this symbol has not ticked within the threshold. (The
        simulator ticks a random third of the symbols each second, so for
        it only feed-level freshness applies.)"""

        now = now or self._clock()
        status = self.data_status(now)
        if self._provider.simulated:
            # The simulator's warm-up history is its data until it ticks;
            # only a feed that went quiet (or stopped) is stale.
            return status in (DataStatus.STALE, DataStatus.DISCONNECTED)
        if status != DataStatus.LIVE:
            return True
        if symbol is None:
            return False
        seen = self._symbol_tick_at.get(symbol)
        return seen is None or now - seen > self.stale_threshold

    def data_age_seconds(
        self, symbol: str | None = None, now: datetime | None = None
    ) -> float | None:
        seen = self.last_tick_at if symbol is None else self._symbol_tick_at.get(symbol)
        if seen is None:
            return None
        return max(((now or self._clock()) - seen).total_seconds(), 0.0)

    def candle_status(self, symbol: str, timeframe: Timeframe, now: datetime | None = None) -> str:
        """FORMING (current candle ticking), CLOSED (last candle complete,
        none forming) or NONE."""

        builder = self._candle_builder
        if builder.get_current_candle(symbol, timeframe) is not None:
            return "FORMING"
        minute = builder.get_current_candle(symbol, Timeframe.M1)
        if minute is not None:
            return "FORMING"  # its first minute is ticking; it aggregates when that closes
        if self._candle_builder.last_closed(symbol, timeframe) is not None:
            return "CLOSED"
        return "NONE"

    @property
    def stats(self) -> MarketDataServiceStats:
        return self._stats

    @property
    def connection_manager(self) -> ConnectionManager:
        return self._connection_manager

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()
