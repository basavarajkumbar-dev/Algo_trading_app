"""Response schemas for /market endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.market_data.schemas import MarketDataProviderStatus, Timeframe


class InstrumentResponse(BaseModel):
    symbol: str
    name: str
    exchange: str
    sector: str | None
    is_active: bool


class MarketSnapshotResponse(BaseModel):
    symbol: str
    price: float
    change: float
    change_percent: float
    volume: int
    timestamp: datetime


class CandleResponse(BaseModel):
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_closed: bool


class MarketStatusResponse(BaseModel):
    provider_status: MarketDataProviderStatus
    is_streaming: bool
    watchlist: list[str]
    connected_websocket_clients: int
    ticks_received: int
    ticks_accepted: int
    ticks_rejected: int
    rejected_by_issue: dict[str, int]
    started_at: datetime | None
    # Phase 13: where the data comes from. "SIMULATED" data must never be
    # shown as real; "ZERODHA_LIVE" is real NSE data from Kite Connect.
    source: str = "MOCK"
    data_source: str = "SIMULATED"
    data_status: str | None = None
    notes: list[str]
