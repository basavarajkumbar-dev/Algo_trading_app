"""Instrument master: parsing and validation of the Kite dump, idempotent
upserts (no duplicates), deactivation, sync-run records, and resolution of
equities, indices, futures and options. Needs Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select

from app.brokers.zerodha.instruments import (
    InstrumentNotFound,
    InstrumentRepository,
    InstrumentResolver,
    InstrumentService,
)
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.models import parse_instrument_dump
from app.database.models.market_data import BrokerInstrument, InstrumentSyncRun
from app.database.session import async_session_factory
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, MASTER_CSV, FakeKite

NOW = datetime(2026, 10, 1, 3, 30, tzinfo=UTC)  # 09:00 IST


@pytest.fixture
async def clean() -> AsyncIterator[None]:
    async def wipe() -> None:
        async with async_session_factory() as s:
            await s.execute(delete(BrokerInstrument))
            await s.execute(delete(InstrumentSyncRun))
            await s.commit()

    await wipe()
    yield
    await wipe()


def service(fake: FakeKite) -> InstrumentService:
    client = KiteClient(api_key=API_KEY, access_token=ACCESS_TOKEN, transport=fake.transport())
    repo = InstrumentRepository(async_session_factory)
    return InstrumentService(
        client,
        repo,
        InstrumentResolver(repository=repo),
        exchanges=["NSE", "NFO"],
        clock=lambda: NOW,
    )


def test_parser_validates_and_counts_bad_rows() -> None:
    parsed = parse_instrument_dump(MASTER_CSV.encode())
    assert parsed.total_rows == 11 and parsed.invalid_rows == 1
    assert len(parsed.records) == 10 and parsed.invalid_examples
    nifty = next(r for r in parsed.records if r.tradingsymbol == "NIFTY 50")
    assert nifty.is_index and nifty.instrument_token == 256265
    opt = next(r for r in parsed.records if r.tradingsymbol == "NIFTY2610625000CE")
    assert opt.expiry == date(2026, 10, 6) and opt.strike == 25000 and opt.lot_size == 75
    dup = MASTER_CSV.encode() + b"738561,2885,RELIANCE2,X,0,,0,0.1,1,EQ,NSE,NSE\n"
    assert parse_instrument_dump(dup).invalid_rows == 2  # a repeated token is rejected


async def test_sync_upserts_without_duplicates_and_records_runs(clean: None) -> None:
    fake = FakeKite()
    fake.instruments_csv = MASTER_CSV
    svc = service(fake)
    first = await svc.sync_instruments()
    assert first.ok and first.rows_valid == 10 and first.rows_invalid == 1
    assert first.inserted == 10 and first.updated == 0
    second = await svc.sync_instruments()
    assert second.ok and second.inserted == 0 and second.updated == 10
    async with async_session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(BrokerInstrument)) == 10
        runs = (await s.execute(select(InstrumentSyncRun))).scalars().all()
    assert len(runs) == 2 and {r.status for r in runs} == {"SUCCESS"}
    assert all(r.rows_invalid == 1 for r in runs)
    status = await svc.status()
    assert status["status"] == "SYNCED" and status["in_memory"] == 5  # NSE cash + indices


async def test_instruments_that_disappear_are_deactivated(clean: None) -> None:
    fake = FakeKite()
    fake.instruments_csv = MASTER_CSV
    svc = service(fake)
    await svc.sync_instruments()
    fake.instruments_csv = (
        "\n".join(ln for ln in MASTER_CSV.splitlines() if not ln.startswith("408065")) + "\n"
    )
    result = await svc.sync_instruments()
    assert result.deactivated == 1
    with pytest.raises(InstrumentNotFound):
        svc.resolver.resolve_nse_equity("INFY")


async def test_a_failed_sync_is_recorded_and_keeps_the_old_master(clean: None) -> None:
    fake = FakeKite()
    fake.instruments_csv = MASTER_CSV
    svc = service(fake)
    await svc.sync_instruments()
    fake.down = True
    failed = await svc.sync_instruments()
    assert not failed.ok and failed.error
    assert svc.resolver.resolve_nse_equity("RELIANCE").instrument_token == 738561
    async with async_session_factory() as s:
        statuses = (await s.execute(select(InstrumentSyncRun.status))).scalars().all()
    assert sorted(statuses) == ["FAILED", "SUCCESS"]


async def test_resolution_of_every_instrument_kind(clean: None) -> None:
    fake = FakeKite()
    fake.instruments_csv = MASTER_CSV
    svc = service(fake)
    await svc.sync_instruments()
    r = svc.resolver
    assert r.resolve_nse_equity("RELIANCE").instrument_token == 738561
    assert r.resolve_index("NIFTY50").instrument_token == 256265  # app alias
    assert r.resolve_index("BANKNIFTY").tradingsymbol == "NIFTY BANK"
    assert r.resolve_app_symbol("TCS").instrument_token == 2953217
    assert r.find_by_token(256265).tradingsymbol == "NIFTY 50"  # type: ignore[union-attr]
    assert r.find_by_symbol("INFY") is not None
    with pytest.raises(InstrumentNotFound):
        r.resolve_nse_equity("NIFTY 50")  # an index is not an equity
    fut = await r.resolve_future("NIFTY", today=date(2026, 10, 1))
    assert fut.tradingsymbol == "NIFTY26OCTFUT"  # nearest expiry
    opt = await r.resolve_option("NIFTY", strike=25000, option_type="PE", today=date(2026, 10, 1))
    assert opt.instrument_token == 10561026
    nfo = await r.resolve_nfo_instrument("NIFTY26NOVFUT")
    assert nfo.expiry == date(2026, 11, 24)
    # a fresh resolver reloads from the database (no download)
    fresh = InstrumentResolver(repository=InstrumentRepository(async_session_factory))
    assert await fresh.reload() == 5


async def test_ensure_fresh_downloads_once_per_day(clean: None) -> None:
    fake = FakeKite()
    fake.instruments_csv = MASTER_CSV
    svc = service(fake)
    assert await svc.ensure_fresh()
    calls = sum(1 for _, p, _, _ in fake.requests if p.startswith("/instruments"))
    assert await svc.ensure_fresh()  # same morning: no second download
    assert sum(1 for _, p, _, _ in fake.requests if p.startswith("/instruments")) == calls
