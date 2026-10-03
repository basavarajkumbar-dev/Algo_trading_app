"""MarketDataProvider interface.

Per PROJECT_CONTRACT.md's Market Data Engine section: strategies and
the rest of the app depend only on this interface, never on a concrete
provider. `MockMarketDataProvider` (fully working, no credentials) is
the default for demo/paper use; `BrokerMarketDataProvider` is an
abstract extension point for a real broker feed added in a later phase.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.market_data.schemas import (
    Candle,
    InstrumentInfo,
    MarketDataProviderStatus,
    Tick,
    Timeframe,
)


class MarketDataProviderError(Exception):
    """Base error for provider failures (connection issues, bad symbol, etc.)."""


class MarketDataProvider(ABC):
    """Abstract interface every market data source implements.

    Phase 13 adds non-abstract defaults describing the source, so callers
    can label data honestly and wait for a feed that needs a login:

      source             "MOCK" (simulated) or "ZERODHA" (real exchange data)
      simulated          True for any generated data
      is_ready()         the feed can serve data now (a broker feed: logged in
                         and connected); always True for the simulator
      ready_generation   bumps each time a feed (re)starts after a login, so
                         the service knows to (re)load warm-up history
      background_warmup  warm-up history is slow (rate-limited REST), load
                         it in the background instead of at startup
    """

    source: str = "MOCK"
    simulated: bool = True
    background_warmup: bool = False

    def is_ready(self) -> bool:
        return True

    @property
    def ready_generation(self) -> int:
        return 1

    async def wait_ready(self, after: int = 0) -> int:
        """Wait until ready with a generation above `after`; return it."""

        if self.is_ready() and self.ready_generation > after:
            return self.ready_generation
        await asyncio.Event().wait()  # a static feed never changes generation
        return self.ready_generation  # pragma: no cover

    def status_details(self) -> dict[str, object]:
        """Extra, credential-free status for system health."""

        return {}

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection/session needed to serve data."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the connection/session cleanly."""

    @abstractmethod
    async def subscribe(self, symbols: list[str]) -> None:
        """Mark the given symbols as actively streamed."""

    @abstractmethod
    async def unsubscribe(self, symbols: list[str]) -> None:
        """Stop streaming the given symbols."""

    @abstractmethod
    async def get_ltp(self, symbol: str) -> float:
        """Return the last traded price for a symbol.

        Raises MarketDataProviderError if the symbol is unknown or no
        price is available yet.
        """

    @abstractmethod
    async def get_historical_data(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Return up to `limit` most recent closed candles for a symbol/timeframe."""

    @abstractmethod
    async def get_instruments(self) -> list[InstrumentInfo]:
        """Return the tradable instrument universe this provider serves."""

    @abstractmethod
    async def health_check(self) -> MarketDataProviderStatus:
        """Report this provider's current connection status."""

    @abstractmethod
    def stream_ticks(self) -> AsyncIterator[Tick]:
        """Yield ticks for subscribed symbols as they become available.

        Implementations define this as an async generator
        (`async def stream_ticks(self): ... yield tick`).
        """


def utcnow() -> datetime:
    """Single source of "now" so validation/tests can reason about it consistently."""

    return datetime.now(UTC)
