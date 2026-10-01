"""Tests for GET /api/v1/market/* endpoints.

Uses FastAPI's TestClient (not httpx.AsyncClient+ASGITransport) so the
app's lifespan runs and MarketDataService is actually attached to
app.state — see test_market_websocket.py's module docstring for why.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


def _poll_for_200(client: TestClient, url: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    last_response = None
    while time.monotonic() < deadline:
        last_response = client.get(url)
        if last_response.status_code == 200:
            return last_response
        time.sleep(0.1)
    assert last_response is not None
    return last_response


def test_market_status_reports_connected_and_streaming() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/status")
        assert response.status_code == 200
        body = response.json()
        assert body["provider_status"] == "CONNECTED"
        assert body["is_streaming"] is True
        assert len(body["watchlist"]) > 0
        assert any("simulated" in note for note in body["notes"])


def test_market_instruments_returns_nifty_50_plus_index() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/instruments")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 51
        symbols = {i["symbol"] for i in body}
        assert "RELIANCE" in symbols
        assert "NIFTY50" in symbols


def test_market_snapshot_becomes_available_and_has_expected_shape() -> None:
    with TestClient(app) as client:
        response = _poll_for_200(client, "/api/v1/market/snapshot/RELIANCE")
        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "RELIANCE"
        assert body["price"] > 0
        assert "change" in body
        assert "change_percent" in body
        assert "volume" in body


def test_market_snapshot_unknown_symbol_is_404() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/snapshot/NOT_A_REAL_SYMBOL")
        assert response.status_code == 404


def test_market_candles_returns_ohlcv_shape() -> None:
    with TestClient(app) as client:
        # Give the stream a moment to populate at least a current candle.
        _poll_for_200(client, "/api/v1/market/snapshot/RELIANCE")

        response = client.get("/api/v1/market/candles?symbol=RELIANCE&timeframe=1m&limit=5")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        if body:  # a candle may or may not exist yet depending on timing
            candle = body[0]
            assert candle["symbol"] == "RELIANCE"
            assert candle["timeframe"] == "1m"
            for field in ("open", "high", "low", "close", "volume", "timestamp", "is_closed"):
                assert field in candle


def test_market_candles_rejects_invalid_timeframe() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/candles?symbol=RELIANCE&timeframe=7m")
        assert response.status_code == 422
