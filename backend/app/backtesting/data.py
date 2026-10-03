"""Historical data sources for backtests.

Two sources share one interface:

* SyntheticHistorySource: a deterministic simulated history on the NSE
  session calendar, for exercising the machinery with zero external data.
  It has regimes, a market factor (the NIFTY 50 index is built first and
  constituents move partly with it), overnight gaps, intraday volatility
  and volume profiles. It is NOT market data: a strategy that "works" on
  it has found structure in a random process, not a market edge.

* CsvHistorySource: real 5-minute OHLCV bars you supply, one file per
  symbol, plus an optional point-in-time universe file. The format is
  documented in docs/backtesting.md.

Both return only session bars and expose a `Universe` that says which
symbols were index members on each day (survivorship-bias control).
"""

from __future__ import annotations

import csv
import hashlib
import random
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.backtesting.calendar import IST, bar_starts, in_session, trading_days
from app.market_data.schemas import Candle, Timeframe
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_BASELINE, NIFTY_INDEX_SYMBOL

TICK = 0.05


def _tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)


# --- universe ------------------------------------------------------------------------------


@dataclass
class Universe:
    """Index membership over time: symbol -> [(first_day, last_day or None)]."""

    periods: dict[str, list[tuple[date, date | None]]]
    point_in_time: bool  # False: a static list applied to every day
    source: str

    def members(self, day: date) -> set[str]:
        return {
            s
            for s, spans in self.periods.items()
            if any(a <= day and (b is None or day <= b) for a, b in spans)
        }

    def ever_members(self, start: date, end: date) -> set[str]:
        return {
            s
            for s, spans in self.periods.items()
            if any(a <= end and (b is None or b >= start) for a, b in spans)
        }


@dataclass
class DataQuality:
    bars: int = 0
    missing_bars: int = 0  # expected session bars absent
    invalid_bars: int = 0  # dropped: high < low, non-positive prices, ...
    duplicate_bars: int = 0
    out_of_session_bars: int = 0
    notes: list[str] = field(default_factory=list)


class HistoricalDataSource(ABC):
    kind: str
    synthetic: bool

    @abstractmethod
    def fingerprint(self) -> str:
        """Identifies the exact data (used for caching and stored with runs)."""

    @abstractmethod
    def available_range(self) -> tuple[date, date]: ...

    @abstractmethod
    def universe(self) -> Universe: ...

    @abstractmethod
    def symbols(self) -> list[str]:
        """Every tradable symbol the source knows (excluding the benchmark)."""

    @abstractmethod
    def bars(self, symbol: str, start: date, end: date) -> list[Candle]:
        """Closed 5-minute session bars with start times in [start, end] (IST days)."""

    def quality(self, symbol: str) -> DataQuality:
        return DataQuality()

    def holidays(self) -> list[date]:
        return []


# --- synthetic -----------------------------------------------------------------------------

# Per-bar (5m) drift and volatility of the idiosyncratic component per regime.
_REGIMES: dict[str, tuple[float, float]] = {
    "TRENDING_UP": (0.0006, 0.0014),
    "TRENDING_DOWN": (-0.0006, 0.0014),
    "RANGE": (0.0, 0.0012),
    "BREAKOUT": (0.0, 0.0011),  # compression, then an expansion leg (see below)
    "HIGH_VOLATILITY": (0.0, 0.0035),
}
_REGIME_ORDER = list(_REGIMES)
_REGIME_WEIGHTS = [0.22, 0.22, 0.32, 0.12, 0.12]
_PERSISTENCE = 0.55  # chance a symbol keeps yesterday's regime
_MARKET_VOL = 0.0012  # index per-bar volatility
_GAP_VOL = 0.004  # overnight gap volatility
_EPOCH = date(2024, 1, 1)


def _u_shape(i: int, n: int) -> float:
    """Intraday volatility/volume profile: high at open and close."""

    x = i / max(n - 1, 1)
    return 0.75 + 1.1 * (x - 0.5) ** 2 * 4 / 2 + (0.6 if i < 3 else 0.0)


