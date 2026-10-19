"""WebSocket endpoint for real-time market data updates.

Foundation only: broadcasts `market_tick` and `candle_closed` events to
every connected client (no per-client symbol filtering yet — see
app.websocket.manager's module docstring). On connect, sends an
immediate acknowledgement plus the current snapshot for every watched
symbol, so a client has something to render before the next tick
arrives.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import websocket_allowed
from app.core.config import get_settings
from app.core.logging import get_logger
from app.market_data.service import MarketDataService

logger = get_logger(__name__)

router = APIRouter()


AUTH_TIMEOUT_SECONDS = 10.0


async def _authenticate(websocket: WebSocket) -> bool:
    """When API_REQUIRE_AUTH is on, the client's first message must be
    {"type": "auth", "token": "<access token>"} (sent within 10 s). The
    token travels in the message, never in the URL, so it cannot end up in
    access logs."""

    settings = get_settings()
    if not settings.effective_api_require_auth:
        return True
    try:
        message = await asyncio.wait_for(websocket.receive_json(), AUTH_TIMEOUT_SECONDS)
    except (TimeoutError, ValueError, KeyError, WebSocketDisconnect):
        return False
    token = message.get("token") if isinstance(message, dict) else None
    return message.get("type") == "auth" and await websocket_allowed(token, settings)


@router.websocket("/ws/market")
async def market_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    if not await _authenticate(websocket):
        await websocket.close(code=1008)  # 1008 = policy violation: sign in first
        return
    service: MarketDataService | None = getattr(websocket.app.state, "market_data_service", None)
    if service is None:
        await websocket.close(code=1013)  # 1013 = Try Again Later
        return

    manager = service.connection_manager
    await manager.connect(websocket)

    try:
        await manager.send_to(
            websocket,
            {
                "type": "connected",
                "message": (
                    "Connected to GAINZALGO PRO MAX market data stream "
                    f"({'simulated' if service.simulated else 'Zerodha live'})."
                ),
                "source": service.source,
                "simulated": service.simulated,
                "watchlist": service.get_watchlist(),
            },
        )

        for symbol in service.get_watchlist():
            snapshot = service.get_snapshot(symbol)
            if snapshot is not None:
                await manager.send_to(
                    websocket,
                    {
                        "type": "market_snapshot",
                        "symbol": snapshot.symbol,
                        "price": snapshot.price,
                        "change": snapshot.change,
                        "change_percent": snapshot.change_percent,
                        "volume": snapshot.volume,
                        "timestamp": snapshot.timestamp.isoformat(),
                    },
                )

        # Current market intelligence, if computed, so the client doesn't
        # wait for the next refresh cycle to render the regime panel.
        intelligence = getattr(websocket.app.state, "market_intelligence_service", None)
        current = intelligence.get_intelligence() if intelligence is not None else None
        if current is not None:
            await manager.send_to(
                websocket,
                {
                    "type": "market_intelligence",
                    **current.model_dump(mode="json", exclude={"status"}),
                },
            )

        # This WebSocket is broadcast-only (server -> client).
        # We still need to await incoming messages so the server notices
        # a client disconnect promptly; any inbound message is ignored.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
