"""ZerodhaMarketDataProvider: real NSE data from Kite Connect (Phase 13).

    Kite WebSocket (KiteTickerClient)
      -> SubscriptionManager      instrument tokens resolved from the
                                  instrument master (never hardcoded)
      -> TickProcessor            validation, normalization, timestamps, dedup
      -> NormalizedTick listeners (Redis market state, optional raw-tick store)
      -> Tick queue -> MarketDataService -> CandleBuilder -> engines

This provider only READS market data. It never places, modifies or cancels
an order: the REST client it shares is created with orders disabled unless
live execution is explicitly enabled, and even then only OrderManager can
reach the order adapter.

Readiness: the feed needs a Kite login. Before one, the provider is
"WAITING_FOR_LOGIN" and serves nothing (no simulated fallback: simulated
data must never look like real data). After a login it syncs the
instrument master if needed, resolves the watchlist, connects, subscribes,
and after every (re)connection runs the recovery sequence

    connected (the handshake authenticates the token)
      -> restore subscriptions -> verify instruments -> verify latest prices
      -> LIVE

before reporting ready.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from app.brokers.zerodha.auth import AuthState, KiteAuthService
from app.brokers.zerodha.exceptions import KiteError
from app.brokers.zerodha.historical import ZerodhaHistoricalDataProvider
from app.brokers.zerodha.instruments import (
    InstrumentNotFound,
    InstrumentResolver,
    InstrumentService,
)
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.ticker import KiteTickerClient, SubscriptionManager, TickMode
from app.core.logging import get_logger
from app.market_data.base import MarketDataProviderError, utcnow
from app.market_data.broker_provider import BrokerMarketDataProvider
from app.market_data.historical import HistoricalDataError
from app.market_data.schemas import (
    Candle,
    InstrumentInfo,
    MarketDataProviderStatus,
    NormalizedTick,
    Tick,
    Timeframe,
)
from app.market_data.tick_processor import TickProcessor

logger = get_logger(__name__)

TickListener = Callable[[NormalizedTick], Awaitable[None] | None]
TickerFactory = Callable[[], KiteTickerClient]


class FeedState:
    NOT_CONFIGURED = "NOT_CONFIGURED"
    WAITING_FOR_LOGIN = "WAITING_FOR_LOGIN"
    SYNCING_INSTRUMENTS = "SYNCING_INSTRUMENTS"
    CONNECTING = "CONNECTING"
    RECOVERING = "RECOVERING"
    LIVE = "LIVE"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class ZerodhaMarketDataProvider(BrokerMarketDataProvider):
    source = "ZERODHA"
    simulated = False
    background_warmup = True

    def __init__(
        self,
        *,
        auth: KiteAuthService,
        client: KiteClient,
        resolver: InstrumentResolver,
        historical: ZerodhaHistoricalDataProvider,
        instrument_service: InstrumentService | None = None,
        api_key: str | None = None,
        ws_url: str = "wss://ws.kite.trade",
        tick_mode: TickMode = TickMode.QUOTE,
        index_mode: TickMode = TickMode.FULL,
        stale_tick_seconds: float = 30.0,
        max_move_percent: float = 20.0,
        ticker_factory: TickerFactory | None = None,
        queue_size: int = 20_000,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.auth = auth
        self.client = client
        self.resolver = resolver
        self.historical = historical
        self.instrument_service = instrument_service
        self._api_key = api_key or ""
        self._ws_url = ws_url
        self.tick_mode = tick_mode
        self.index_mode = index_mode
        self._ticker_factory = ticker_factory
        self._clock = clock
        self._symbols: list[str] = []
        self._token_symbol: dict[int, tuple[str, str]] = {}
        self._symbol_token: dict[str, int] = {}
        self.unresolved: list[str] = []
        self.processor = TickProcessor(
            resolve=self._token_symbol.get,
            clock=clock,
            stale_after=timedelta(seconds=stale_tick_seconds),
            max_move_percent=max_move_percent,
        )
        self._queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=queue_size)
        self.dropped_ticks = 0
        self._ltp: dict[str, float] = {}
        self.last_tick_at: datetime | None = None
        self.latency_ms: float | None = None
        self.tick_listeners: list[TickListener] = []
        self.ticker: KiteTickerClient | None = None
        self.subscriptions: SubscriptionManager | None = None
        self.state = (
            FeedState.NOT_CONFIGURED if not auth.configured else FeedState.WAITING_FOR_LOGIN
        )
        self.last_error: str | None = None
        self._ready = False
        self._generation = 0
        self._ready_changed = asyncio.Event()
        self._started = False
        self._feed_lock = asyncio.Lock()
        self._feed_task: asyncio.Task[None] | None = None
        self.verified_prices = 0
        self._new_session = False
        auth.add_listener(self._on_auth_state)

    # --- identity ---------------------------------------------------------------

    @property
    def credentials_configured(self) -> bool:
        return self.auth.configured

    @property
    def broker_name(self) -> str:
        return "ZERODHA"

    def is_ready(self) -> bool:
        return self._ready

    @property
    def ready_generation(self) -> int:
        return self._generation

    async def wait_ready(self, after: int = 0) -> int:
        while not (self._ready and self._generation > after):
            self._ready_changed.clear()
            await self._ready_changed.wait()
        return self._generation

    def _set_ready(self, ready: bool, *, new_generation: bool = False) -> None:
        if new_generation and ready:
            self._generation += 1
        self._ready = ready
        self._ready_changed.set()

    # --- lifecycle ----------------------------------------------------------------

    async def connect(self) -> None:
        self._started = True
        if self.auth.authenticated:
            self._spawn_feed_start()

    async def disconnect(self) -> None:
        self._started = False
        if self._feed_task is not None:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._feed_task = None
        await self._stop_feed(FeedState.STOPPED)

    def _spawn_feed_start(self) -> None:
        if self._feed_task is not None and not self._feed_task.done():
            return
        self._feed_task = asyncio.create_task(self._start_feed(), name="zerodha-feed-start")

    async def _on_auth_state(self, state: AuthState) -> None:
        if state is AuthState.AUTHENTICATED:
            if self._started:
                self._spawn_feed_start()
        elif state in (AuthState.TOKEN_EXPIRED, AuthState.LOGGED_OUT, AuthState.AUTH_ERROR):
            await self._stop_feed(FeedState.WAITING_FOR_LOGIN)

    async def _start_feed(self) -> None:
        async with self._feed_lock:
            if self.ticker is not None:
                await self._stop_feed(FeedState.CONNECTING)
            try:
                if self.instrument_service is not None:
                    self.state = FeedState.SYNCING_INSTRUMENTS
                    if not await self.instrument_service.ensure_fresh():
                        raise KiteError("instrument sync failed")
                elif self.resolver.size == 0:
                    await self.resolver.reload()
            except Exception as exc:  # noqa: BLE001 - reported in status; retried on next login
                self._fail(f"instrument master unavailable: {type(exc).__name__}: {exc}")
                return
            desired = self._resolve_symbols()
            if not desired:
                self._fail("no watchlist symbol could be resolved in the instrument master")
                return
            self.state = FeedState.CONNECTING
            ticker = (
                self._ticker_factory()
                if self._ticker_factory
                else KiteTickerClient(
                    api_key=self._api_key,
                    token_provider=self.client.access_token_for_ticker,
                    url=self._ws_url,
                )
            )
            ticker.on_connect = self._on_ticker_connect
            ticker.on_ticks = self._on_ticks
            ticker.on_error = self._on_ticker_error
            ticker.on_close = self._on_ticker_close
            ticker.on_reconnect = self._on_ticker_reconnect
            ticker.on_noreconnect = self._on_ticker_noreconnect
            self.ticker = ticker
            self.subscriptions = SubscriptionManager(ticker, default_mode=self.tick_mode)
            await self.subscriptions.update_subscriptions(desired)
            self._new_session = True
            logger.info(
                "BROKER_CONNECT market data feed starting: %d instruments (%d unresolved)",
                len(desired),
                len(self.unresolved),
            )
            await ticker.connect()

    async def _stop_feed(self, state: str) -> None:
        ticker, self.ticker = self.ticker, None
        self._set_ready(False)
        if ticker is not None:
            await ticker.disconnect()
        if self.subscriptions is not None:
            self.subscriptions.clear()
        self.subscriptions = None
        self.state = state if self.auth.configured else FeedState.NOT_CONFIGURED

    def _fail(self, message: str) -> None:
        self.state = FeedState.ERROR
        self.last_error = message
        logger.error("BROKER_ERROR %s", message)

    def _resolve_symbols(self) -> dict[int, TickMode]:
        self._token_symbol.clear()
        self._symbol_token.clear()
        self.unresolved = []
        desired: dict[int, TickMode] = {}
        for symbol in self._symbols:
            try:
                record = self.resolver.resolve_app_symbol(symbol)
            except InstrumentNotFound:
                self.unresolved.append(symbol)
                continue
            self._token_symbol[record.instrument_token] = (symbol, record.exchange)
            self._symbol_token[symbol] = record.instrument_token
            desired[record.instrument_token] = (
                self.index_mode if record.is_index else self.tick_mode
            )
        if self.unresolved:
            logger.warning(
                "Symbols not in the instrument master (not subscribed): %s",
                ", ".join(self.unresolved[:20]),
            )
        return desired

    # --- ticker callbacks ------------------------------------------------------------

    async def _on_ticker_connect(self) -> None:
        self.state = FeedState.RECOVERING
        self._set_ready(False)
        self.processor.reset()  # ordering state from before the gap no longer applies
        subs = self.subscriptions
        restored = await subs.recover_subscriptions_after_reconnect() if subs else 0
        missing = [t for t in self._token_symbol if self.resolver.find_by_token(t) is None]
        if missing:
            self._fail(f"{len(missing)} subscribed instruments are missing from the master")
            return
        try:
            await self._verify_prices()
        except Exception as exc:  # noqa: BLE001 - live ticks still flow; flagged in status
            self.last_error = f"price verification failed: {type(exc).__name__}"
            logger.warning("BROKER_ERROR %s", self.last_error)
        self.state = FeedState.LIVE
        new, self._new_session = self._new_session, False
        self._set_ready(True, new_generation=new)
        logger.info(
            "Market data feed LIVE: %d subscriptions restored, %d prices verified",
            restored,
            self.verified_prices,
        )

    async def _verify_prices(self) -> None:
        keys: dict[str, str] = {}
        for token, (symbol, exchange) in self._token_symbol.items():
            record = self.resolver.find_by_token(token)
            if record is not None:
                keys[f"{exchange}:{record.tradingsymbol}"] = symbol
        data = await self.client.ltp(list(keys)[:1000])
        verified = 0
        for key, item in data.items():
            app_symbol = keys.get(key)
            price = item.get("last_price") if isinstance(item, dict) else None
            if app_symbol and isinstance(price, int | float) and price > 0:
                self._ltp.setdefault(app_symbol, float(price))
                verified += 1
        self.verified_prices = verified

    async def _on_ticks(self, raws: list[dict[str, Any]], received_at: datetime) -> None:
        for raw in raws:
            result = self.processor.process(raw, received_at)
            tick = result.tick
            if tick is None:
                continue
            self._ltp[tick.symbol] = tick.last_price
            self.last_tick_at = received_at
            if tick.exchange_timestamp is not None:
                lag = max((received_at - tick.exchange_timestamp).total_seconds() * 1000, 0.0)
                self.latency_ms = (
                    lag if self.latency_ms is None else 0.8 * self.latency_ms + 0.2 * lag
                )
            for listener in list(self.tick_listeners):
                try:
                    out = listener(tick)
                    if inspect.isawaitable(out):
                        await out
                except Exception:  # noqa: BLE001 - a sink failure must not stop the feed
                    logger.exception("Tick listener failed")
            pipeline_tick = tick.to_tick()
            if self._queue.full():
                self._queue.get_nowait()
                self.dropped_ticks += 1
            self._queue.put_nowait(pipeline_tick)

    async def _on_ticker_error(self, message: str) -> None:
        self.last_error = message

    async def _on_ticker_close(self, code: int | None, reason: str) -> None:
        self._set_ready(False)
        self.state = FeedState.RECONNECTING

    async def _on_ticker_reconnect(self, attempt: int) -> None:
        self._set_ready(False)
        self.state = FeedState.RECONNECTING

    async def _on_ticker_noreconnect(self, reason: str) -> None:
        self._set_ready(False)
        self._fail(f"websocket stopped: {reason}")
        if "auth" in reason:
            await self.auth.validate_session()  # marks the session expired if it is

    # --- MarketDataProvider ------------------------------------------------------------

    async def subscribe(self, symbols: list[str]) -> None:
        for s in symbols:
            if s not in self._symbols:
                self._symbols.append(s)
        if self.subscriptions is not None:
            await self.subscriptions.update_subscriptions(self._resolve_symbols())

    async def unsubscribe(self, symbols: list[str]) -> None:
        self._symbols = [s for s in self._symbols if s not in symbols]
        if self.subscriptions is not None:
            await self.subscriptions.update_subscriptions(self._resolve_symbols())

    async def stream_ticks(self) -> Any:
        while True:
            yield await self._queue.get()

    async def get_ltp(self, symbol: str) -> float:
        if symbol in self._ltp:
            return self._ltp[symbol]
        if not self.auth.authenticated:
            raise MarketDataProviderError("Zerodha market data needs a Kite login")
        try:
            record = self.resolver.resolve_app_symbol(symbol)
            key = f"{record.exchange}:{record.tradingsymbol}"
            data = await self.client.ltp([key])
        except (KiteError, InstrumentNotFound) as exc:
            raise MarketDataProviderError(f"LTP {symbol}: {exc}") from exc
        item = data.get(key)
        price = item.get("last_price") if isinstance(item, dict) else None
        if not isinstance(price, int | float) or price <= 0:
            raise MarketDataProviderError(f"No LTP for {symbol}")
        self._ltp[symbol] = float(price)
        return float(price)

    async def get_historical_data(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        if not self.auth.authenticated:
            raise MarketDataProviderError("Zerodha historical data needs a Kite login")
        try:
            return await self.historical.get_recent_candles(symbol, timeframe, limit)
        except HistoricalDataError:
            raise
        except KiteError as exc:
            raise MarketDataProviderError(f"historical {symbol}: {exc}") from exc

    async def get_instruments(self) -> list[InstrumentInfo]:
        out: list[InstrumentInfo] = []
        for symbol in self._symbols:
            token = self._symbol_token.get(symbol)
            record = self.resolver.find_by_token(token) if token else None
            if record is None:
                continue
            out.append(
                InstrumentInfo(
                    symbol=symbol,
                    name=record.name or record.tradingsymbol,
                    exchange=record.exchange,
                    is_active=True,
                )
            )
        return out

    async def health_check(self) -> MarketDataProviderStatus:
        if self.state == FeedState.LIVE and self.ticker is not None and self.ticker.connected:
            return MarketDataProviderStatus.CONNECTED
        if self.state in (
            FeedState.CONNECTING,
            FeedState.RECOVERING,
            FeedState.RECONNECTING,
            FeedState.SYNCING_INSTRUMENTS,
        ):
            return MarketDataProviderStatus.CONNECTING
        if self.state == FeedState.ERROR:
            return MarketDataProviderStatus.ERROR
        return MarketDataProviderStatus.DISCONNECTED

    def status_details(self) -> dict[str, object]:
        ticker = self.ticker.status() if self.ticker is not None else None
        return {
            "feed_state": self.state,
            "websocket": ticker["state"] if ticker else "DISCONNECTED",
            "ticker": ticker,
            "subscribed_instruments": len(self.subscriptions) if self.subscriptions else 0,
            "unresolved_symbols": list(self.unresolved),
            "last_tick_at": self.last_tick_at,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
            "ticks_accepted": self.processor.accepted,
            "ticks_rejected": self.processor.rejected,
            "rejected_by_issue": dict(self.processor.rejected_by_issue),
            "dropped_ticks": self.dropped_ticks,
            "verified_prices": self.verified_prices,
            "last_error": self.last_error,
            "historical_requests": self.historical.requests,
            "tick_mode": self.tick_mode.value,
        }


__all__ = ["FeedState", "ZerodhaMarketDataProvider"]
