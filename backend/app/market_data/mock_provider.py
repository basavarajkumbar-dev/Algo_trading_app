"""MockMarketDataProvider.

A fully working, self-contained market data source requiring no
external credentials or network access. Simulates realistic tick
movement, volume, and configurable per-symbol regimes
(trending/range/breakout/high-volatility) across the NIFTY 50 universe
plus a synthetic NIFTY50 index composite.

This is simulated data. It is never presented to the user as a live
market feed — see PROJECT_CONTRACT.md section 9 and the "notes" field
on /system/health and /market/status.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from datetime import timedelta

from app.market_data.base import MarketDataProvider, MarketDataProviderError, utcnow
from app.market_data.schemas import (
    Candle,
    InstrumentInfo,
    MarketDataProviderStatus,
    SimulatedRegime,
    Tick,
    Timeframe,
)
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_BASELINE, NIFTY_INDEX_SYMBOL

#: Per-regime (drift, volatility) applied to each simulated tick step,
#: expressed as a fraction of price. Drift is the average per-tick
#: percentage move; volatility scales the random component around it.
_REGIME_PARAMETERS: dict[SimulatedRegime, tuple[float, float]] = {
    SimulatedRegime.TRENDING_UP: (0.0006, 0.0012),
    SimulatedRegime.TRENDING_DOWN: (-0.0006, 0.0012),
    SimulatedRegime.RANGE: (0.0, 0.0008),
    SimulatedRegime.BREAKOUT: (0.0015, 0.0025),
    SimulatedRegime.HIGH_VOLATILITY: (0.0, 0.006),
}


class _SimulatedSymbolState:
    __slots__ = ("price", "regime", "day_open")

    def __init__(self, price: float, regime: SimulatedRegime) -> None:
        self.price = price
        self.regime = regime
        self.day_open = price


class MockMarketDataProvider(MarketDataProvider):
    """Simulated market data for the NIFTY 50 universe.

    Safe to instantiate and use with zero configuration. `connect()` /
    `disconnect()` toggle a simple internal flag — there is no real
    network resource to manage.
    """

    def __init__(self, *, tick_interval_seconds: float = 1.0, rng_seed: int | None = None) -> None:
        self._tick_interval_seconds = tick_interval_seconds
        self._rng = random.Random(rng_seed)
        self._status = MarketDataProviderStatus.DISCONNECTED
        self._subscribed: set[str] = set()

        self._instruments: dict[str, InstrumentInfo] = {
            inst.symbol: InstrumentInfo(symbol=inst.symbol, name=inst.name, sector=inst.sector)
            for inst in NIFTY_50_UNIVERSE
        }
        self._instruments[NIFTY_INDEX_SYMBOL] = InstrumentInfo(
            symbol=NIFTY_INDEX_SYMBOL, name="NIFTY 50 Index (simulated composite)", sector=None
        )

        self._state: dict[str, _SimulatedSymbolState] = {
            inst.symbol: _SimulatedSymbolState(
                inst.reference_price, self._rng.choice(list(SimulatedRegime))
            )
            for inst in NIFTY_50_UNIVERSE
        }
        self._state[NIFTY_INDEX_SYMBOL] = _SimulatedSymbolState(
            NIFTY_INDEX_BASELINE, SimulatedRegime.RANGE
        )

    # --- MarketDataProvider interface -----------------------------------

    async def connect(self) -> None:
        self._status = MarketDataProviderStatus.CONNECTED

    async def disconnect(self) -> None:
        self._status = MarketDataProviderStatus.DISCONNECTED

    async def subscribe(self, symbols: list[str]) -> None:
        unknown = [s for s in symbols if s not in self._instruments]
        if unknown:
            raise MarketDataProviderError(f"Unknown symbol(s): {', '.join(unknown)}")
        self._subscribed.update(symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        self._subscribed.difference_update(symbols)

    async def get_ltp(self, symbol: str) -> float:
        state = self._state.get(symbol)
        if state is None:
            raise MarketDataProviderError(f"Unknown symbol: {symbol}")
        return round(state.price, 2)

    async def get_historical_data(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Generate a deterministic-per-run synthetic candle history.

        Deterministic for a given provider instance and symbol (same
        seeded RNG stream reproduced from the symbol's current state),
        which is enough for demo/UI purposes. This is NOT a substitute
        for the real historical-data phase (backtesting, Phase 15) —
        it exists so the Market page and future strategy prototyping
        have *something* plausible to render without a live feed.
        """

        state = self._state.get(symbol)
        if state is None:
            raise MarketDataProviderError(f"Unknown symbol: {symbol}")

        rng = random.Random(hash((symbol, timeframe.value)) & 0xFFFFFFFF)
        minutes = timeframe.minutes
        now = utcnow().replace(second=0, microsecond=0)
        price = state.price
        candles: list[Candle] = []

        for i in range(limit, 0, -1):
            bucket_start = now.replace(minute=(now.minute // minutes) * minutes) - timedelta(
                minutes=minutes * i
            )
            drift, vol = _REGIME_PARAMETERS[state.regime]
            open_price = price
            close_price = max(0.01, open_price * (1 + rng.gauss(drift, vol)))
            high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0, vol / 2)))
            low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0, vol / 2)))
            volume = max(0, int(rng.gauss(50_000, 15_000)))

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bucket_start,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(low_price, 2),
                    close=round(close_price, 2),
                    volume=volume,
                    is_closed=True,
                )
            )
            price = close_price

        return candles

    async def get_instruments(self) -> list[InstrumentInfo]:
        return list(self._instruments.values())

    async def health_check(self) -> MarketDataProviderStatus:
        return self._status

    async def stream_ticks(self) -> AsyncIterator[Tick]:
        """Yield simulated ticks for subscribed symbols indefinitely.

        Each cycle ticks a random subset of subscribed symbols (rather
        than all of them every cycle) to look like real, staggered
        market activity, then sleeps `tick_interval_seconds`. The caller
        is responsible for cancelling the task that drives this
        generator (see app.market_data.service.MarketDataService).
        """

        while self._status == MarketDataProviderStatus.CONNECTED:
            if not self._subscribed:
                await asyncio.sleep(self._tick_interval_seconds)
                continue

            sample_size = max(1, len(self._subscribed) // 3)
            symbols_this_cycle = self._rng.sample(list(self._subscribed), sample_size)

            for symbol in symbols_this_cycle:
                yield self._generate_tick(symbol)

            await asyncio.sleep(self._tick_interval_seconds)

    # --- internal simulation helpers ------------------------------------

    def _generate_tick(self, symbol: str) -> Tick:
        state = self._state[symbol]
        drift, vol = _REGIME_PARAMETERS[state.regime]
        pct_change = self._rng.gauss(drift, vol)
        new_price = max(0.01, state.price * (1 + pct_change))
        state.price = new_price

        volume = max(0, int(self._rng.gauss(500, 200)))
        return Tick(symbol=symbol, price=round(new_price, 2), volume=volume, timestamp=utcnow())

    def set_regime(self, symbol: str, regime: SimulatedRegime) -> None:
        """Testing/demo hook: force a symbol's simulated regime."""

        state = self._state.get(symbol)
        if state is None:
            raise MarketDataProviderError(f"Unknown symbol: {symbol}")
        state.regime = regime

    def get_regime(self, symbol: str) -> SimulatedRegime:
        state = self._state.get(symbol)
        if state is None:
            raise MarketDataProviderError(f"Unknown symbol: {symbol}")
        return state.regime

    def get_day_open(self, symbol: str) -> float:
        state = self._state.get(symbol)
        if state is None:
            raise MarketDataProviderError(f"Unknown symbol: {symbol}")
        return state.day_open
