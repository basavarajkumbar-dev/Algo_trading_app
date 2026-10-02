"""Backtest API, background runs and storage (needs Postgres)."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    os.environ["BACKTEST_CACHE_DIR"] = str(tmp_path_factory.getbasetemp() / "btcache-api")
    with TestClient(app) as c:
        app.state.backtest_service.workers = 1
        yield c


def _token(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"bt-{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-1"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


BODY: dict[str, Any] = {
    "name": "api test",
    "mode": "OUT_OF_SAMPLE",
    "strategy": "TREND_CONTINUATION",
    "symbols": ["RELIANCE"],
    "start_date": "2026-07-27",
    "end_date": "2026-08-04",
    "timeframe": "5m",
    "parameters": {"min_score": 60},
    "grid": {"min_score": [55, 65]},
    "warmup_days": 10,
}


def _wait(client: TestClient, run_id: str, timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/backtests/{run_id}").json()
        if body["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return body
        time.sleep(0.5)
    raise AssertionError("backtest did not finish")


def test_options_describe_everything_the_form_needs(client: TestClient) -> None:
    body = client.get("/api/v1/backtests/options").json()
    assert [s["value"] for s in body["strategies"]] == [
        "ALL",
        "TREND_CONTINUATION",
        "BREAKOUT_MOMENTUM",
        "MEAN_REVERSION",
    ]
    tc = next(s for s in body["strategies"] if s["value"] == "TREND_CONTINUATION")
    keys = {p["key"] for p in tc["parameters"]}
    assert {"min_pullback_percent", "min_score", "trailing_mode"} <= keys
    assert all("minimum" in p for p in tc["parameters"])
    assert any(v["current_default"] for v in body["versions"])
    syn = body["data_sources"][0]
    assert syn["kind"] == "synthetic" and syn["synthetic"] is True and syn["symbols"]
    assert body["modes"] == ["HISTORICAL", "OUT_OF_SAMPLE", "WALK_FORWARD"]
    assert body["timeframes"] == ["5m", "15m"]
    assert body["limits"]["max_grid_combinations"] == 27
    assert "do not predict" in body["note"]


def test_bad_requests_are_rejected_before_queueing(client: TestClient) -> None:
    h = _token(client)
    assert client.post("/api/v1/backtests", json=BODY).status_code == 401
    cases: list[tuple[dict[str, Any], str]] = [
        ({"symbols": ["NOPE"]}, "No data for"),
        ({"mode": "WALK_FORWARD", "grid": None}, "Walk-forward needs"),
        ({"parameters": {"bogus": 1}}, "Unknown parameter"),
        ({"parameters": {"min_score": 10}}, ">="),
        (
            {
                "grid": {
                    "min_score": [50, 55, 60, 65, 70, 75],
                    "trailing_activation_r": [0.5, 1, 1.5, 2, 2.5],
                }
            },
            "limit",
        ),
        ({"version": "9.9.9+deadbeef"}, "is not what these parameters produce"),
        ({"start_date": "2019-01-01", "end_date": "2019-01-10"}, "Data covers"),
    ]
    for patch, text in cases:
        r = client.post("/api/v1/backtests", json={**BODY, **patch}, headers=h)
        assert r.status_code == 422, (patch, r.text)
        assert text in r.text, (patch, r.text)
    assert client.get("/api/v1/backtests").json()["total"] == 0


def test_run_stores_results_trades_equity_and_versions(client: TestClient) -> None:
    h = _token(client)
    r = client.post("/api/v1/backtests", json=BODY, headers=h)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "QUEUED" and run["estimated_seconds"] > 0
    done = _wait(client, run["id"])
    assert done["status"] == "COMPLETED", done["error"]
    assert done["progress"] == 1.0
    result = done["result"]
    assert result["mode"] == "OUT_OF_SAMPLE"
    assert [s["kind"] for s in result["segments"]] == ["IN_SAMPLE", "OUT_OF_SAMPLE"]
    assert result["combinations_tested"] == 2
    if result["selected_params"]["min_score"] == 60:  # nothing qualified: yours, and said so
        assert any("your own parameters were used" in f for f in done["robustness"]["flags"])
    else:
        assert result["selected_params"]["min_score"] in (55, 65)
    assert result["strategy_versions"][0]["strategy"] == "TREND_CONTINUATION"
    assert done["metrics"]["sharpe_note"]
    assert "flags" in done["robustness"]
    assert done["data_source"]["synthetic"] is True

    trades = client.get(f"/api/v1/backtests/{run['id']}/trades").json()
    assert trades["total"] == len([t for t in trades["items"]])
    for t in trades["items"]:
        assert t["segment"] in ("IN_SAMPLE", "OUT_OF_SAMPLE")
        assert t["exit_reason"]
    equity = client.get(f"/api/v1/backtests/{run['id']}/equity").json()
    assert len(equity["days"]) >= 1

    listing = client.get("/api/v1/backtests").json()
    assert listing["items"][0]["id"] == run["id"]
    assert listing["items"][0]["headline"] is not None

    # the strategy version and parameter set are now stored and offered back
    versions = client.get("/api/v1/backtests/options").json()["versions"]
    assert any(v["strategy_choice"] == "TREND_CONTINUATION" for v in versions)

    assert client.post(f"/api/v1/backtests/{run['id']}/cancel", headers=h).status_code == 409
    assert client.delete(f"/api/v1/backtests/{run['id']}", headers=h).status_code == 204
    assert client.get(f"/api/v1/backtests/{run['id']}").status_code == 404


def test_cancel_a_queued_run(client: TestClient) -> None:
    h = _token(client)
    first = client.post(
        "/api/v1/backtests", json={**BODY, "mode": "HISTORICAL", "grid": None}, headers=h
    ).json()
    second = client.post(
        "/api/v1/backtests",
        json={**BODY, "mode": "HISTORICAL", "grid": None, "symbols": ["TCS"]},
        headers=h,
    ).json()
    c = client.post(f"/api/v1/backtests/{second['id']}/cancel", headers=h)
    assert c.status_code == 200
    assert _wait(client, second["id"])["status"] == "CANCELLED"
    _wait(client, first["id"])
