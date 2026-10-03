"""Kite portfolio: net positions (GET /portfolio/positions) and holdings
(GET /portfolio/holdings), mapped to broker-independent models. Read-only."""

from __future__ import annotations

from typing import Any

from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.mapping import position_from_kite
from app.execution.models import Position


async def net_positions(client: KiteClient) -> list[Position]:
    data = await client.positions()
    return [position_from_kite(p) for p in data.get("net", [])]


async def holdings(client: KiteClient) -> list[dict[str, Any]]:
    """Delivery holdings as Kite reports them (not used for intraday trading)."""

    return await client.holdings()


__all__ = ["holdings", "net_positions", "position_from_kite"]
