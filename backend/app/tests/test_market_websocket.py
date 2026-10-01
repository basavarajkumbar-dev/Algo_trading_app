"""WebSocket foundation tests.

Two layers, deliberately separated so timing flakiness in one doesn't
mask a bug in the other:

1. `ConnectionManager.broadcast` unit-tested against fake WebSocket
   objects — fully deterministic, no real network/ASGI involved.
2. The real `/api/v1/ws/market` endpoint, exercised through FastAPI's
   `TestClient` (which correctly drives the app's lifespan — unlike the
   httpx.AsyncClient+ASGITransport pattern used elsewhere in this test
   suite, which does NOT trigger lifespan). This starts the real
   MarketDataService background stream for the duration of the `with`
   block and stops it cleanly afterward.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.main import app
from app.websocket.manager import ConnectionManager


class _FakeWebSocket:
    """Minimal stand-in exposing only what ConnectionManager needs."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict[str, Any]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("simulated send failure")
        self.sent.append(message)


async def test_connection_manager_broadcasts_to_all_connected_clients() -> None:
    manager = ConnectionManager()
    ws_a = _FakeWebSocket()
    ws_b = _FakeWebSocket()

    await manager.connect(ws_a)  # type: ignore[arg-type]
    await manager.connect(ws_b)  # type: ignore[arg-type]
    assert manager.connection_count == 2

    await manager.broadcast({"type": "market_tick", "symbol": "RELIANCE", "price": 1200.0})

    assert ws_a.sent == [{"type": "market_tick", "symbol": "RELIANCE", "price": 1200.0}]
    assert ws_b.sent == ws_a.sent


async def test_connection_manager_drops_clients_that_fail_to_receive() -> None:
    """A broadcast to a dead/misbehaving client must not prevent
    delivery to the others, and the dead client should be removed."""

    manager = ConnectionManager()
    good = _FakeWebSocket()
    bad = _FakeWebSocket(fail=True)

    await manager.connect(good)  # type: ignore[arg-type]
    await manager.connect(bad)  # type: ignore[arg-type]

    await manager.broadcast({"type": "market_tick", "symbol": "TCS", "price": 2000.0})

    assert good.sent == [{"type": "market_tick", "symbol": "TCS", "price": 2000.0}]
    assert manager.connection_count == 1  # the failing client was dropped


async def test_disconnect_removes_client() -> None:
    manager = ConnectionManager()
    ws = _FakeWebSocket()
    await manager.connect(ws)  # type: ignore[arg-type]
    assert manager.connection_count == 1

    manager.disconnect(ws)  # type: ignore[arg-type]
    assert manager.connection_count == 0


def test_websocket_endpoint_sends_connected_ack_and_snapshots() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market") as websocket:
            first_message = websocket.receive_json()
            assert first_message["type"] == "connected"
            assert "watchlist" in first_message
            assert len(first_message["watchlist"]) > 0


def test_websocket_endpoint_eventually_streams_a_market_tick() -> None:
    """After the connection ack (and possibly some snapshot messages),
    the live streaming loop should eventually broadcast at least one
    real market_tick to this client."""

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market") as websocket:
            seen_types = []
            found_tick = False
            for _ in range(30):  # bounded: ack + up to ~11 snapshots + several tick cycles
                message = websocket.receive_json()
                seen_types.append(message["type"])
                if message["type"] == "market_tick":
                    assert message["price"] > 0
                    assert "symbol" in message
                    found_tick = True
                    break

            assert found_tick, f"expected a market_tick message, only saw: {seen_types}"
