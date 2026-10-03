"""Historical candles for warm-up, analysis and backtests (Phase 13).

    HistoricalDataProvider          the interface
    MockHistoricalDataProvider      deterministic simulated history (the
                                    backtester's synthetic NSE-session
                                    series); clearly SIMULATED
    ZerodhaHistoricalDataProvider   Kite Connect historical API
                                    (app.brokers.zerodha.historical)

Every implementation returns closed candles only (a candle whose period has
not ended is dropped), timestamps are UTC candle starts, and a day without
data (weekend, exchange holiday) simply has no candles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from app.market_data.base import MarketDataProviderError, utcnow
from app.market_data.candle_builder import NSE_SESSION, SessionCalendar, aggregate_candles
from app.market_data.schemas import Candle, Timeframe


class HistoricalDataError(MarketDataProviderError):
    pass


class DailyCandle(BaseModel):
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(default=0, ge=0)


class HistoricalDataProvider(ABC):
    source: str = "UNKNOWN"
    simulated: bool = False

    def __init__(self, *, session: SessionCalendar = NSE_SESSION) -> None:
        self.session = session

    @abstractmethod
    async def get_historical_candles(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Closed candles starting in [start, end] (timezone-aware)."""

    @abstractmethod
    async def get_daily_candles(self, symbol: str, start: date, end: date) -> list[DailyCandle]:
        """One candle per trading day in [start, end]."""

    @abstractmethod
    async def get_trading_days(self, start: date, end: date) -> list[date]:
        """Days in [start, end] the exchange was open (holidays excluded)."""

    async def get_intraday_candles(
        self, symbol: str, timeframe: Timeframe, day: date | None = None
    ) -> list[Candle]:
        """One session's candles (today's so far by default)."""

        tz = self.session.zone
        day = day or utcnow().astimezone(tz).date()
        open_ = datetime.combine(day, self.session.open, tzinfo=tz)
        close = datetime.combine(day, self.session.close, tzinfo=tz)
        return await self.get_historical_candles(symbol, timeframe, open_, close)

    async def get_recent_candles(
        self, symbol: str, timeframe: Timeframe, limit: int, *, now: datetime | None = None
    ) -> list[Candle]:
        """The last `limit` closed candles (warm-up)."""

        now = now or utcnow()
        per_session = -(-self.session.minutes // timeframe.minutes)
        sessions = -(-limit // per_session) + 1
        calendar_days = sessions * 7 // 5 + 7  # weekends and a few holidays
        candles = await self.get_historical_candles(
            symbol, timeframe, now - timedelta(days=calendar_days), now
        )
        return candles[-limit:]


class MockHistoricalDataProvider(HistoricalDataProvider):
    """Simulated history: the backtester's deterministic synthetic series
    (5-minute NSE session bars on weekdays), aggregated to 15/30/60m with
    session anchoring. 1m and 3m are not available. Never real market data."""

    source = "MOCK"
    simulated = True

    def __init__(self, *, seed: int = 42, holidays: list[date] | None = None) -> None:
        super().__init__()
        from app.backtesting.data import SyntheticHistorySource

        self._synthetic = SyntheticHistorySource(seed=seed, holidays=holidays or [])

    def available_range(self) -> tuple[date, date]:
        return self._synthetic.available_range()

    async def get_historical_candles(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        if timeframe.minutes % 5:
            raise HistoricalDataError("simulated history has 5-minute resolution")
        tz = self.session.zone
        try:
            bars = self._synthetic.bars(
                symbol, start.astimezone(tz).date(), end.astimezone(tz).date()
            )
        except KeyError as exc:
            raise HistoricalDataError(f"no simulated history for {symbol}") from exc
        bars = [b for b in bars if start <= b.timestamp <= end]
        if timeframe is not Timeframe.M5:
            bars = aggregate_candles(bars, timeframe, self.session)
        return bars

    async def get_daily_candles(self, symbol: str, start: date, end: date) -> list[DailyCandle]:
        tz = self.session.zone
        by_day: dict[date, list[Candle]] = {}
        for bar in self._synthetic.bars(symbol, start, end):
            by_day.setdefault(bar.timestamp.astimezone(tz).date(), []).append(bar)
        return [
            DailyCandle(
                symbol=symbol,
                day=day,
                open=bars[0].open,
                high=max(b.high for b in bars),
                low=min(b.low for b in bars),
                close=bars[-1].close,
                volume=sum(b.volume for b in bars),
            )
            for day, bars in sorted(by_day.items())
        ]

    async def get_trading_days(self, start: date, end: date) -> list[date]:
        first, last = self._synthetic.available_range()
        from app.backtesting.calendar import trading_days

        return trading_days(max(start, first), min(end, last), self._synthetic.holidays())


def closed_only(candles: list[Candle], timeframe: Timeframe, now: datetime) -> list[Candle]:
    """Drop the candle still in progress at `now` and mark the rest closed."""

    out: list[Candle] = []
    for c in candles:
        end = c.timestamp + timedelta(minutes=timeframe.minutes)
        if end <= now or NSE_SESSION.close_on(c.timestamp) <= now:
            c.is_closed = True
            out.append(c)
    return out


__all__ = [
    "DailyCandle",
    "HistoricalDataError",
    "HistoricalDataProvider",
    "MockHistoricalDataProvider",
    "closed_only",
]
