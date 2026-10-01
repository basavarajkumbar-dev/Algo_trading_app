"""PaperMarketDataProvider.

The data source the paper trading engine (docs/development-roadmap.md
Phase 11) will use. Today it delegates entirely to
`MockMarketDataProvider` because there is no live feed connected in
this phase — but it is structurally its own class implementing the
same `MarketDataProvider` interface, so paper trading always depends on
"whatever PaperMarketDataProvider currently wraps" rather than directly
on the mock implementation. Swapping in a real delayed/live feed later
means changing this file, not every caller.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.market_data.base import MarketDataProvider
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.schemas import (
    Candle,
    InstrumentInfo,
    MarketDataProviderStatus,
    Tick,
    Timeframe,
)


class PaperMarketDataProvider(MarketDataProvider):
    """Data source for PAPER-mode trading. Currently backed by
    `MockMarketDataProvider`; see module docstring."""

    def __init__(self, *, delegate: MarketDataProvider | None = None) -> None:
        self._delegate: MarketDataProvider = delegate or MockMarketDataProvider()

    async def connect(self) -> None:
        await self._delegate.connect()

    async def disconnect(self) -> None:
        await self._delegate.disconnect()

    async def subscribe(self, symbols: list[str]) -> None:
        await self._delegate.subscribe(symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        await self._delegate.unsubscribe(symbols)

    async def get_ltp(self, symbol: str) -> float:
        return await self._delegate.get_ltp(symbol)

    async def get_historical_data(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        return await self._delegate.get_historical_data(symbol, timeframe, limit)

    async def get_instruments(self) -> list[InstrumentInfo]:
        return await self._delegate.get_instruments()

    async def health_check(self) -> MarketDataProviderStatus:
        return await self._delegate.health_check()

    async def stream_ticks(self) -> AsyncIterator[Tick]:
        async for tick in self._delegate.stream_ticks():
            yield tick

    @property
    def delegate(self) -> MarketDataProvider:
        """Exposed for tests/introspection (e.g. forcing a simulated
        regime on the underlying mock provider)."""

        return self._delegate