class SyntheticHistorySource(HistoricalDataSource):
    kind = "synthetic"
    synthetic = True

    def __init__(
        self,
        seed: int = 42,
        *,
        end: date | None = None,
        holidays: list[date] | None = None,
        null_model: bool = False,
    ):
        """`null_model=True` removes every source of predictability (no
        drift, no mean reversion, no breakout legs, no market drift) while
        keeping volatility, gaps and volume shapes: a control dataset on
        which no rule should make money after costs."""

        self.seed = seed
        self.null_model = null_model
        self._holidays = sorted(holidays or [])
        self._end = end or (datetime.now(UTC).astimezone(IST).date() - timedelta(days=1))
        self._days = trading_days(_EPOCH, self._end, self._holidays)
        self._cache: dict[str, list[tuple[float, float, float, float, int]]] = {}
        self._regime_cache: dict[str, list[str]] = {}
        self._market: list[float] | None = None  # per-bar index returns
        self._market_gaps: list[float] | None = None

    def fingerprint(self) -> str:
        model = "null" if self.null_model else "regimes"
        return f"synthetic-v1:{model}:seed={self.seed}:holidays={len(self._holidays)}"

    def available_range(self) -> tuple[date, date]:
        return self._days[0], self._days[-1]

    def holidays(self) -> list[date]:
        return list(self._holidays)

    def universe(self) -> Universe:
        start = self._days[0]
        periods: dict[str, list[tuple[date, date | None]]] = {
            i.symbol: [(start, None)] for i in NIFTY_50_UNIVERSE
        }
        # Every synthetic symbol is generated for every day regardless of
        # how it "performs", so the synthetic universe has no survivorship
        # bias; it is still a static list.
        return Universe(periods=periods, point_in_time=True, source="synthetic (static, unbiased)")

    def symbols(self) -> list[str]:
        return [i.symbol for i in NIFTY_50_UNIVERSE]

    def regime_on(self, symbol: str, day: date) -> str | None:
        """The generator's own regime label for a day (for tests and reports)."""

        self._series(symbol)
        try:
            return self._regime_cache[symbol][self._days.index(day)]
        except ValueError:
            return None

    # generation --------------------------------------------------------------------------

    def _rng(self, *parts: object) -> random.Random:
        key = ":".join(str(p) for p in (self.seed, *parts))
        return random.Random(zlib.crc32(key.encode()))

    def _market_series(self) -> tuple[list[float], list[float]]:
        if self._market is None or self._market_gaps is None:
            rng = self._rng("market", "null" if self.null_model else "")
            returns: list[float] = []
            gaps: list[float] = []
            day_drift = 0.0
            for _ in self._days:
                day_drift = 0.0 if self.null_model else 0.6 * day_drift + rng.gauss(0, 0.00012)
                gaps.append(rng.gauss(0, _GAP_VOL * 0.6))
                for i in range(75):
                    returns.append(rng.gauss(day_drift, _MARKET_VOL * _u_shape(i, 75)))
            self._market, self._market_gaps = returns, gaps
        return self._market, self._market_gaps

    def _series(self, symbol: str) -> list[tuple[float, float, float, float, int]]:
        if symbol in self._cache:
            return self._cache[symbol]
        market, market_gaps = self._market_series()
        if symbol == NIFTY_INDEX_SYMBOL:
            beta, start_price, idio_scale = 1.0, NIFTY_INDEX_BASELINE, 0.0
        else:
            ref = next((i for i in NIFTY_50_UNIVERSE if i.symbol == symbol), None)
            if ref is None:
                raise KeyError(f"Unknown synthetic symbol {symbol}")
            h = zlib.crc32(symbol.encode())
            beta = 0.6 + (h % 70) / 100  # 0.60 .. 1.29
            start_price, idio_scale = ref.reference_price, 1.0
        rng = self._rng("symbol", symbol, "null" if self.null_model else "")
        regime = rng.choices(_REGIME_ORDER, _REGIME_WEIGHTS)[0]
        price = start_price
        out: list[tuple[float, float, float, float, int]] = []
        regimes: list[str] = []
        k = 0
        for d_index, _day in enumerate(self._days):
            if rng.random() > _PERSISTENCE:
                regime = rng.choices(_REGIME_ORDER, _REGIME_WEIGHTS)[0]
            regimes.append(regime)
            drift, vol = _REGIMES[regime]
            if symbol == NIFTY_INDEX_SYMBOL:
                drift, vol = 0.0, 0.0
            # overnight gap
            price *= 1 + beta * market_gaps[d_index] + idio_scale * rng.gauss(0, _GAP_VOL)
            anchor = price
            breakout_bar = rng.randint(30, 55)
            breakout_sign = rng.choice((-1, 1))
            for i in range(75):
                shape = _u_shape(i, 75)
                d, v = drift, vol
                if regime == "RANGE":
                    d = -0.08 * (price / anchor - 1)
                elif regime == "BREAKOUT":
                    if i < breakout_bar:
                        d, v = -0.05 * (price / anchor - 1), vol * 0.45
                    elif i < breakout_bar + 6:
                        d, v = breakout_sign * 0.0022, vol * 1.6
                    else:
                        d = breakout_sign * 0.0003
                if self.null_model:
                    d, v = 0.0, vol  # pure noise around the market factor
                eps = rng.gauss(0, 1)
                r = beta * market[k] + idio_scale * (d + v * shape * eps)
                o = price
                c = max(0.01, o * (1 + r))
                wick = (abs(r) * 0.6 + v * shape * 0.5 + 0.0004) * o
                hi = max(o, c) + abs(rng.gauss(0, wick))
                lo = max(0.01, min(o, c) - abs(rng.gauss(0, wick)))
                vol_mult = {"BREAKOUT": 1.0, "HIGH_VOLATILITY": 1.5}.get(regime, 1.0)
                if (
                    regime == "BREAKOUT"
                    and breakout_bar <= i < breakout_bar + 6
                    and not self.null_model
                ):
                    vol_mult = 3.0
                volume = int(
                    40_000 * shape * vol_mult * (1 + 2.5 * abs(eps)) * rng.lognormvariate(0, 0.25)
                )
                out.append((_tick(o), _tick(hi), _tick(lo), _tick(c), volume))
                price = c
                k += 1
        # keep OHLC consistent after rounding
        fixed = [(o, max(h, o, c), min(lo, o, c), c, v) for (o, h, lo, c, v) in out]
        self._cache[symbol] = fixed
        self._regime_cache[symbol] = regimes
        return fixed

    def bars(self, symbol: str, start: date, end: date) -> list[Candle]:
        series = self._series(symbol)
        candles: list[Candle] = []
        for d_index, day in enumerate(self._days):
            if day < start or day > end:
                continue
            for i, ts in enumerate(bar_starts(day)):
                o, h, lo, c, v = series[d_index * 75 + i]
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=Timeframe.M5,
                        timestamp=ts,
                        open=o,
                        high=h,
                        low=lo,
                        close=c,
                        volume=v,
                        is_closed=True,
                    )
                )
        return candles


