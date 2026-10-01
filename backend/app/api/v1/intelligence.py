"""Market intelligence and regime endpoints.

Read-only and unauthenticated, like the other /market endpoints: the
data is simulated market analysis, not account data. Handlers are thin;
all computation happens in MarketIntelligenceService.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.market_intelligence.analyzer import AnalysisConfig
from app.market_intelligence.models import MarketIntelligenceResponse, SymbolIntelligence
from app.market_intelligence.service import MarketIntelligenceService
from app.regime.config import RegimeConfig
from app.regime.schemas import RegimeResult

router = APIRouter(prefix="/market", tags=["market intelligence"])


class IntelligenceConfigResponse(BaseModel):
    benchmark_symbol: str
    analysis: AnalysisConfig
    regime: RegimeConfig


def _get_service(request: Request) -> MarketIntelligenceService:
    service: MarketIntelligenceService | None = getattr(
        request.app.state, "market_intelligence_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market intelligence service is not running.",
        )
    return service


@router.get("/intelligence", response_model=MarketIntelligenceResponse)
async def market_intelligence(request: Request) -> MarketIntelligenceResponse:
    """Market snapshot (benchmark regime, breadth, factor scores) plus a
    regime summary row for every watched symbol."""

    result = _get_service(request).get_intelligence()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market intelligence has not been computed yet.",
        )
    return result


@router.get("/intelligence/config", response_model=IntelligenceConfigResponse)
async def intelligence_config(request: Request) -> IntelligenceConfigResponse:
    """Every weight and threshold the engines and regime detector use."""

    service = _get_service(request)
    return IntelligenceConfigResponse(
        benchmark_symbol=service.benchmark_symbol,
        analysis=service.analysis_config,
        regime=service.regime_config,
    )


@router.get("/intelligence/{symbol}", response_model=SymbolIntelligence)
async def symbol_intelligence(symbol: str, request: Request) -> SymbolIntelligence:
    """Full engine output, factor scores and regime for one symbol."""

    result = _get_service(request).get_symbol(symbol.upper())
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No intelligence for '{symbol}'. It may not be on the watchlist.",
        )
    return result


@router.get("/regime", response_model=RegimeResult)
async def market_regime(
    request: Request,
    symbol: str | None = Query(default=None, description="Defaults to the benchmark (NIFTY50)."),
) -> RegimeResult:
    service = _get_service(request)
    target = (symbol or service.benchmark_symbol).upper()
    result = service.get_regime(target)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regime for '{target}'. It may not be on the watchlist.",
        )
    return result
