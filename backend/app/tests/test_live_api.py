"""Live safety layer API and storage (needs Postgres): arming needs an ADMIN,
the typed confirmation and the password; every action is audited; a live
trade left open by a previous run blocks new live entries until an admin
acknowledges it. Kite is unreachable here: nothing can be sent."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.database.models.audit_log import AuditLog
from app.database.models.live_trade import LiveTradeRecord
from app.database.session import async_session_factory
from app.live.order_manager import LiveTradeStatus
from app.main import app
from app.tests.test_broker_api import _user
from app.tests.test_live_safety import ADMIN, Rig, armed, make
from app.tests.test_zerodha_api import KITE_ENV

PASSWORD = "correct-horse-1"  # what _user registers


def _client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Iterator[TestClient]:
    for k in (*KITE_ENV, "EXECUTION_MODE", "LIVE_TRADING_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", get_settings())
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def paper(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, {})


@pytest.fixture
def live_configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(
        monkeypatch,
        {
            "KITE_API_KEY": "testkey123",
            "KITE_API_SECRET": "s3cr3t-api-secret-value",
            "ZERODHA_API_BASE_URL": "http://127.0.0.1:9",
            "KITE_WS_URL": "ws://127.0.0.1:9",
            "EXECUTION_MODE": "ZERODHA",
            "LIVE_TRADING_ENABLED": "true",
        },
    )


def test_paper_default_cannot_be_armed(paper: TestClient) -> None:
    st = paper.get("/api/v1/live/status").json()
    assert st["configured"] is False and st["execution_mode"] == "PAPER"
    assert st["arming"]["armed"] is False and st["max_active_positions"] == 1
    admin = _user(paper, "ADMIN")
    r = paper.post(
        "/api/v1/live/arm",
        json={"confirmation": "ARM LIVE TRADING", "password": PASSWORD},
        headers=admin,
    )
    assert r.status_code == 409 and "not configured" in r.json()["detail"]


def test_arming_needs_admin_confirmation_and_password(live_configured: TestClient) -> None:
    c = live_configured
    st = c.get("/api/v1/live/status").json()
    assert st["configured"] is True and st["execution_mode"] == "ZERODHA"
    assert any("Kite session" in b for b in st["arm_blockers"])
    body = {"confirmation": "ARM LIVE TRADING", "password": PASSWORD}
    assert c.post("/api/v1/live/arm", json=body).status_code == 401
    trader = _user(c)
    assert c.post("/api/v1/live/arm", json=body, headers=trader).status_code == 403
    admin = _user(c, "ADMIN")
    wrong = c.post("/api/v1/live/arm", json={**body, "confirmation": "yes"}, headers=admin)
    assert wrong.status_code == 422
    bad_pw = c.post("/api/v1/live/arm", json={**body, "password": "nope"}, headers=admin)
    assert bad_pw.status_code == 403
    blocked = c.post("/api/v1/live/arm", json=body, headers=admin)
    assert blocked.status_code == 409 and "Kite session" in blocked.json()["detail"]
    assert c.get("/api/v1/live/status").json()["arming"]["armed"] is False
    audit = c.get("/api/v1/live/audit", headers=trader).json()["items"]
    refused = [a for a in audit if a["action"] == "LIVE_ARM_REFUSED"]
    assert len(refused) >= 2 and any("wrong password" in str(a["details"]) for a in refused)


def test_kill_switch_engage_and_reset_are_audited(live_configured: TestClient) -> None:
    c = live_configured
    trader = _user(c)
    admin = _user(c, "ADMIN")
    r = c.post("/api/v1/live/kill-switch", json={"reason": "drill"}, headers=trader)
    assert r.status_code == 200 and r.json()["status"]["kill_switch"]["engaged"] is True
    assert r.json()["result"]["exit_requested"] is False  # positions are not closed by default
    assert c.post("/api/v1/live/kill-switch/reset", headers=trader).status_code == 403
    st = c.post("/api/v1/live/kill-switch/reset", headers=admin).json()
    assert st["kill_switch"]["engaged"] is False and st["arming"]["armed"] is False
    actions = [a["action"] for a in c.get("/api/v1/live/audit", headers=admin).json()["items"]]
    assert "LIVE_KILL_SWITCH" in actions and "LIVE_KILL_SWITCH_RESET" in actions
    assert c.post("/api/v1/live/exit", headers=trader).status_code == 409  # no live trade


async def _clean() -> None:
    async with async_session_factory() as s:
        await s.execute(delete(LiveTradeRecord))
        await s.commit()


async def _with_storage(rig: Rig) -> None:
    rig.svc._session_factory = async_session_factory


async def test_live_actions_are_audited_and_trades_stored() -> None:
    await _clean()
    rig = await make()
    await _with_storage(rig)
    await rig.svc.arm(user_id=ADMIN, email="admin@example.com")
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=None)
    assert trade is not None
    async with async_session_factory() as s:
        row = (
            await s.execute(
                select(LiveTradeRecord).where(
                    LiveTradeRecord.correlation_id == trade.correlation_id
                )
            )
        ).scalar_one()
        assert row.status == "PENDING_ENTRY" and row.quantity == trade.quantity
        actions = (
            (
                await s.execute(
                    select(AuditLog.action)
                    .where(AuditLog.action.like("LIVE_%"))
                    .order_by(AuditLog.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
    assert {"LIVE_ORDER_PLACED", "LIVE_PRETRADE_CHECK"} <= set(actions)
    await _clean()


async def test_a_trade_left_open_by_a_restart_blocks_live_until_acknowledged() -> None:
    await _clean()
    rig = await armed()
    await _with_storage(rig)
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=None)
    assert trade is not None and trade.status == LiveTradeStatus.PENDING_ENTRY
    # "restart": a new service finds the unfinished trade
    fresh = await make()
    fresh.svc._session_factory = async_session_factory
    await fresh.svc._load_unresolved()
    assert fresh.svc.unresolved and "INFY" in fresh.svc.unresolved
    assert any("previous run" in b for b in fresh.svc.arm_blockers())
    await fresh.svc.acknowledge_unresolved(user_id=ADMIN, note="closed in Kite")
    assert fresh.svc.unresolved is None
    async with async_session_factory() as s:
        row = (
            await s.execute(
                select(LiveTradeRecord).where(
                    LiveTradeRecord.correlation_id == trade.correlation_id
                )
            )
        ).scalar_one()
    assert row.status == "CANCELLED" and row.exit_reason == "ACKNOWLEDGED_AFTER_RESTART"
    await _clean()
