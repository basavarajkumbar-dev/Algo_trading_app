"""WebSocket connection manager.

Tracks connected clients and broadcasts JSON-serializable messages to
all of them. Deliberately minimal in this phase — per-client
subscription filtering (only sending ticks for symbols a client cares
about) is a reasonable future enhancement, not required for the
"WebSocket foundation" this phase asks for.
"""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        if (
            getattr(websocket, "client_state", WebSocketState.CONNECTING)
            == WebSocketState.CONNECTING
        ):
            await websocket.accept()  # the endpoint may have accepted already (auth)
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def send_to(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        await websocket.send_json(message)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to every connected client.

        A send failure on one client (e.g. it disconnected without a
        clean close) removes that client rather than raising and
        blocking delivery to everyone else.
        """

        dead: list[WebSocket] = []
        for connection in list(self._connections):
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001 - broadcast must not crash on one bad client
                dead.append(connection)

        for connection in dead:
            self.disconnect(connection)

    @property
    def connection_count(self) -> int:
        return len(self._connections)
