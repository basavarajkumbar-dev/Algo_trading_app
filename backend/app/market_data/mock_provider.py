"""MockMarketDataProvider.

A fully working, self-contained market data source requiring no
external credentials or network access. Simulates tick movement,
volume, and per-symbol regimes (trending / range / breakout /
high-volatility) across the NIFTY 50 universe plus a synthetic NIFTY50
index.

Calibration (Phase 4): regime parameters are defined per *minute* and
scaled to per-tick and per-candle values, so ticks, live candles and
generated history all share one volatility scale. The defaults put a
typical large-cap at roughly 0.06-0.08% per-minute volatility, in the
neighbourhood of real NSE large caps, rather than the per-tick values
used in Phase 3, which were several times too volatile for realistic
indicator and regime behaviour.

This is simulated data. It is never presented to the user as a live
market feed — see PROJECT_CONTRACT.md section 9 and the "notes" field
on /system/health and /market/status. Known simplifications:

- The NIFTY50 index is its own simulated series, not computed from the
  50 constituents, so index direction and constituent breadth can
  disagree more often than in a real market.
- Generated history for each timeframe is produced independently, so a
  symbol's 5m and 60m histories are each internally consistent but
  not aggregates of one another.
- The feed runs 24/7; there are no session boundaries or market hours.
"""

from __future__ import annotations

import asyncio
import math
import random
import zlib
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

#: Per-regime (drift, volatility) per MINUTE, as fractions of price.
_REGIME_PARAMETERS_PER_MINUTE: dict[SimulatedRegime, tuple[float, float]] = {
    SimulatedRegime.TRENDING_UP: (0.00012, 0.0007),
    SimulatedRegime.TRENDING_DOWN: (-0.00012, 0.0007),
    SimulatedRegime.RANGE: (0.0, 0.0007),
    SimulatedRegime.BREAKOUT: (0.00035, 0.0011),
    SimulatedRegime.HIGH_VOLATILITY: (0.0, 0.0028),
}

#: Fraction of the distance back to the range anchor recovered per
#: minute while a symbol is in the RANGE regime (makes RANGE actually
#: range-bound instead of a driftless random walk).
_RANGE_MEAN_REVERSION_PER_MINUTE = 0.06

#: Relative volume multiplier per regime.
_REGIME_VOLUME_MULTIPLIER: dict[SimulatedRegime, float] = {
    SimulatedRegime.TRENDING_UP: 1.1,
    SimulatedRegime.TRENDING_DOWN: 1.1,
    SimulatedRegime.RANGE: 0.9,
    SimulatedRegime.BREAKOUT: 2.2,
    SimulatedRegime.HIGH_VOLATILITY: 1.6,
}

#: Wick size relative to per-candle volatility, per regime.
_REGIME_WICK_FACTOR: dict[SimulatedRegime, float] = {
    SimulatedRegime.TRENDING_UP: 0.4,
    SimulatedRegime.TRENDING_DOWN: 0.4,
    SimulatedRegime.RANGE: 0.5,
    SimulatedRegime.BREAKOUT: 0.4,
    SimulatedRegime.HIGH_VOLATILITY: 1.2,
}

#: How often each regime is chosen when (re)assigning regimes.
_REGIME_WEIGHTS: dict[SimulatedRegime, float] = {
    SimulatedRegime.TRENDING_UP: 0.25,
    SimulatedRegime.TRENDING_DOWN: 0.25,
    SimulatedRegime.RANGE: 0.30,
    SimulatedRegime.BREAKOUT: 0.10,
    SimulatedRegime.HIGH_VOLATILITY: 0.10,
}

#: Simulated traded volume per symbol per minute (before regime multiplier).
_BASE_VOLUME_PER_MINUTE = 8_000

#: Shape of a BREAKOUT history: ordinary two-way trade, then a
#: low-volatility compression phase, then the breakout candles at the end.
_BREAKOUT_CANDLES = 1
_COMPRESSION_CANDLES = 40
_COMPRESSION_VOL_FACTOR = 0.35

#: A HIGH_VOLATILITY history is ordinary trade followed by a volatile
#: episode of this many candles, so the volatility stands out against
#: the symbol's own baseline.
_HIGH_VOLATILITY_EPISODE_CANDLES = 25
_CALM_VOL_PER_MINUTE = _REGIME_PARAMETERS_PER_MINUTE[SimulatedRegime.RANGE][1]


class _SimulatedSymbolState:
    __slots__ = ("price", "regime", "day_open", "range_anchor")

    def __init__(self, price: float, regime: SimulatedRegime) -> None:
        self.price = price
        self.regime = regime
        self.day_open = price
        self.range_anchor = price


