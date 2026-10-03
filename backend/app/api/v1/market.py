"""Market data REST endpoints.

No authentication required: this is read-only, simulated market
information, not account/trading data. Route handlers stay thin — all
state lives in the MarketDataService attached to `app.state`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.market_data.schemas import Timeframe
from app.market_data.service import MarketDataService
from app.schemas.market import (
    CandleResponse,
    InstrumentResponse,
    MarketSnapshotResponse,
    MarketStatusResponse,
)

router = APIRouter(prefix="/market", tags=["market"])


def _get_service(request: Request) -> MarketDataService:
    service: MarketDataService | None = getattr(request.app.state, "market_data_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data service is not running.",
        )
    return service


@router.get("/status", response_model=MarketStatusResponse)
async def market_status(request: Request) -> MarketStatusResponse:
    service = _get_service(request)
    provider_status = await service.get_provider_status()
    stats = service.stats

    return MarketStatusResponse(
        provider_status=provider_status,
        is_streaming=service.is_running,
        watchlist=service.get_watchlist(),
        connected_websocket_clients=service.connection_manager.connection_count,
        ticks_received=stats.ticks_received,
        ticks_accepted=stats.ticks_accepted,
        ticks_rejected=stats.ticks_rejected,
        rejected_by_issue=stats.rejected_by_issue,
        started_at=stats.started_at,
        source=service.source,
        data_source="SIMULATED" if service.simulated else "ZERODHA_LIVE",
        data_status=service.data_status(),
        notes=[
            (
                "Market data is simulated (mock provider) — not a live feed."
                if service.simulated
                else "Market data is real NSE data from Zerodha Kite Connect."
            ),
            "No real orders are placed: execution is PAPER.",
        ],
    )


@router.get("/instruments", response_model=list[InstrumentResponse])
async def list_instruments(request: Request) -> list[InstrumentResponse]:
    service = _get_service(request)
    instruments = await service.get_instruments()
    return [
        InstrumentResponse(
            symbol=i.symbol,
            name=i.name,
            exchange=i.exchange,
            sector=i.sector,
            is_active=i.is_active,
        )
        for i in instruments
    ]


@router.get("/snapshot/{symbol}", response_model=MarketSnapshotResponse)
async def get_snapshot(symbol: str, request: Request) -> MarketSnapshotResponse:
    service = _get_service(request)
    snapshot = service.get_snapshot(symbol.upper())
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No market data yet for symbol '{symbol}'. It may not be on the "
            "watchlist, or no tick has arrived since startup.",
        )
    return MarketSnapshotResponse(
        symbol=snapshot.symbol,
        price=snapshot.price,
        change=snapshot.change,
        change_percent=snapshot.change_percent,
        volume=snapshot.volume,
        timestamp=snapshot.timestamp,
    )


@router.get("/candles", response_model=list[CandleResponse])
async def get_candles(
    request: Request,
    symbol: str = Query(...),
    timeframe: Timeframe = Query(default=Timeframe.M5),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[CandleResponse]:
    service = _get_service(request)
    candles = service.get_candles(symbol.upper(), timeframe, limit)
    return [
        CandleResponse(
            symbol=c.symbol,
            timeframe=c.timeframe,
            timestamp=c.timestamp,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            is_closed=c.is_closed,
        )
        for c in candles
    ]
