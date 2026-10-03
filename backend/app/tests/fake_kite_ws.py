"""A fake Kite WebSocket server for tests (a real `websockets` server on
127.0.0.1, random port). It speaks the documented protocol: the token in
the URL query, JSON subscribe / unsubscribe / mode requests, binary tick
frames and 1-byte heartbeats. The test pushes ticks and injects faults."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from app.brokers.zerodha.ticker import encode_frame


class FakeKiteWS:
    def __init__(self, *, api_key: str, token: str) -> None:
        self.api_key = api_key
        self.valid_token: str | None = token
        self.messages: list[dict[str, Any]] = []  # every JSON request received
        self.connections = 0
        self.rejected_handshakes = 0
        self.urls: list[str] = []
        self._server: Any = None
        self._clients: set[ServerConnection] = set()
        self.port = 0
        self.heartbeat = True
        self.connected = asyncio.Event()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await serve(
            self._handler, "127.0.0.1", 0, process_request=self._check_auth, compression=None
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _check_auth(self, connection: ServerConnection, request: Request) -> Response | None:
        self.urls.append(request.path)
        q = parse_qs(urlparse(request.path).query)
        if q.get("api_key") != [self.api_key] or q.get("access_token") != [self.valid_token]:
            self.rejected_handshakes += 1
            return Response(403, "Forbidden", Headers(), b"invalid token")
        return None

    async def _handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        self._clients.add(ws)
        self.connected.set()
        beat = asyncio.create_task(self._heartbeat(ws))
        try:
            async for message in ws:
                if isinstance(message, str):
                    self.messages.append(json.loads(message))
        except Exception:  # noqa: BLE001 - a dropped client
            pass
        finally:
            beat.cancel()
            self._clients.discard(ws)

    async def _heartbeat(self, ws: ServerConnection) -> None:
        while True:
            await asyncio.sleep(0.3)
            if self.heartbeat:
                try:
                    await ws.send(b"\x00")
                except Exception:  # noqa: BLE001
                    return

    # --- test controls ----------------------------------------------------------

    async def push(self, packets: list[bytes]) -> None:
        frame = encode_frame(packets)
        for ws in list(self._clients):
            await ws.send(frame)

    async def push_raw(self, data: bytes | str) -> None:
        for ws in list(self._clients):
            await ws.send(data)

    async def drop_all(self) -> None:
        """Server-side disconnect of every client (a network blip)."""

        self.connected.clear()
        for ws in list(self._clients):
            await ws.close(code=1011, reason="server restart")

    def subscribed(self) -> set[int]:
        tokens: set[int] = set()
        for m in self.messages:
            if m.get("a") == "subscribe":
                tokens.update(m["v"])
            elif m.get("a") == "unsubscribe":
                tokens.difference_update(m["v"])
        return tokens

    def modes(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for m in self.messages:
            if m.get("a") == "mode":
                mode, tokens = m["v"]
                for t in tokens:
                    out[t] = mode
        return out

    def clear(self) -> None:
        self.messages.clear()


async def wait_for(predicate: Any, timeout: float = 5.0, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)
