"""API access control (API_REQUIRE_AUTH) and the universe endpoints (needs
Postgres for the signed-in cases). Data endpoints stay open in development
by default; with API_REQUIRE_AUTH=true they need a valid access token,
while /health and the Kite redirect stay reachable."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.tests.test_broker_api import _user

DATA_ENDPOINTS = (
    "/api/v1/market/status",
    "/api/v1/signals",
    "/api/v1/trading/status",
    "/api/v1/trading/trades",
    "/api/v1/analytics/performance",
    "/api/v1/backtests",
    "/api/v1/universes",
    "/api/v1/system/components",
)


def _client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Iterator[TestClient]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", get_settings())
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def locked(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, {"API_REQUIRE_AUTH": "true"})


@pytest.fixture
def open_dev(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("API_REQUIRE_AUTH", raising=False)
    yield from _client(monkeypatch, {"APP_ENV": "development"})


def test_data_endpoints_need_a_token_when_locked(locked: TestClient) -> None:
    for path in (*DATA_ENDPOINTS, "/api/v1/scanner"):
        assert locked.get(path).status_code == 401, path
    assert locked.get("/api/v1/system/health").status_code in (200, 503)
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert locked.get("/api/v1/market/status", headers=bad).status_code == 401


def test_a_signed_in_user_can_read_when_locked(locked: TestClient) -> None:
    headers = _user(locked)
    for path in DATA_ENDPOINTS:
        assert locked.get(path, headers=headers).status_code == 200, path
    # no scan may have finished yet: 503, never 401
    assert locked.get("/api/v1/scanner", headers=headers).status_code in (200, 503)


def test_websocket_needs_an_auth_message_when_locked(locked: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with locked.websocket_connect("/api/v1/ws/market") as ws:
            ws.send_json({"type": "auth", "token": "not-a-real-token"})
            ws.receive_json()
    headers = _user(locked)
    token = headers["Authorization"].split(" ", 1)[1]
    with locked.websocket_connect("/api/v1/ws/market") as ws:
        ws.send_json({"type": "auth", "token": token})  # in the message, not the URL
        assert ws.receive_json()["type"] == "connected"


def test_development_default_is_open(open_dev: TestClient) -> None:
    assert open_dev.get("/api/v1/market/status").status_code == 200


def test_universe_endpoints_report_exact_counts(open_dev: TestClient) -> None:
    body = open_dev.get("/api/v1/universes").json()
    ids = {u["id"] for u in body["universes"]}
    assert {"NIFTY50", "NIFTY500", "NIFTY_BANK", "NSE_ALL_EQ"} <= ids
    active = open_dev.get("/api/v1/universes/active").json()
    assert active["requested_universes"] == ["NIFTY50"]
    assert active["processed_count"] == 50 and active["excluded_count"] == 0
    assert active["processed_count"] + active["excluded_count"] == active["requested_count"]
    unknown = open_dev.post("/api/v1/universes/resolve", json={"universes": ["NIFTY150"]})
    assert unknown.status_code == 422


def test_production_refuses_placeholder_secrets() -> None:
    from app.core.config import Settings

    dev = Settings(APP_ENV="development")
    assert dev.deployment_problems() == []
    prod = Settings(APP_ENV="production")
    problems = prod.deployment_problems()
    assert any("APP_SECRET" in p for p in problems)
    assert any("JWT_SECRET" in p for p in problems)
    ok = Settings(APP_ENV="production", APP_SECRET="a" * 40, JWT_SECRET="b" * 40)
    assert ok.deployment_problems() == []
    no_auth = Settings(
        APP_ENV="production", APP_SECRET="a" * 40, JWT_SECRET="b" * 40, API_REQUIRE_AUTH=False
    )
    assert no_auth.deployment_problems() == [
        "API_REQUIRE_AUTH=false is not allowed outside development"
    ]


def test_sign_up_can_be_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"email": "nobody-signup@example.com", "password": "long-enough-1", "full_name": None}
    for client in _client(monkeypatch, {"ALLOW_REGISTRATION": "false"}):
        r = client.post("/api/v1/auth/register", json=body)
        assert r.status_code == 403
        assert "closed" in r.json()["detail"]
    from app.core.config import Settings

    assert (
        Settings(APP_ENV="production", ALLOW_REGISTRATION=None).effective_allow_registration
        is False
    )
    assert (
        Settings(APP_ENV="development", ALLOW_REGISTRATION=None).effective_allow_registration
        is True
    )


def test_create_user_script_sets_the_role() -> None:
    import subprocess
    import sys
    import uuid

    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"

    def run(*extra: str, password: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "app.scripts.create_user",
                "--email",
                email,
                *extra,
                "--password-stdin",
            ],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=120,
        )

    created = run("--role", "TRADER", password="a-long-password-1")
    assert created.returncode == 0, created.stderr
    assert f"created {email} with role TRADER" in created.stdout
    assert "a-long-password-1" not in created.stdout + created.stderr
    promoted = run("--role", "ADMIN")  # existing user: password not needed
    assert promoted.returncode == 0, promoted.stderr
    assert f"updated {email} with role ADMIN" in promoted.stdout
