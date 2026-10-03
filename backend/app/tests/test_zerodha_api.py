"""Phase 13 endpoints: the Kite login flow (login URL with state, callback
redirect, logout), the safe status, /system/market-data and
/system/broker-health, provider selection from settings, and that no
secret, access token or request token ever appears in a response.
Kite is the fake server; nothing reaches the network. Needs Postgres."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, REQUEST_TOKEN, FakeKite
from app.tests.test_broker_api import _user

KITE_ENV = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "ZERODHA_API_KEY",
    "ZERODHA_API_SECRET",
    "ZERODHA_ACCESS_TOKEN",
    "ZERODHA_API_BASE_URL",
    "MARKET_DATA_MODE",
)
SECRETS = (API_SECRET, ACCESS_TOKEN, REQUEST_TOKEN)


def _client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Iterator[TestClient]:
    for k in KITE_ENV:
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
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, {})


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(
        monkeypatch,
        {
            "KITE_API_KEY": API_KEY,
            "KITE_API_SECRET": API_SECRET,
            "ZERODHA_API_BASE_URL": "http://127.0.0.1:9",  # nothing listens
            "KITE_WS_URL": "ws://127.0.0.1:9",
            "FRONTEND_URL": "http://frontend.test",
        },
    )


@pytest.fixture
def mock_data_with_kite(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(
        monkeypatch,
        {
            "KITE_API_KEY": API_KEY,
            "KITE_API_SECRET": API_SECRET,
            "MARKET_DATA_MODE": "MOCK",
            "FRONTEND_URL": "http://frontend.test",
        },
    )


def _no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


def test_without_credentials_data_is_simulated_and_labelled(unconfigured: TestClient) -> None:
    md = unconfigured.get("/api/v1/system/market-data").json()
    assert md["provider"] == "MOCK" and md["mode"] == "SIMULATED" and md["simulated"] is True
    assert set(md) >= {
        "provider",
        "mode",
        "websocket",
        "rest_api",
        "last_tick",
        "latency_ms",
        "subscribed_instruments",
        "stale",
    }
    status = unconfigured.get("/api/v1/broker/zerodha/status").json()
    assert status == {**status, "configured": False, "connected": False, "market_data": "SIMULATED"}
    assert status["execution_mode"] == "PAPER"
    health = unconfigured.get("/api/v1/system/broker-health").json()
    assert health["status"] == "DISCONNECTED" and not health["configured"]
    market = unconfigured.get("/api/v1/market/status").json()
    assert market["data_source"] == "SIMULATED"


def test_credentials_select_zerodha_data_but_execution_stays_paper(configured: TestClient) -> None:
    assert os.environ.get("LIVE_TRADING_ENABLED") in (None, "false")
    md = configured.get("/api/v1/system/market-data").json()
    assert md["provider"] == "ZERODHA" and md["mode"] == "ZERODHA_LIVE" and not md["simulated"]
    assert md["status"] == "DISCONNECTED" and md["stale"] is True  # no login yet
    status = configured.get("/api/v1/broker/zerodha/status").json()
    assert status["configured"] and not status["connected"]
    assert status["auth"] == "LOGIN_REQUIRED" and status["execution_mode"] == "PAPER"
    assert status["market_data"] == "DISCONNECTED"
    health = configured.get("/api/v1/system/broker-health").json()
    assert health["status"] == "AUTH_REQUIRED" and health["execution_mode"] == "PAPER"
    names = {c["name"]: c for c in health["checks"]}
    assert {
        "database",
        "redis",
        "auth",
        "rest_api",
        "instrument_master",
        "websocket",
        "data_freshness",
    } <= set(names)
    assert names["auth"]["ok"] is False and names["websocket"]["status"] == "DISCONNECTED"
    assert configured.get("/api/v1/market/status").json()["data_source"] == "ZERODHA_LIVE"
    assert configured.get("/api/v1/market/snapshot/RELIANCE").status_code in (404, 503)
    comps = configured.get("/api/v1/system/components").json()
    md_comp = next(c for c in comps["components"] if c["key"] == "market_data")
    assert md_comp["metrics"]["simulated"] is False and md_comp["status"] == "CRITICAL"


def test_login_endpoints_need_an_admin(configured: TestClient) -> None:
    assert configured.get("/api/v1/broker/zerodha/login").status_code == 401
    trader = _user(configured)
    assert configured.get("/api/v1/broker/zerodha/login", headers=trader).status_code == 403
    assert configured.post("/api/v1/broker/zerodha/logout", headers=trader).status_code == 403
    admin = _user(configured, "ADMIN")
    url = configured.get("/api/v1/broker/zerodha/login", headers=admin).json()["login_url"]
    q = parse_qs(urlparse(url).query)
    assert q["api_key"] == [API_KEY] and "state" in parse_qs(q["redirect_params"][0])
    _no_secrets(url)
    r = configured.get(
        "/api/v1/broker/zerodha/login?redirect=true", headers=admin, follow_redirects=False
    )
    assert r.status_code == 307 and r.headers["location"].startswith("https://kite.zerodha.com/")


def test_callback_rejects_a_forged_state_and_never_echoes_tokens(configured: TestClient) -> None:
    r = configured.get(
        f"/api/v1/broker/zerodha/callback?request_token={REQUEST_TOKEN}&status=success&state=forged",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "http://frontend.test/settings?zerodha=error&reason=state"
    _no_secrets(r.headers["location"], r.text)


def test_full_login_callback_and_logout(mock_data_with_kite: TestClient) -> None:
    c = mock_data_with_kite
    fake = FakeKite(token=None)
    fake.login_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    runtime = app.state.zerodha
    assert runtime.client is not None
    runtime.client._http = httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=fake.transport(),
        headers={"X-Kite-Version": "3"},
    )
    admin = _user(c, "ADMIN")
    url = c.get("/api/v1/broker/zerodha/login", headers=admin).json()["login_url"]
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    r = c.get(
        "/api/v1/broker/zerodha/callback",
        params={
            "request_token": REQUEST_TOKEN,
            "status": "success",
            "state": state,
            "action": "login",
            "type": "login",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "http://frontend.test/settings?zerodha=connected"
    status = c.get("/api/v1/broker/zerodha/status").json()
    assert status["connected"] and status["auth"] == "AUTHENTICATED"
    assert status["user_id"] and status["user_id"] != "AB1234"  # masked
    assert status["market_data"] == "SIMULATED"  # MARKET_DATA_MODE=MOCK: still labelled
    replay = c.get(
        "/api/v1/broker/zerodha/callback",
        params={"request_token": REQUEST_TOKEN, "status": "success", "state": state},
        follow_redirects=False,
    )
    assert "zerodha=error" in replay.headers["location"]  # the state was single-use
    texts = [
        c.get(p, headers=admin).text
        for p in (
            "/api/v1/broker/zerodha/status",
            "/api/v1/broker/status",
            "/api/v1/system/broker-health",
            "/api/v1/system/market-data",
            "/api/v1/system/components",
            "/api/v1/broker/zerodha/profile",
        )
    ]
    _no_secrets(*texts, r.headers["location"])
    out = c.post("/api/v1/broker/zerodha/logout", headers=admin).json()
    assert out["auth"] == "LOGGED_OUT" and not out["connected"]
    assert fake.posts() == []  # no order was ever sent


def test_zerodha_backtest_source_lists_symbols_and_dates(configured: TestClient) -> None:
    """Regression: the Zerodha source offered no symbols to pick."""

    trader = _user(configured)
    opts = configured.get("/api/v1/backtests/options", headers=trader).json()
    z = next(s for s in opts["data_sources"] if s["kind"] == "zerodha")
    assert len(z["symbols"]) == 50 and "RELIANCE" in z["symbols"]
    assert "NIFTY50" not in z["symbols"]  # the benchmark is added automatically
    assert z["first_day"] and z["last_day"] < datetime.now().date().isoformat()
    assert z["available"] is False  # no Kite login in this test
