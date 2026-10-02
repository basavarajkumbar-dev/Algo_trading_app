"""Signal storage, the scan service and the scanner / signal API.

Needs a reachable Postgres (skipped otherwise, see conftest)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.models.signal import Signal
from app.database.session import async_session_factory
from app.main import app
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.paper_provider import PaperMarketDataProvider
from app.market_data.service import MarketDataService
from app.price_action.service import PriceActionService
from app.signals.repository import get_signal, list_signals, sync_scan
from app.signals.schemas import SignalDirection, SignalStatus, TradingSignal
from app.signals.service import SignalService
from app.strategies.service import StrategyService
from app.tests.test_signals import make, signal_for, strong_trend_long

T0 = datetime(2026, 1, 6, 9, 0, tzinfo=UTC)


def strong(symbol: str, score: float = 92.0, **extra) -> TradingSignal:
    return make(symbol, score, **extra)


# --- lifecycle ------------------------------------------------------------------------


async def test_new_setups_are_stored_with_their_explanation(db_session) -> None:
    signal = signal_for(strong_trend_long()).model_copy(update={"symbol": "RELIANCE"})
    result = await sync_scan(
        db_session, [signal], now=T0, store_min_score=70, ranks={signal.key: 1}
    )
    assert result.created == 1 and result.updated == 0 and result.expired == 0
    record = await get_signal(db_session, result.ids[signal.key])
    assert record is not None
    stored = record.signal
    assert stored.id == result.ids[signal.key]
    assert stored.status == SignalStatus.ACTIVE
    assert (stored.entry, stored.stop_loss, stored.target) == (
        signal.entry,
        signal.stop_loss,
        signal.target,
    )
    assert stored.breakdown.total == signal.score
    assert stored.reasons == signal.reasons
    assert stored.strategy_checks == signal.strategy_checks
    assert stored.strategy_version == signal.strategy_version
    assert record.last_rank == 1 and record.times_seen == 1


async def test_a_persisting_setup_keeps_its_original_values(db_session) -> None:
    first = strong("TCS", 90.0)
    created = await sync_scan(db_session, [first], now=T0, store_min_score=70)
    later = first.model_copy(update={"score": 87.0, "entry": 999.0})
    again = await sync_scan(
        db_session, [later], now=T0 + timedelta(minutes=5), store_min_score=70, ranks={later.key: 3}
    )
    assert again.created == 0 and again.updated == 1
    assert again.ids[first.key] == created.ids[first.key]
    record = await get_signal(db_session, created.ids[first.key])
    assert record is not None
    assert record.signal.score == 90.0 and record.signal.entry == first.entry  # frozen at creation
    assert record.last_score == 87.0 and record.last_rank == 3 and record.times_seen == 2
    assert record.last_seen_at == T0 + timedelta(minutes=5)


async def test_vanished_setups_expire_and_reappear_as_new_signals(db_session) -> None:
    signal = strong("INFY")
    first = await sync_scan(db_session, [signal], now=T0, store_min_score=70)
    gone = await sync_scan(db_session, [], now=T0 + timedelta(minutes=5), store_min_score=70)
    assert gone.expired == 1
    record = await get_signal(db_session, first.ids[signal.key])
    assert record is not None and record.signal.status == SignalStatus.EXPIRED
    assert record.expired_at == T0 + timedelta(minutes=5)
    back = await sync_scan(db_session, [signal], now=T0 + timedelta(minutes=10), store_min_score=70)
    assert back.created == 1 and back.ids[signal.key] != first.ids[signal.key]


async def test_weak_candidates_are_not_stored(db_session) -> None:
    result = await sync_scan(
        db_session, [strong("A", 65.0), strong("B", 75.0)], now=T0, store_min_score=70
    )
    assert result.created == 1
    assert ("A", "TREND_CONTINUATION", "BUY") not in result.ids


async def test_database_allows_one_active_signal_per_setup(db_session) -> None:
    await sync_scan(db_session, [strong("HDFC")], now=T0, store_min_score=70)
    row = (await db_session.execute(select(Signal))).scalars().one()
    duplicate = Signal(
        **{
            c.name: getattr(row, c.name)
            for c in Signal.__table__.columns
            if c.name not in ("id", "created_at", "updated_at")
        },
        id=uuid.uuid4(),
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_list_filters(db_session) -> None:
    await sync_scan(
        db_session,
        [strong("A", 92.0), strong("B", 75.0), strong("C", 88.0, direction=SignalDirection.SELL)],
        now=T0,
        store_min_score=70,
    )
    everything, total = await list_signals(db_session)
    assert total == 3 and len(everything) == 3
    _, executable = await list_signals(db_session, executable=True)
    assert executable == 2
    watch, _ = await list_signals(db_session, category="WATCH")
    assert [r.signal.symbol for r in watch] == ["B"]
    one, _ = await list_signals(db_session, symbol="C")
    assert one[0].signal.direction == SignalDirection.SELL
    page, total = await list_signals(db_session, limit=1, offset=1)
    assert len(page) == 1 and total == 3


# --- the scan service with a real pipeline -------------------------------------------------


async def test_signal_service_runs_the_whole_pipeline() -> None:
    provider = PaperMarketDataProvider(
        delegate=MockMarketDataProvider(
            rng_seed=4, tick_interval_seconds=10.0, regime_rotation_seconds=None
        )
    )
    watchlist = ["NIFTY50", "RELIANCE", "TCS", "INFY", "HDFCBANK"]
    market_data = MarketDataService(provider, watchlist=watchlist, warmup_candles=120)
    await market_data.start()
    price_action = PriceActionService(market_data, refresh_seconds=60)
    strategies = StrategyService(
        market_data, price_action, benchmark_symbol="NIFTY50", poll_seconds=60
    )
    service = SignalService(
        market_data,
        strategies,
        benchmark_symbol="NIFTY50",
        session_factory=async_session_factory,
        poll_seconds=60,
    )
    try:
        await price_action.start()
        await strategies.start()
        await service.start()
        scan = service.get_scan()
        assert scan is not None
        assert {r.symbol for r in scan.rows} == set(watchlist) - {"NIFTY50"}
        assert scan.market_context.available
        status = service.status()
        assert status.persistence_ok and status.last_error is None
        assert status.strategy_refresh == strategies.refresh_count

        count = status.scan_count
        await service.refresh()  # nothing new upstream: no rescan
        assert service.status().scan_count == count
        await price_action.refresh()
        await strategies.refresh()
        await service.refresh()
        assert service.status().scan_count == count + 1
    finally:
        await service.stop()
        await strategies.stop()
        await price_action.stop()
        await market_data.stop()


async def test_signal_service_keeps_scanning_without_a_database() -> None:
    def broken_factory():
        raise ConnectionError("database down")

    provider = PaperMarketDataProvider(
        delegate=MockMarketDataProvider(
            rng_seed=4, tick_interval_seconds=10.0, regime_rotation_seconds=None
        )
    )
    market_data = MarketDataService(provider, watchlist=["NIFTY50", "RELIANCE"], warmup_candles=120)
    await market_data.start()
    price_action = PriceActionService(market_data, refresh_seconds=60)
    strategies = StrategyService(
        market_data, price_action, benchmark_symbol="NIFTY50", poll_seconds=60
    )
    service = SignalService(
        market_data,
        strategies,
        benchmark_symbol="NIFTY50",
        session_factory=broken_factory,
        poll_seconds=60,
    )
    try:
        await price_action.start()
        await strategies.start()
        await service.start()
        assert service.get_scan() is not None
        status = service.status()
        assert not status.persistence_ok and "database down" in (status.persistence_error or "")
    finally:
        await service.stop()
        await strategies.stop()
        await price_action.stop()
        await market_data.stop()


# --- API -----------------------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_scanner_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/scanner")
    assert response.status_code == 200
    body = response.json()
    scan = body["scan"]
    assert len(scan["rows"]) == 50  # the 50 constituents; the index is market context
    assert scan["filtered_out"] == {"NIFTY50": "index: used as market context, not scanned"}
    assert scan["market_context"]["benchmark_symbol"] == "NIFTY50"
    assert sum(scan["status_counts"].values()) == 50
    assert body["status"]["executable_threshold"] == 85.0
    assert "not proof of profitability" in scan["ranking_note"]
    row = scan["rows"][0]
    for key in (
        "rank",
        "symbol",
        "price",
        "change_percent",
        "status",
        "score",
        "signal_strategy",
        "regime",
    ):
        assert key in row


def test_signals_endpoints(client: TestClient) -> None:
    async def seed() -> uuid.UUID:
        async with async_session_factory() as session:
            result = await sync_scan(session, [strong("ZZTEST", 93.0)], now=T0, store_min_score=70)
        return next(iter(result.ids.values()))

    # Seed on the app's own event loop: asyncpg connections are bound to the
    # loop that opened them, and the TestClient runs the app on its own loop.
    assert client.portal is not None
    signal_id = client.portal.call(seed)

    listing = client.get("/api/v1/signals?symbol=zztest").json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["signal"]["id"] == str(signal_id)
    assert "not orders" in listing["note"]

    detail = client.get(f"/api/v1/signals/{signal_id}")
    assert detail.status_code == 200
    signal = detail.json()["signal"]
    for key in (
        "symbol",
        "direction",
        "strategy",
        "entry",
        "stop_loss",
        "target",
        "quantity_candidate",
        "score",
        "market_regime",
        "reasons",
        "timestamp",
        "strategy_version",
        "breakdown",
    ):
        assert key in signal, key
    assert signal["category"] == "ELITE" and signal["executable"] is True

    assert client.get("/api/v1/signals?category=ELITE&status=ACTIVE").json()["total"] >= 1
    assert client.get(f"/api/v1/signals/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/v1/signals/not-a-uuid").status_code == 422
    assert client.get("/api/v1/signals?category=AMAZING").status_code == 422
    config = client.get("/api/v1/signals/config").json()
    assert config["scoring"]["weights"]["market_alignment"] == 15.0
    assert config["categories"]["executable_threshold"] == 85.0
