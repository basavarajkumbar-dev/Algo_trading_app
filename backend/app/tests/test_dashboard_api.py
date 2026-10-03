"""Phase 9 dashboard endpoints against the running app (needs Postgres)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.database.session import async_session_factory
from app.main import app
from app.risk.config import MarketSessionConfig, SessionMode
from app.risk.schemas import TradeSide
from app.tests.paper_helpers import T0, intent
from app.trading.repository import save_changes
from app.trading.schemas import EngineEvent, ExitReason, PaperTrade, TradeStatus
from app.trading.service import TradingService

COMPONENT_KEYS = [
    "market_data",
    "backend",
    "database",
    "redis",
    "websocket",
    "strategy_engine",
    "risk_engine",
    "paper_broker",
    "live_broker",
]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_components_cover_every_subsystem(client: TestClient) -> None:
    body = client.get("/api/v1/system/components").json()
    assert [c["key"] for c in body["components"]] == COMPONENT_KEYS
    for c in body["components"]:
        assert c["status"] in ("HEALTHY", "WARNING", "CRITICAL")
        assert c["detail"]
    by_key = {c["key"]: c for c in body["components"]}
    assert by_key["backend"]["status"] == "HEALTHY"
    assert by_key["database"]["status"] == "HEALTHY"
    assert by_key["market_data"]["metrics"]["simulated"] is True
    assert by_key["paper_broker"]["status"] == "HEALTHY"
    assert by_key["live_broker"]["metrics"]["execution_broker"] == "paper"
    assert body["status"] in ("HEALTHY", "WARNING", "CRITICAL")
    assert body["trading_mode"] == "PAPER"


def test_emergency_stop_shows_as_a_risk_engine_warning(client: TestClient) -> None:
    svc: TradingService = app.state.trading_service
    assert client.portal is not None
    client.portal.call(svc.emergency_stop, "health test")
    by_key = {c["key"]: c for c in client.get("/api/v1/system/components").json()["components"]}
    assert by_key["risk_engine"]["status"] == "WARNING"
    assert "Emergency stop" in by_key["risk_engine"]["detail"]
    client.portal.call(svc.reset_emergency_stop)


def test_trading_status_reports_the_session(client: TestClient) -> None:
    body = client.get("/api/v1/trading/status").json()
    assert isinstance(body["market_open"], bool) and isinstance(body["entries_allowed"], bool)
    assert body["session_mode"] in ("EXCHANGE", "SIMULATED_24H")
    assert body["market_detail"]


def test_events_are_listed_newest_first_and_filterable(client: TestClient) -> None:
    async def seed() -> None:
        async with async_session_factory() as session:
            await save_changes(
                session,
                orders=[],
                trades=[],
                events=[
                    EngineEvent(event_type="FIRST", message="first", at=T0),
                    EngineEvent(event_type="SECOND", severity="CRITICAL", message="second", at=T0),
                ],
                trade_of=lambda _: None,
                purpose_of=lambda _: None,
            )

    assert client.portal is not None
    client.portal.call(seed)
    body = client.get("/api/v1/system/events?limit=10").json()
    types = [e["event_type"] for e in body["items"]]
    assert "PAPER_FIRST" in types and "PAPER_SECOND" in types
    critical = client.get("/api/v1/system/events?severity=CRITICAL").json()
    assert all(e["severity"] == "CRITICAL" for e in critical["items"])
    assert client.get("/api/v1/system/events?severity=BAD").status_code == 422


def test_performance_endpoint_reads_stored_trades(client: TestClient) -> None:
    def closed(net: float, minutes: int) -> PaperTrade:
        from datetime import timedelta

        return PaperTrade(
            symbol="INFY",
            side=TradeSide.LONG,
            strategy="TREND_CONTINUATION",
            status=TradeStatus.CLOSED,
            planned_entry=100,
            stop_loss=99,
            target=102,
            quantity=10,
            filled_quantity=10,
            initial_risk=10,
            net_pnl=net,
            r_multiple=net / 10,
            exit_reason=ExitReason.TARGET if net > 0 else ExitReason.STOP_LOSS,
            created_at=T0,
            closed_at=T0 + timedelta(minutes=minutes),
        )

    async def seed() -> None:
        async with async_session_factory() as session:
            await save_changes(
                session,
                orders=[],
                trades=[closed(120, 1), closed(-40, 2), closed(60, 3)],
                events=[],
                trade_of=lambda _: None,
                purpose_of=lambda _: None,
            )

    assert client.portal is not None
    client.portal.call(seed)
    body = client.get("/api/v1/analytics/performance").json()
    s = body["summary"]
    assert s["closed_trades"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["profit_factor"] == 4.5 and s["net_pnl"] == 140
    assert [p["equity"] for p in body["equity_curve"]] == [100_120, 100_080, 100_140]
    assert body["equity_curve"][1]["drawdown"] == -40
    assert body["by_strategy"][0]["key"] == "TREND_CONTINUATION"


def test_trading_events_are_pushed_over_the_websocket(client: TestClient) -> None:
    svc: TradingService = app.state.trading_service
    svc.engine.risk.limits = svc.engine.risk.limits.model_copy(
        update={"session": MarketSessionConfig(mode=SessionMode.SIMULATED_24H)}
    )
    snapshot = app.state.market_data_service.get_snapshot("RELIANCE")
    assert snapshot is not None
    px = snapshot.price
    with client.websocket_connect("/api/v1/ws/market") as ws:
        assert ws.receive_json()["type"] == "connected"
        assert client.portal is not None
        # a rejected intent (score below threshold) still produces an event
        client.portal.call(
            svc.execute_intent,
            intent(
                entry=px,
                stop=round(px * 0.99, 2),
                target=round(px * 1.02, 2),
                score=10,
                signal_id=uuid.uuid4(),
            ),
        )
        for _ in range(2000):
            message = ws.receive_json()
            if message["type"] == "trading_event":
                break
        else:  # pragma: no cover
            pytest.fail("no trading_event received")
    assert message["event_type"] == "RISK_REJECTED"
    assert message["severity"] == "WARNING"
    assert "SIGNAL_NOT_EXECUTABLE" in message["metadata"]["reasons"]
    assert TradeSide.LONG.value in message["message"]
