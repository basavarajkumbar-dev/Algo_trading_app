"""Analytics endpoint (Phase 9).

GET /analytics/performance   equity curve, drawdown, daily P&L, win rate,
                             profit factor and per-strategy results over
                             the stored paper trades

Read-only. Paper results on simulated data; not a forecast and not
evidence of profitability.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.performance import PerformanceReport, compute_performance
from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.trading.repository import list_trades

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_TRADES = 5000


@router.get("/performance", response_model=PerformanceReport)
async def performance(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> PerformanceReport:
    try:
        trades, _ = await list_trades(db, limit=MAX_TRADES)
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Trade storage unavailable: {type(exc).__name__}",
        ) from exc
    return compute_performance(trades, starting_capital=settings.paper_capital)
