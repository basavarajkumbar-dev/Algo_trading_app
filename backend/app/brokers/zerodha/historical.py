"""ZerodhaHistoricalDataProvider: Kite Connect historical candles (Phase 13).

GET /instruments/historical/{instrument_token}/{interval}?from=&to=
returns {"candles": [[timestamp, open, high, low, close, volume, (oi)], ...]}
with timestamps like "2026-09-30T09:15:00+0530" (candle start, IST).

* Chunking: each request covers at most the interval's published day
  limit (minute 30, 3/5/10minute 90, 15/30minute 180, 60minute 365, day
  2000), so long ranges are split and stitched; duplicates at chunk
  boundaries are dropped.
* Rate limits: the client's historical limiter keeps us at 3 requests/s;
  a 429 is retried after a backoff (max 3 tries), other errors surface.
* Holidays / missing data: Kite simply returns no candles for a closed
  day; nothing is invented. Trading days come from the NIFTY 50 index's
  daily candles, the exchange's own record of which days traded.
* Candles that are still in progress are dropped (closed candles only);
  invalid rows (non-positive prices, high below low) are counted and
  skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.brokers.zerodha.exceptions import KiteError, KiteNetworkError, KiteRateLimitError
from app.brokers.zerodha.instruments import InstrumentNotFound, InstrumentResolver
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.models import KiteInterval
from app.core.logging import get_logger
from app.market_data.base import utcnow
from app.market_data.candle_builder import NSE_SESSION, SessionCalendar
from app.market_data.historical import (
    DailyCandle,
    HistoricalDataError,
    HistoricalDataProvider,
    closed_only,
)
from app.market_data.schemas import Candle, Timeframe
from app.market_data.universe import NIFTY_INDEX_SYMBOL

logger = get_logger(__name__)

INTERVALS: dict[Timeframe, KiteInterval] = {
    Timeframe.M1: KiteInterval.MINUTE,
    Timeframe.M3: KiteInterval.MINUTE_3,
    Timeframe.M5: KiteInterval.MINUTE_5,
    Timeframe.M15: KiteInterval.MINUTE_15,
    Timeframe.M30: KiteInterval.MINUTE_30,
    Timeframe.M60: KiteInterval.MINUTE_60,
}


def parse_kite_timestamp(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        ts = raw
    else:
        text = str(raw).strip()
        if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":  # +0530 -> +05:30
            text = f"{text[:-2]}:{text[-2:]}"
        ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=NSE_SESSION.zone)
    return ts.astimezone(UTC)


def chunk_ranges(start: datetime, end: datetime, max_days: int) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    step = timedelta(days=max_days)
    while cursor <= end:
        stop = min(cursor + step - timedelta(seconds=1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(seconds=1)
    return out


class ZerodhaHistoricalDataProvider(HistoricalDataProvider):
    source = "ZERODHA"
    simulated = False

    def __init__(
        self,
        client: KiteClient,
        resolver: InstrumentResolver,
        *,
        session: SessionCalendar = NSE_SESSION,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = 3,
    ) -> None:
        super().__init__(session=session)
        self.client = client
        self.resolver = resolver
        self._clock = clock
        self._sleep = sleep
        self.max_retries = max_retries
        self.requests = 0
        self.invalid_rows = 0
        self.rate_limited = 0
        self._trading_days: dict[tuple[date, date], list[date]] = {}

    def _token(self, symbol: str) -> int:
        try:
            return self.resolver.resolve_app_symbol(symbol).instrument_token
        except InstrumentNotFound as exc:
            raise HistoricalDataError(str(exc)) from exc

    async def _request(
        self, token: int, interval: KiteInterval, start: datetime, end: datetime
    ) -> list[list[Any]]:
        zone = self.session.zone
        for attempt in range(1, self.max_retries + 1):
            try:
                self.requests += 1
                return await self.client.historical(
                    token, interval.value, start.astimezone(zone), end.astimezone(zone)
                )
            except KiteRateLimitError:
                self.rate_limited += 1
                if attempt == self.max_retries:
                    raise
                await self._sleep(1.0 * attempt)
            except KiteNetworkError:
                if attempt == self.max_retries:
                    raise
                await self._sleep(0.5 * attempt)
        return []  # pragma: no cover - the loop returns or raises

    async def fetch_rows(
        self, symbol: str, interval: KiteInterval, start: datetime, end: datetime
    ) -> list[tuple[datetime, float, float, float, float, int]]:
        if end < start:
            return []
        token = self._token(symbol)
        rows: dict[datetime, tuple[datetime, float, float, float, float, int]] = {}
        for a, b in chunk_ranges(start, end, interval.max_days):
            try:
                raw = await self._request(token, interval, a, b)
            except KiteError as exc:
                raise HistoricalDataError(
                    f"historical {symbol} {interval.value}: {type(exc).__name__}: {exc}"
                ) from exc
            for item in raw:
                try:
                    ts = parse_kite_timestamp(item[0])
                    o, h, lo, c = (float(item[i]) for i in range(1, 5))
                    v = int(item[5]) if len(item) > 5 and item[5] is not None else 0
                except (TypeError, ValueError, IndexError):
                    self.invalid_rows += 1
                    continue
                if min(o, h, lo, c) <= 0 or h < max(o, c, lo) or lo > min(o, c, h) or v < 0:
                    self.invalid_rows += 1
                    continue
                if start <= ts <= end:
                    rows[ts] = (ts, o, h, lo, c, v)
        return [rows[t] for t in sorted(rows)]

    async def get_historical_candles(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        interval = INTERVALS[timeframe]
        rows = await self.fetch_rows(symbol, interval, start, end)
        candles = [
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=v,
                is_closed=False,
            )
            for ts, o, h, lo, c, v in rows
        ]
        return closed_only(candles, timeframe, self._clock())

    async def get_daily_candles(self, symbol: str, start: date, end: date) -> list[DailyCandle]:
        zone = self.session.zone
        rows = await self.fetch_rows(
            symbol,
            KiteInterval.DAY,
            datetime.combine(start, time(0, 0), tzinfo=zone),
            datetime.combine(end, time(23, 59, 59), tzinfo=zone),
        )
        today = self._clock().astimezone(zone).date()
        now = self._clock()
        out: list[DailyCandle] = []
        for ts, o, h, lo, c, v in rows:
            day = ts.astimezone(zone).date()
            if day == today and now < self.session.close_on(ts):
                continue  # today's session is not over: not a closed daily candle
            out.append(
                DailyCandle(symbol=symbol, day=day, open=o, high=h, low=lo, close=c, volume=v)
            )
        return out

    async def get_trading_days(self, start: date, end: date) -> list[date]:
        key = (start, end)
        if key not in self._trading_days:
            daily = await self.get_daily_candles(NIFTY_INDEX_SYMBOL, start, end)
            self._trading_days[key] = [d.day for d in daily]
        return list(self._trading_days[key])


__all__ = ["INTERVALS", "ZerodhaHistoricalDataProvider", "chunk_ranges", "parse_kite_timestamp"]
