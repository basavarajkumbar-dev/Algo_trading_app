"""Broker endpoints and startup behaviour (needs Postgres for users)."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.main import app

SECRET = "never-show-this-secret-123"
TOKEN = "never-show-this-token-456"
ZERODHA_ENV = (
    "ZERODHA_API_KEY",
    "ZERODHA_API_SECRET",
    "ZERODHA_ACCESS_TOKEN",
    "ZERODHA_API_BASE_URL",
)


@pytest.fixture
def unconfigured() -> Iterator[TestClient]:
    saved = {k: os.environ.pop(k, None) for k in ZERODHA_ENV}
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
    get_settings.cache_clear()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ZERODHA_API_KEY", "somekey")
    monkeypatch.setenv("ZERODHA_API_SECRET", SECRET)
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("ZERODHA_API_BASE_URL", "http://127.0.0.1:9")  # nothing listens: no network
    get_settings.cache_clear()
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", get_settings())
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _user(client: TestClient, role: str = "TRADER") -> dict[str, str]:
    email = f"broker-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 201, r.text
    if role != "TRADER":

        async def promote() -> None:
            engine = create_async_engine(get_settings().database_url)
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE users SET role=:r WHERE email=:e"), {"r": role, "e": email}
                )
            await engine.dispose()

        asyncio.run(promote())
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "correct-horse-1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_without_credentials_the_app_runs_in_paper_mode(unconfigured: TestClient) -> None:
    body = unconfigured.get("/api/v1/broker/status").json()
    assert body["execution_broker"] == "paper" and body["trading_mode"] == "PAPER"
    assert body["live_trading_possible"] is False
    assert body["zerodha"]["configured"] is False
    assert body["zerodha"]["state"] == "NOT_CONFIGURED"
    assert body["zerodha"]["live_orders_armed"] is False
    assert any("LIVE_MODE_ENABLED" in r for r in body["live_blockers"])
    comps = unconfigured.get("/api/v1/system/components").json()
    live = next(c for c in comps["components"] if c["key"] == "live_broker")
    assert live["status"] == "HEALTHY" and "PAPER" in live["detail"]
    # paper trading itself still runs
    assert unconfigured.get("/api/v1/trading/status").json()["mode"] == "PAPER"


def test_broker_actions_need_roles_and_configuration(unconfigured: TestClient) -> None:
    assert unconfigured.post("/api/v1/broker/reconcile").status_code == 401
    trader = _user(unconfigured)
    assert unconfigured.get("/api/v1/broker/zerodha/login-url", headers=trader).status_code == 403
    assert unconfigured.post("/api/v1/broker/reconcile", headers=trader).status_code == 409
    assert unconfigured.get("/api/v1/broker/zerodha/orders", headers=trader).status_code == 409
    assert unconfigured.get("/api/v1/broker/events").json() == {"items": []}


def test_configured_but_unreachable_is_a_warning_and_never_leaks_secrets(
    configured: TestClient,
) -> None:
    body = configured.get("/api/v1/broker/status")
    status = body.json()
    assert status["zerodha"]["configured"] is True
    assert status["zerodha"]["state"] in ("DISCONNECTED", "DEGRADED")
    assert status["execution_broker"] == "paper"
    comps = configured.get("/api/v1/system/components").json()
    live = next(c for c in comps["components"] if c["key"] == "live_broker")
    assert live["status"] == "WARNING" and "paper trading is unaffected" in live["detail"]
    trader = _user(configured)
    for path in ("/api/v1/broker/status", "/api/v1/system/components", "/api/v1/broker/events"):
        text_ = configured.get(path, headers=trader).text
        assert SECRET not in text_ and TOKEN not in text_
    assert configured.get("/api/v1/broker/zerodha/positions", headers=trader).status_code == 503


def test_admin_gets_the_login_url_and_the_secret_stays_out(configured: TestClient) -> None:
    admin = _user(configured, "ADMIN")
    r = configured.get("/api/v1/broker/zerodha/login-url", headers=admin)
    assert r.status_code == 200
    assert r.json()["login_url"] == "https://kite.zerodha.com/connect/login?v=3&api_key=somekey"
    assert SECRET not in r.text
    # a bad request token is refused by Kite (unreachable here): reported, not crashed
    bad = configured.post(
        "/api/v1/broker/zerodha/session", json={"request_token": "abcdef"}, headers=admin
    )
    assert bad.status_code == 503 and SECRET not in bad.text
    status = configured.post("/api/v1/broker/disconnect", headers=admin).json()
    assert (
        status["zerodha"]["paused_by_user"] is True and status["zerodha"]["state"] == "DISCONNECTED"
    )
