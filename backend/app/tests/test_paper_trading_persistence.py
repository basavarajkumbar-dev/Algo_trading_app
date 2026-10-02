"""Paper trading persistence, the trading service and the /trading API.

Needs a reachable Postgres (skipped otherwise, see conftest)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.database.models.audit_log import AuditLog
from app.database.models.paper_trading import PaperOrderRecord, PaperTradeRecord
from app.database.models.signal import Signal
from app.database.models.system_event import SystemEvent
from app.database.session import async_session_factory
from app.execution.paper_broker import PaperBrokerConfig
from app.main import app
from app.market_data.schemas import MarketDataProviderStatus
from app.risk.config import MarketSessionConfig, RiskLimits, SessionMode
from app.risk.schemas import RejectionReason as R
from app.signals.repository import sync_scan
from app.tests.paper_helpers import Clock, intent
from app.tests.test_signals import make
from app.trading.repository import list_orders, list_trades, restore
from app.trading.schemas import ExitReason, TradeStatus
from app.trading.service import SignalNotFound, TradingService
from app.trading.state import TradingState

# --- a controllable market --------------------------------------------------------------


@dataclass
class Snap:
    symbol: str
    price: float
    timestamp: datetime


class FakeMarket:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.prices: dict[str, float] = {}
        self.is_running = True
        self.provider = MarketDataProviderStatus.CONNECTED

    def set(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    def get_snapshot(self, symbol: str) -> Snap | None:
        if symbol not in self.prices:
            return None
        return Snap(symbol, self.prices[symbol], self.clock())

    async def get_provider_status(self) -> MarketDataProviderStatus:
        return self.provider


def service(
    clock: Clock,
    market: FakeMarket,
    *,
    signals=None,
    session_factory=async_session_factory,
    capital: float = 100_000.0,
    **limits,
) -> TradingService:
    return TradingService(
        market,  # type: ignore[arg-type]
        signals,
        starting_capital=capital,
        limits=RiskLimits(**limits),
        broker_config=PaperBrokerConfig(slippage_bps=0),
        session_factory=session_factory,
        clock=clock,
    )


async def step(svc: TradingService, market: FakeMarket, clock: Clock, symbol: str, px: float):
    clock.advance(1)
    market.set(symbol, px)
    await svc.tick()


# --- service + storage -----------------------------------------------------------------------


async def test_service_runs_the_lifecycle_and_stores_everything(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    svc = service(clock, market)
    await svc._restore()

    decision = await svc.execute_intent(intent())
    assert decision.approved, decision.summary
    status = await svc.status()
    assert status.state == TradingState.POSITION_ACTIVE
    assert status.active_trade is not None and status.active_trade.status == TradeStatus.OPEN
    assert len(status.working_orders) == 2  # stop + target
    assert status.positions[0].quantity == 50
    assert status.persistence_ok and status.system_healthy

    await step(svc, market, clock, "RELIANCE", 1010)
    await step(svc, market, clock, "RELIANCE", 1020)
    assert svc.engine.state == TradingState.IDLE

    trades, total = await list_trades(db_session)
    assert total == 1
    t = trades[0]
    assert t.status == TradeStatus.CLOSED and t.exit_reason == ExitReason.TARGET
    assert t.net_pnl == pytest.approx(1000 - t.charges)
    row = await db_session.get(PaperTradeRecord, t.id)
    assert row is not None and float(row.net_pnl) == t.net_pnl

    orders, n = await list_orders(db_session, trade_id=t.id)
    assert n == 3
    by_purpose = {o.purpose: o.order for o in orders}
    assert by_purpose["ENTRY"].status.value == "FILLED"
    assert by_purpose["TARGET"].status.value == "FILLED"
    assert by_purpose["STOP_LOSS"].status.value == "CANCELLED"
    assert by_purpose["ENTRY"].fills and by_purpose["ENTRY"].charges.total > 0

    events = (
        (await db_session.execute(select(SystemEvent.event_type).order_by(SystemEvent.created_at)))
        .scalars()
        .all()
    )
    assert "PAPER_ORDER_PLACED" in events and "PAPER_TRADE_CLOSED" in events


async def test_rejections_are_stored_as_events_without_orders(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    svc = service(clock, market)
    await svc._restore()
    decision = await svc.execute_intent(intent(score=10))
    assert decision.rejection_reasons == [R.SIGNAL_NOT_EXECUTABLE]
    count = await db_session.scalar(select(func.count()).select_from(PaperOrderRecord))
    assert count == 0
    event = (
        await db_session.execute(
            select(SystemEvent).where(SystemEvent.event_type == "PAPER_RISK_REJECTED")
        )
    ).scalar_one()
    assert event.severity == "WARNING"
    assert event.event_metadata["reasons"] == ["SIGNAL_NOT_EXECUTABLE"]


async def test_unhealthy_feed_or_storage_blocks_entries() -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    svc = service(clock, market)
    await svc._restore()
    market.provider = MarketDataProviderStatus.DISCONNECTED
    d = await svc.execute_intent(intent())
    assert d.rejection_reasons == [R.SYSTEM_UNHEALTHY]

    def broken_factory():
        raise OSError("database is down")

    market.provider = MarketDataProviderStatus.CONNECTED
    broken = service(clock, market, session_factory=broken_factory)
    await broken._restore()
    healthy, detail = await broken.health()
    assert not healthy and "trade storage unavailable" in detail
    d2 = await broken.execute_intent(intent())
    assert d2.rejection_reasons == [R.SYSTEM_UNHEALTHY]


async def test_stale_market_data_blocks_entries() -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    svc = service(clock, market)
    await svc._restore()
    stale = Snap("RELIANCE", 1000, clock() - timedelta(minutes=5))
    market.get_snapshot = lambda symbol: stale  # type: ignore[method-assign]
    d = await svc.execute_intent(intent())
    assert d.rejection_reasons == [R.DATA_STALE]


async def test_restart_restores_capital_counters_and_interrupts_open_trades(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    first = service(clock, market)
    await first._restore()
    i1 = intent()
    assert (await first.execute_intent(i1)).approved
    await step(first, market, clock, "RELIANCE", 985)  # stop loss
    loss = first.engine.closed[-1].net_pnl
    assert loss < 0
    i2 = intent(entry=985, stop=975, target=1005)
    assert (await first.execute_intent(i2)).approved  # left open
    assert first.engine.state == TradingState.POSITION_ACTIVE

    # "restart"
    second = service(clock, market)
    await second._restore()
    status = await second.status()
    assert status.interrupted_trades == 1
    assert status.restored_trades_today == 2
    risk = second.engine.risk.state
    assert risk.trades_today == 2
    assert risk.realized_pnl_today == pytest.approx(loss)
    assert risk.consecutive_losses == 1
    assert i1.signal_id in risk.executed_signal_ids and i2.signal_id in risk.executed_signal_ids
    assert second.engine.broker.starting_capital == pytest.approx(100_000 + loss)
    assert risk.day_start_capital == 100_000
    trades, _ = await list_trades(db_session, status="INTERRUPTED")
    assert len(trades) == 1 and trades[0].signal_id == i2.signal_id

    # the restored duplicate guard still holds
    again = await second.execute_intent(i1)
    assert R.DUPLICATE_ORDER in again.rejection_reasons


async def test_restore_counts_only_today(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    svc = service(clock, market)
    await svc._restore()
    await svc.execute_intent(intent())
    await step(svc, market, clock, "RELIANCE", 1020)
    gain = svc.engine.closed[-1].net_pnl

    tomorrow = Clock(clock() + timedelta(days=1))
    state = await restore(db_session, day=tomorrow().date(), now=tomorrow())
    assert state.realized_before_today == pytest.approx(gain)
    assert state.today == [] and state.interrupted == []


async def test_execute_signal_reads_the_stored_signal(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    async with async_session_factory() as session:
        signal = make("RELIANCE", 92.0)
        result = await sync_scan(session, [signal], now=clock(), store_min_score=70)
    signal_id = next(iter(result.ids.values()))
    market.set("RELIANCE", signal.entry)
    svc = service(clock, market)
    await svc._restore()

    d = await svc.execute_signal(signal_id)
    assert d.approved, d.summary
    assert d.intent.signal_id == signal_id
    assert d.intent.entry == signal.entry and d.intent.stop_loss == signal.stop_loss
    with pytest.raises(SignalNotFound):
        await svc.execute_signal(uuid.uuid4())


async def test_expired_signal_is_rejected_as_stale(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    async with async_session_factory() as session:
        signal = make("RELIANCE", 92.0)
        result = await sync_scan(session, [signal], now=clock(), store_min_score=70)
        await sync_scan(session, [], now=clock(), store_min_score=70)  # expires it
    market.set("RELIANCE", signal.entry)
    svc = service(clock, market)
    await svc._restore()
    d = await svc.execute_signal(next(iter(result.ids.values())))
    assert d.rejection_reasons == [R.DATA_STALE]


async def test_auto_trading_takes_the_top_ready_candidate_once(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    async with async_session_factory() as session:
        ready = make("RELIANCE", 92.0)
        result = await sync_scan(session, [ready], now=clock(), store_min_score=70)
    ready_id = next(iter(result.ids.values()))
    market.set("RELIANCE", ready.entry)

    scan = SimpleNamespace(
        top_candidates=[
            SimpleNamespace(status="WATCH", signal_id=uuid.uuid4()),
            SimpleNamespace(status="READY", signal_id=ready_id),
        ]
    )
    counter = {"n": 1}
    signals = SimpleNamespace(
        status=lambda: SimpleNamespace(scan_count=counter["n"], last_error=None),
        get_scan=lambda: scan,
    )
    svc = service(clock, market, signals=signals)
    await svc._restore()

    await svc.tick()
    assert svc.engine.active is None  # automatic trading is OFF by default
    await svc.set_auto_trading(True)
    assert svc.engine.state == TradingState.SCANNING
    await svc.tick()
    assert svc.engine.active is not None and svc.engine.active.signal_id == ready_id
    assert svc.engine.state == TradingState.POSITION_ACTIVE

    await step(svc, market, clock, "RELIANCE", ready.target)
    assert svc.engine.state == TradingState.SCANNING
    counter["n"] += 1  # a new scan with the same candidate
    await svc.tick()
    assert svc.engine.active is None  # not traded twice
    _, total = await list_trades(db_session)
    assert total == 1


async def test_emergency_stop_through_the_service(db_session) -> None:
    clock = Clock()
    market = FakeMarket(clock)
    market.set("RELIANCE", 1000)
    svc = service(clock, market)
    await svc._restore()
    await svc.execute_intent(intent())
    market.set("RELIANCE", 1003)
    await svc.emergency_stop("drill")
    st = await svc.status()
    assert st.emergency_stop and st.active_trade is None and st.positions == []
    assert st.risk.entries_blocked and R.EMERGENCY_STOP in st.risk.blocked_reasons
    trades, _ = await list_trades(db_session)
    assert trades[0].exit_reason == ExitReason.EMERGENCY and trades[0].exit_price == 1003
    critical = await db_session.scalar(
        select(func.count()).select_from(SystemEvent).where(SystemEvent.severity == "CRITICAL")
    )
    assert critical >= 1
    await svc.reset_emergency_stop()
    assert not (await svc.status()).emergency_stop


# --- API --------------------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        svc: TradingService = app.state.trading_service
        # The test run may be outside NSE hours: exercise the simulated session.
        svc.engine.risk.limits = svc.engine.risk.limits.model_copy(
            update={"session": MarketSessionConfig(mode=SessionMode.SIMULATED_24H)}
        )
        yield c


def _token(client: TestClient) -> str:
    email = f"trader-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 201
    return r.json()["access_token"]


def test_read_endpoints(client: TestClient) -> None:
    status = client.get("/api/v1/trading/status")
    assert status.status_code == 200
    body = status.json()
    assert body["mode"] == "PAPER"
    assert body["auto_trading"] is False  # never on by default
    assert body["state"] == "IDLE"
    assert body["balance"]["starting_capital"] == 100_000
    assert "No broker is connected" in body["note"]

    risk = client.get("/api/v1/trading/risk").json()
    assert risk["limits"]["risk_per_trade_percent"] == 1.0
    assert risk["limits"]["max_consecutive_losses"] == 3
    assert risk["snapshot"]["daily_loss_limit_amount"] == 3_000
    assert client.get("/api/v1/trading/positions").json()["positions"] == []
    assert client.get("/api/v1/trading/trades").json()["total"] == 0
    assert client.get("/api/v1/trading/orders").json()["total"] == 0
    assert client.get(f"/api/v1/trading/trades/{uuid.uuid4()}").status_code == 404


def test_actions_require_an_operator(client: TestClient) -> None:
    for path in (
        "/api/v1/trading/emergency-stop",
        "/api/v1/trading/exit",
        "/api/v1/trading/emergency-stop/reset",
        f"/api/v1/trading/signals/{uuid.uuid4()}/execute",
    ):
        assert client.post(path).status_code == 401
    assert client.post("/api/v1/trading/auto", json={"enabled": True}).status_code == 401


def test_execute_signal_end_to_end_with_emergency_stop(client: TestClient) -> None:
    svc: TradingService = app.state.trading_service
    snapshot = app.state.market_data_service.get_snapshot("RELIANCE")
    assert snapshot is not None
    px = snapshot.price

    async def seed() -> uuid.UUID:
        async with async_session_factory() as session:
            await session.execute(delete(Signal))
            await session.commit()
            signal = make(
                "RELIANCE",
                93.0,
                entry=px,
                stop_loss=round(px * 0.99, 2),
                target=round(px * 1.03, 2),
                timestamp=datetime.now(UTC),
            )
            result = await sync_scan(session, [signal], now=datetime.now(UTC), store_min_score=70)
        return next(iter(result.ids.values()))

    assert client.portal is not None
    signal_id = client.portal.call(seed)
    headers = {"Authorization": f"Bearer {_token(client)}"}

    assert (
        client.post(f"/api/v1/trading/signals/{uuid.uuid4()}/execute", headers=headers).status_code
        == 404
    )
    r = client.post(f"/api/v1/trading/signals/{signal_id}/execute", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"]["approved"] is True, body["decision"]["rejection_reasons"]
    assert body["status"]["state"] in ("POSITION_ACTIVE", "ORDER_PENDING")
    assert body["status"]["active_trade"]["signal_id"] == str(signal_id)

    again = client.post(f"/api/v1/trading/signals/{signal_id}/execute", headers=headers).json()
    assert again["decision"]["approved"] is False
    assert "DUPLICATE_ORDER" in again["decision"]["rejection_reasons"]
    assert "ACTIVE_POSITION_EXISTS" in again["decision"]["rejection_reasons"]

    stop = client.post(
        "/api/v1/trading/emergency-stop", headers=headers, json={"reason": "api test"}
    )
    assert stop.status_code == 200
    st = stop.json()
    assert st["emergency_stop"] is True
    assert st["auto_trading"] is False
    assert st["active_trade"] is None or st["state"] == "EXIT"

    assert (
        client.post("/api/v1/trading/auto", headers=headers, json={"enabled": True}).status_code
        == 409
    )

    async def drain() -> None:
        for _ in range(20):
            if svc.engine.active is None:
                return
            await svc.tick()

    client.portal.call(drain)
    trades = client.get("/api/v1/trading/trades").json()
    assert trades["total"] == 1
    assert trades["items"][0]["exit_reason"] == "EMERGENCY"
    trade_id = trades["items"][0]["id"]
    assert client.get(f"/api/v1/trading/trades/{trade_id}").json()["status"] == "CLOSED"
    orders = client.get(f"/api/v1/trading/orders?trade_id={trade_id}").json()
    assert {o["purpose"] for o in orders["items"]} >= {"ENTRY", "EXIT"}

    reset = client.post("/api/v1/trading/emergency-stop/reset", headers=headers)
    assert reset.status_code == 200 and reset.json()["emergency_stop"] is False
    auto = client.post("/api/v1/trading/auto", headers=headers, json={"enabled": True})
    assert auto.status_code == 200 and auto.json()["state"] == "SCANNING"
    off = client.post("/api/v1/trading/auto", headers=headers, json={"enabled": False})
    assert off.json()["state"] == "IDLE"
    assert client.post("/api/v1/trading/exit", headers=headers).status_code == 409

    async def audit_actions() -> list[str]:
        async with async_session_factory() as session:
            return list((await session.execute(select(AuditLog.action))).scalars().all())

    actions = client.portal.call(audit_actions)
    for action in (
        "PAPER_EXECUTE_SIGNAL",
        "PAPER_EMERGENCY_STOP",
        "PAPER_EMERGENCY_STOP_RESET",
        "PAPER_AUTO_TRADING",
    ):
        assert action in actions