class MockMarketDataProvider(MarketDataProvider):
    """Simulated market data for the NIFTY 50 universe.

    Safe to instantiate and use with zero configuration. `connect()` /
    `disconnect()` toggle a simple internal flag — there is no real
    network resource to manage.

    `regime_rotation_seconds` controls how often simulated regimes are
    re-rolled while streaming (None disables rotation, e.g. for tests),
    so the demo shows regimes changing over time.
    """

    def __init__(
        self,
        *,
        tick_interval_seconds: float = 1.0,
        rng_seed: int | None = None,
        regime_rotation_seconds: float | None = 900.0,
        regime_rotation_probability: float = 0.35,
    ) -> None:
        self._tick_interval_seconds = tick_interval_seconds
        self._rng = random.Random(rng_seed)
        self._seed_salt = rng_seed if rng_seed is not None else self._rng.getrandbits(32)
        self._status = MarketDataProviderStatus.DISCONNECTED
        self._subscribed: set[str] = set()
        self._regime_rotation_seconds = regime_rotation_seconds
        self._regime_rotation_probability = regime_rotation_probability
        self._ticks_per_minute = 20.0

        self._instruments: dict[str, InstrumentInfo] = {
            inst.symbol: InstrumentInfo(symbol=inst.symbol, name=inst.name, sector=inst.sector)
            for inst in NIFTY_50_UNIVERSE
        }
        self._instruments[NIFTY_INDEX_SYMBOL] = InstrumentInfo(
            symbol=NIFTY_INDEX_SYMBOL, name="NIFTY 50 Index (simulated)", sector=None
        )

        self._state: dict[str, _SimulatedSymbolState] = {
            inst.symbol: _SimulatedSymbolState(inst.reference_price, self._random_regime())
            for inst in NIFTY_50_UNIVERSE
        }
        self._state[NIFTY_INDEX_SYMBOL] = _SimulatedSymbolState(
            NIFTY_INDEX_BASELINE, self._random_regime()
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
        return round(self._require_state(symbol).price, 2)

    async def get_historical_data(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        """Generate a synthetic closed-candle history ending at the
        symbol's current price, shaped by its current simulated regime.

        The series is generated forward from an arbitrary start and then
        rescaled so its final close equals the current price, so the
        history joins up with the live feed without a price gap.
        Deterministic for a given provider seed, symbol and timeframe.

        Synthetic warm-up data, not real history: the backtesting phase
        will need genuine historical data.
        """

        state = self._require_state(symbol)
        if limit <= 0:
            return []

        seed = zlib.crc32(f"{symbol}:{timeframe.value}".encode()) ^ (self._seed_salt & 0xFFFFFFFF)
        rng = random.Random(seed)
        minutes = timeframe.minutes
        regime = state.regime
        drift_min, vol_min = _REGIME_PARAMETERS_PER_MINUTE[regime]
        candle_vol = vol_min * math.sqrt(minutes)
        wick_factor = _REGIME_WICK_FACTOR[regime]
        volume_multiplier = _REGIME_VOLUME_MULTIPLIER[regime]
        reversion = min(1.0, _RANGE_MEAN_REVERSION_PER_MINUTE * minutes)

        breakout_start = limit - _BREAKOUT_CANDLES
        compression_start = breakout_start - min(_COMPRESSION_CANDLES, limit // 3)
        episode_start = limit - _HIGH_VOLATILITY_EPISODE_CANDLES
        calm_vol = _CALM_VOL_PER_MINUTE * math.sqrt(minutes)
        calm_wick = _REGIME_WICK_FACTOR[SimulatedRegime.RANGE]

        raw: list[tuple[float, float, float, float, int]] = []
        price = 1.0
        for i in range(limit):
            mean_reverting = -reversion * (price - 1.0)
            # (drift, volatility, volume multiplier, wick factor) per candle.
            if regime == SimulatedRegime.BREAKOUT:
                if i >= breakout_start:  # the breakout: big candles, heavy volume
                    step = (
                        drift_min * minutes * 6,
                        candle_vol,
                        volume_multiplier * 1.5,
                        wick_factor,
                    )
                elif i >= compression_start:  # quiet compression, light volume
                    step = (mean_reverting, candle_vol * _COMPRESSION_VOL_FACTOR, 0.7, wick_factor)
                else:  # ordinary two-way trade
                    step = (mean_reverting, calm_vol, 1.0, calm_wick)
            elif regime == SimulatedRegime.HIGH_VOLATILITY:
                if i >= episode_start:  # the volatile episode
                    step = (0.0, candle_vol, volume_multiplier, wick_factor)
                else:
                    step = (mean_reverting, calm_vol, 1.0, calm_wick)
            elif regime == SimulatedRegime.RANGE:
                step = (mean_reverting, candle_vol, volume_multiplier, wick_factor)
            else:  # trending
                step = (drift_min * minutes, candle_vol, volume_multiplier, wick_factor)
            drift, vol, vol_mult, candle_wick_factor = step

            open_price = price
            close_price = max(1e-6, open_price * (1 + rng.gauss(drift, vol)))
            wick = vol * candle_wick_factor
            high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0, wick)))
            low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0, wick)))
            volume = int(_BASE_VOLUME_PER_MINUTE * minutes * vol_mult * rng.lognormvariate(0, 0.3))
            raw.append((open_price, high_price, low_price, close_price, max(volume, 0)))
            price = close_price

        scale = state.price / raw[-1][3]
        now = utcnow().replace(second=0, microsecond=0)
        current_bucket = now.replace(minute=(now.minute // minutes) * minutes)

        candles: list[Candle] = []
        for offset, (o, h, low, c, v) in enumerate(raw):
            periods_back = limit - offset
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=current_bucket - timedelta(minutes=minutes * periods_back),
                    open=round(o * scale, 2),
                    high=round(h * scale, 2),
                    low=round(low * scale, 2),
                    close=round(c * scale, 2),
                    volume=v,
                    is_closed=True,
                )
            )
        return candles

    async def get_instruments(self) -> list[InstrumentInfo]:
        return list(self._instruments.values())

    async def health_check(self) -> MarketDataProviderStatus:
        return self._status

    async def stream_ticks(self) -> AsyncIterator[Tick]:
        """Yield simulated ticks for subscribed symbols until disconnected.

        Each cycle ticks a random subset (about a third) of subscribed
        symbols, to mimic staggered real market activity, then sleeps
        `tick_interval_seconds`. Regimes are periodically re-rolled if
        rotation is enabled. The caller cancels the driving task (see
        app.market_data.service.MarketDataService).
        """

        loop = asyncio.get_running_loop()
        last_rotation = loop.time()

        while self._status == MarketDataProviderStatus.CONNECTED:
            if not self._subscribed:
                await asyncio.sleep(self._tick_interval_seconds)
                continue

            if (
                self._regime_rotation_seconds is not None
                and loop.time() - last_rotation >= self._regime_rotation_seconds
            ):
                self._rotate_regimes()
                last_rotation = loop.time()

            subscribed = list(self._subscribed)
            sample_size = max(1, len(subscribed) // 3)
            self._ticks_per_minute = max(
                1.0, (60.0 / max(self._tick_interval_seconds, 1e-3)) * sample_size / len(subscribed)
            )
            for symbol in self._rng.sample(subscribed, sample_size):
                yield self._generate_tick(symbol)

            await asyncio.sleep(self._tick_interval_seconds)

    # --- internal simulation helpers ------------------------------------

    def _require_state(self, symbol: str) -> _SimulatedSymbolState:
        state = self._state.get(symbol)
        if state is None:
            raise MarketDataProviderError(f"Unknown symbol: {symbol}")
        return state

    def _random_regime(self) -> SimulatedRegime:
        regimes = list(_REGIME_WEIGHTS)
        return self._rng.choices(regimes, weights=[_REGIME_WEIGHTS[r] for r in regimes])[0]

    def _rotate_regimes(self) -> None:
        for state in self._state.values():
            if self._rng.random() < self._regime_rotation_probability:
                self._apply_regime(state, self._random_regime())

    @staticmethod
    def _apply_regime(state: _SimulatedSymbolState, regime: SimulatedRegime) -> None:
        state.regime = regime
        state.range_anchor = state.price

    def _generate_tick(self, symbol: str) -> Tick:
        state = self._state[symbol]
        tpm = self._ticks_per_minute
        drift_min, vol_min = _REGIME_PARAMETERS_PER_MINUTE[state.regime]
        drift = drift_min / tpm
        if state.regime == SimulatedRegime.RANGE and state.range_anchor > 0:
            drift -= (_RANGE_MEAN_REVERSION_PER_MINUTE / tpm) * (
                state.price / state.range_anchor - 1.0
            )
        vol = vol_min / math.sqrt(tpm)

        new_price = max(0.01, state.price * (1 + self._rng.gauss(drift, vol)))
        state.price = new_price

        mean_volume = _BASE_VOLUME_PER_MINUTE * _REGIME_VOLUME_MULTIPLIER[state.regime] / tpm
        volume = max(0, int(mean_volume * self._rng.lognormvariate(0, 0.4)))
        return Tick(symbol=symbol, price=round(new_price, 2), volume=volume, timestamp=utcnow())

    def set_regime(self, symbol: str, regime: SimulatedRegime) -> None:
        """Testing/demo hook: force a symbol's simulated regime."""

        self._apply_regime(self._require_state(symbol), regime)

    def get_regime(self, symbol: str) -> SimulatedRegime:
        return self._require_state(symbol).regime

    def get_day_open(self, symbol: str) -> float:
        return self._require_state(symbol).day_open
