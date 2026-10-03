"""Kite funds and margins (GET /user/margins/equity), mapped to Balance
and Margins. Read-only."""

from __future__ import annotations

from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.mapping import balance_from_kite, margins_from_kite
from app.execution.models import Balance, Margins


async def equity_margins(client: KiteClient) -> Margins:
    return margins_from_kite(await client.margins_equity())


async def equity_balance(client: KiteClient) -> Balance:
    return balance_from_kite(await client.margins_equity())


__all__ = ["balance_from_kite", "equity_balance", "equity_margins", "margins_from_kite"]
