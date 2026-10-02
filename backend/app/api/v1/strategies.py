"""Strategy endpoints.

Read-only and unauthenticated, like the /market endpoints: they show
strategy *candidates* computed from simulated data. Nothing here sizes,
submits or schedules an order, and there is no route from these
handlers to any broker.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.regime.schemas import MarketRegime
from app.strategies.config import StrategyEngineConfig
from app.strategies.schemas import RouterDecision
from app.strategies.service import StrategyOverview, StrategyService, SymbolStrategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _get_service(request: Request) -> StrategyService:
    service: StrategyService | None = getattr(request.app.state, "strategy_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy service is not running.",
        )
    return service


@router.get("", response_model=StrategyOverview)
async def strategies_overview(request: Request) -> StrategyOverview:
    """Per symbol: regime, the strategies the router allows for it, and
    any eligible candidates."""

    result = _get_service(request).get_overview()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategies have not been evaluated yet.",
        )
    return result


@router.get("/config", response_model=StrategyEngineConfig)
async def strategies_config(request: Request) -> StrategyEngineConfig:
    """Router mapping and every strategy threshold and weight."""

    return _get_service(request).config


@router.get("/route", response_model=RouterDecision)
async def route_regime(
    request: Request,
    regime: MarketRegime = Query(...),
    confidence: float = Query(100.0, ge=0, le=100),
) -> RouterDecision:
    """What the router allows for a given regime and confidence."""

    return _get_service(request).router.route(regime, confidence)


@router.get("/{symbol}", response_model=SymbolStrategies)
async def symbol_strategies(
    symbol: str,
    request: Request,
    include_unrouted: bool = Query(
        False, description="Also evaluate every strategy regardless of routing (research view)."
    ),
) -> SymbolStrategies:
    result = _get_service(request).get_symbol(symbol.upper(), include_unrouted=include_unrouted)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No strategy evaluation for '{symbol}'. It may not be on the watchlist.",
        )
    return result
