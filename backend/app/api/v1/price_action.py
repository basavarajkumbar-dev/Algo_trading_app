"""Price action endpoints.

Read-only and unauthenticated, like the other /market endpoints: the
data is analysis of simulated market data, not account data. Patterns
and levels are contextual features, never trade signals; nothing here
creates or proposes an order.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.price_action.config import PriceActionConfig
from app.price_action.service import PriceActionOverview, PriceActionService, SymbolPriceAction

router = APIRouter(prefix="/market", tags=["price action"])


def _get_service(request: Request) -> PriceActionService:
    service: PriceActionService | None = getattr(request.app.state, "price_action_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Price action service is not running.",
        )
    return service


@router.get("/price-action", response_model=PriceActionOverview)
async def price_action_overview(request: Request) -> PriceActionOverview:
    """One summary row per watched symbol: patterns on the latest closed
    candle, retracement zone, nearest levels and multi-timeframe bias."""

    result = _get_service(request).get_overview()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Price action has not been computed yet.",
        )
    return result


@router.get("/price-action/config", response_model=PriceActionConfig)
async def price_action_config(request: Request) -> PriceActionConfig:
    """Every threshold and weight the price action engine uses."""

    return _get_service(request).config


@router.get("/price-action/{symbol}", response_model=SymbolPriceAction)
async def symbol_price_action(symbol: str, request: Request) -> SymbolPriceAction:
    """Full price action analysis for one symbol, with the entry-timeframe
    candles it was computed from (for charting)."""

    result = _get_service(request).get_symbol(symbol.upper())
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price action analysis for '{symbol}'. It may not be on the watchlist.",
        )
    return result