# --- CSV -------------------------------------------------------------------------------------


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if ts.tzinfo is None:  # naive timestamps are exchange time
        ts = ts.replace(tzinfo=IST)
    return ts.astimezone(UTC)


class CsvHistorySource(HistoricalDataSource):
    """`<dir>/<SYMBOL>.csv` with columns timestamp,open,high,low,close,volume
    (timestamp = bar START, ISO 8601; naive means IST). Optional
    `<dir>/universe.csv` with symbol,start_date,end_date (end empty = still a
    member) makes the universe point-in-time; without it the universe is
    every file present, and runs carry a survivorship-bias warning."""

    kind = "csv"
    synthetic = False

    def __init__(self, directory: str | Path, *, benchmark: str = NIFTY_INDEX_SYMBOL):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(f"Backtest data directory not found: {self.directory}")
        self.benchmark = benchmark
        self._bars: dict[str, list[Candle]] = {}
        self._quality: dict[str, DataQuality] = {}
        files = sorted(p for p in self.directory.glob("*.csv") if p.stem.lower() != "universe")
        self._files = {p.stem.upper(): p for p in files}

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for name in sorted(self._files):
            stat = self._files[name].stat()
            digest.update(f"{name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        universe = self.directory / "universe.csv"
        if universe.exists():
            digest.update(universe.read_bytes())
        return f"csv:{self.directory.name}:{digest.hexdigest()[:12]}"

    def _load(self, symbol: str) -> list[Candle]:
        if symbol in self._bars:
            return self._bars[symbol]
        path = self._files.get(symbol.upper())
        if path is None:
            raise KeyError(f"No CSV for {symbol} in {self.directory}")
        q = DataQuality()
        seen: dict[datetime, Candle] = {}
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    ts = _parse_ts(row["timestamp"])
                    o, h, lo, c = (float(row[k]) for k in ("open", "high", "low", "close"))
                    v = int(float(row.get("volume") or 0))
                except (KeyError, ValueError):
                    q.invalid_bars += 1
                    continue
                if min(o, h, lo, c) <= 0 or h < max(o, c, lo) or lo > min(o, c, h):
                    q.invalid_bars += 1
                    continue
                if not in_session(ts):
                    q.out_of_session_bars += 1
                    continue
                if ts in seen:
                    q.duplicate_bars += 1
                seen[ts] = Candle(
                    symbol=symbol,
                    timeframe=Timeframe.M5,
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=v,
                    is_closed=True,
                )
        bars = [seen[t] for t in sorted(seen)]
        q.bars = len(bars)
        if bars:
            days = {b.timestamp.astimezone(IST).date() for b in bars}
            q.missing_bars = max(0, len(days) * 75 - len(bars))
        self._bars[symbol] = bars
        self._quality[symbol] = q
        return bars

    def quality(self, symbol: str) -> DataQuality:
        self._load(symbol)
        return self._quality[symbol]

    def available_range(self) -> tuple[date, date]:
        firsts, lasts = [], []
        for symbol in self._files:
            bars = self._load(symbol)
            if bars:
                firsts.append(bars[0].timestamp.astimezone(IST).date())
                lasts.append(bars[-1].timestamp.astimezone(IST).date())
        if not firsts:
            raise ValueError("No usable bars in the CSV directory")
        return min(firsts), max(lasts)

    def universe(self) -> Universe:
        path = self.directory / "universe.csv"
        tradable = [s for s in self._files if s != self.benchmark]
        if not path.exists():
            start, _ = self.available_range()
            return Universe(
                periods={s: [(start, None)] for s in tradable},
                point_in_time=False,
                source="static list of the CSV files present",
            )
        periods: dict[str, list[tuple[date, date | None]]] = {}
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                end = (row.get("end_date") or "").strip()
                periods.setdefault(row["symbol"].strip().upper(), []).append(
                    (
                        date.fromisoformat(row["start_date"].strip()),
                        date.fromisoformat(end) if end else None,
                    )
                )
        return Universe(periods=periods, point_in_time=True, source="universe.csv membership")

    def symbols(self) -> list[str]:
        return [s for s in self._files if s != self.benchmark]

    def bars(self, symbol: str, start: date, end: date) -> list[Candle]:
        return [b for b in self._load(symbol) if start <= b.timestamp.astimezone(IST).date() <= end]


class ZerodhaHistorySource(CsvHistorySource):
    """Real 5-minute NSE candles downloaded from Kite Connect's historical
    API into a cache directory (one CSV per symbol, same format as the CSV
    source). Days on which the NIFTY 50 index has no candles were exchange
    holidays. The universe is the requested symbols (not point-in-time)."""

    kind = "zerodha"
    synthetic = False

    def fingerprint(self) -> str:
        return "zerodha:" + super().fingerprint().split(":", 1)[1]

    def holidays(self) -> list[date]:
        index = self._load(self.benchmark) if self.benchmark in self._files else []
        if not index:
            return []
        traded = {b.timestamp.astimezone(IST).date() for b in index}
        first, last = min(traded), max(traded)
        out: list[date] = []
        d = first
        while d <= last:
            if d.weekday() < 5 and d not in traded:
                out.append(d)
            d += timedelta(days=1)
        return out


def expected_bars(days: int) -> int:
    return days * 75


def describe_source(source: HistoricalDataSource) -> dict[str, object]:
    first, last = source.available_range()
    return {
        "kind": source.kind,
        "synthetic": source.synthetic,
        "fingerprint": source.fingerprint(),
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "symbols": len(source.symbols()),
    }


__all__ = [
    "CsvHistorySource",
    "DataQuality",
    "HistoricalDataSource",
    "SyntheticHistorySource",
    "Universe",
    "ZerodhaHistorySource",
    "describe_source",
    "expected_bars",
]
