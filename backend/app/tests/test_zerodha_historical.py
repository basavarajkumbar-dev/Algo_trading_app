"""Historical data: Kite candle parsing, chunking by the per-interval day
limit, rate-limit retries, closed-candles-only, trading days from the index,
the mock provider, and DATA_SOURCE=ZERODHA backtest data (CSV cache)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.backtesting.data import ZerodhaHistorySource
from app.backtesting.runner import make_source
from app.backtesting.schemas import BacktestRequest
from app.brokers.zerodha.backtest_data import download_history
from app.brokers.zerodha.historical import (
    ZerodhaHistoricalDataProvider,
    chunk_ranges,
    parse_kite_timestamp,
)
from app.brokers.zerodha.instruments import InstrumentResolver
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.models import KiteInterval, parse_instrument_dump
from app.market_data.historical import HistoricalDataError, MockHistoricalDataProvider
from app.market_data.schemas import Timeframe
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, MASTER_CSV, FakeKite

IST = ZoneInfo("Asia/Kolkata")
RELIANCE, NIFTY = 738561, 256265


def session_rows(day: date, minutes: int, base: float, n: int | None = None) -> list[list[object]]:
    start = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15)
    count = n if n is not None else 375 // minutes
    rows: list[list[object]] = []
    for i in range(count):
        ts = start + timedelta(minutes=minutes * i)
        o = base + i * 0.5
        rows.append([ts.strftime("%Y-%m-%dT%H:%M:%S+0530"), o, o + 1, o - 1, o + 0.25, 1000 + i])
    return rows


def provider(fake: FakeKite, *, now: datetime, sleeps: list[float] | None = None):
    client = KiteClient(api_key=API_KEY, access_token=ACCESS_TOKEN, transport=fake.transport())
    resolver = InstrumentResolver()
    resolver.load(parse_instrument_dump(MASTER_CSV.encode()).records)

    async def sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    return ZerodhaHistoricalDataProvider(client, resolver, clock=lambda: now, sleep=sleep)


def test_timestamps_and_chunks() -> None:
    assert parse_kite_timestamp("2026-09-30T09:15:00+0530") == datetime(
        2026, 9, 30, 3, 45, tzinfo=UTC
    )
    a = datetime(2026, 1, 1, tzinfo=UTC)
    chunks = chunk_ranges(a, a + timedelta(days=200), KiteInterval.MINUTE_5.max_days)
    assert len(chunks) == 3 and chunks[0][0] == a and chunks[-1][1] == a + timedelta(days=200)
    assert all((b - x) <= timedelta(days=90) for x, b in chunks)
    assert KiteInterval.MINUTE.max_days == 30 and KiteInterval.MINUTE_60.max_days == 365


async def test_candles_are_parsed_validated_and_only_closed_ones_returned() -> None:
    fake = FakeKite()
    day = date(2026, 9, 30)
    rows = session_rows(day, 5, 1400.0)
    rows.insert(3, ["2026-09-30T09:30:00+0530", -1, 1, 1, 1, 5])  # invalid: dropped
    rows.append(["garbage"])  # unparseable: dropped
    fake.candles[(RELIANCE, "5minute")] = rows
    now = datetime(2026, 9, 30, 6, 2, tzinfo=UTC)  # 11:32 IST: the 11:30 candle is forming
    p = provider(fake, now=now)
    candles = await p.get_historical_candles(
        "RELIANCE", Timeframe.M5, now - timedelta(hours=3), now
    )
    assert candles and all(c.is_closed for c in candles)
    assert candles[-1].timestamp == datetime(2026, 9, 30, 5, 55, tzinfo=UTC)  # 11:25 IST
    assert p.invalid_rows == 2
    assert fake.historical_calls[0][:2] == (RELIANCE, "5minute")
    assert fake.historical_calls[0][2].startswith("2026-09-30 08:32")  # IST wall clock


async def test_long_ranges_are_chunked_and_holidays_have_no_candles() -> None:
    fake = FakeKite()
    fake.candles[(RELIANCE, "minute")] = session_rows(date(2026, 9, 1), 1, 1400.0, n=5)
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    p = provider(fake, now=now)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    candles = await p.get_historical_candles("RELIANCE", Timeframe.M1, start, now)
    assert len(fake.historical_calls) == 4  # 92 days at 30 days per request
    assert len(candles) == 5


async def test_rate_limits_are_retried_with_backoff() -> None:
    fake = FakeKite()
    fake.candles[(NIFTY, "day")] = [
        ["2026-09-29T00:00:00+0530", 25000, 25100, 24900, 25050, 0],
        ["2026-09-30T00:00:00+0530", 25050, 25150, 24950, 25100, 0],
    ]
    limit = {"status": "error", "message": "Too many requests", "error_type": "NetworkException"}
    fake.status_next = [(429, limit)]
    sleeps: list[float] = []
    p = provider(fake, now=datetime(2026, 10, 1, 12, 0, tzinfo=UTC), sleeps=sleeps)
    days = await p.get_trading_days(date(2026, 9, 28), date(2026, 9, 30))
    assert days == [date(2026, 9, 29), date(2026, 9, 30)]  # 28th: a holiday in this data
    assert p.rate_limited == 1 and sleeps
    fake.status_next = [(429, limit)] * 3
    with pytest.raises(HistoricalDataError):
        await p.get_daily_candles("RELIANCE", date(2026, 9, 1), date(2026, 9, 30))


async def test_unknown_symbols_are_an_error_not_a_guess() -> None:
    p = provider(FakeKite(), now=datetime(2026, 10, 1, tzinfo=UTC))
    with pytest.raises(HistoricalDataError):
        await p.get_recent_candles("NOT_A_SYMBOL", Timeframe.M5, 10)


async def test_mock_history_is_labelled_simulated() -> None:
    mock = MockHistoricalDataProvider()
    assert mock.simulated and mock.source == "MOCK"
    first, last = mock.available_range()
    end = datetime.combine(last, datetime.min.time(), tzinfo=IST) + timedelta(hours=16)
    bars = await mock.get_historical_candles(
        "RELIANCE", Timeframe.M15, end - timedelta(days=3), end
    )
    assert bars and all(b.timeframe == Timeframe.M15 for b in bars)
    with pytest.raises(HistoricalDataError):
        await mock.get_historical_candles("RELIANCE", Timeframe.M1, end - timedelta(days=1), end)
    assert await mock.get_trading_days(last - timedelta(days=6), last)


async def test_zerodha_backtest_data_is_cached_as_csv(tmp_path: Path) -> None:
    fake = FakeKite()
    days = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]
    for token, base in ((RELIANCE, 1400.0), (NIFTY, 25000.0)):
        fake.candles[(token, "5minute")] = [r for d in days for r in session_rows(d, 5, base)]
    p = provider(fake, now=datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
    path = await download_history(
        p, ["RELIANCE"], date(2026, 9, 28), date(2026, 9, 30), tmp_path, warmup_days=8
    )
    assert sorted(f.name for f in path.glob("*.csv")) == ["NIFTY50.csv", "RELIANCE.csv"]
    calls = len(fake.historical_calls)
    again = await download_history(
        p, ["RELIANCE"], date(2026, 9, 28), date(2026, 9, 30), tmp_path, warmup_days=8
    )
    assert again == path and len(fake.historical_calls) == calls  # cached
    req = BacktestRequest(
        symbols=["RELIANCE"],
        start_date=date(2026, 9, 28),
        end_date=date(2026, 9, 30),
        data_source={"kind": "zerodha"},  # type: ignore[arg-type]
    )
    source = make_source(req, str(path))
    assert isinstance(source, ZerodhaHistorySource) and source.kind == "zerodha"
    assert not source.synthetic and source.fingerprint().startswith("zerodha:")
    assert len(source.bars("RELIANCE", date(2026, 9, 28), date(2026, 9, 30))) == 3 * 75
    assert source.holidays() == []
