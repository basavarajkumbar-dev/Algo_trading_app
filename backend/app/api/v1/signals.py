"""Scanner and signal endpoints.

GET /scanner          latest NIFTY 50 scan (rows, ranked candidates, market context)
GET /signals          stored signals, newest first, filterable
GET /signals/config   scoring weights, categories, thresholds
GET /signals/{id}     one stored signal with its full explanation

Read-only and unauthenticated, like the /market endpoints: everything
here is computed from simulated data. Signals are scored candidates,
not orders, and a rank is not proof of profitability.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.scanner.nifty50 import ScanResult
from app.signals.config import SignalEngineConfig
from app.signals.repository import SignalRecord, get_signal, list_signals
from app.signals.schemas import SIGNAL_NOTE, SignalCategory, SignalStatus, TradingSignal
from app.signals.service import SignalService, SignalServiceStatus
from app.strategies.schemas import StrategyName

router = APIRouter(tags=["scanner and signals"])


class ScannerResponse(BaseModel):
    scan: ScanResult
    status: SignalServiceStatus


class StoredSignal(BaseModel):
    signal: TradingSignal
    generated_at: datetime
    last_seen_at: datetime
    expired_at: datetime | None
    last_score: float
    last_rank: int | None
    times_seen: int


class SignalList(BaseModel):
    items: list[StoredSignal]
    total: int
    limit: int
    offset: int
    note: str = SIGNAL_NOTE


def _service(request: Request) -> SignalService:
    service: SignalService | None = getattr(request.app.state, "signal_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Signal service is not running."
        )
    return service


def _stored(record: SignalRecord) -> StoredSignal:
    return StoredSignal(
        signal=record.signal,
        generated_at=record.generated_at,
        last_seen_at=record.last_seen_at,
        expired_at=record.expired_at,
        last_score=record.last_score,
        last_rank=record.last_rank,
        times_seen=record.times_seen,
    )


def _db_unavailable(exc: SQLAlchemyError | OSError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Signal storage is unavailable ({type(exc).__name__}).",
    )


@router.get("/scanner", response_model=ScannerResponse)
async def scanner(request: Request) -> ScannerResponse:
    """The latest NIFTY 50 scan: one row per stock with every analysed
    dimension, plus ranked candidates and the top candidates."""

    service = _service(request)
    scan = service.get_scan()
    if scan is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No scan has completed yet."
        )
    return ScannerResponse(scan=scan, status=service.status())


@router.get("/signals", response_model=SignalList)
async def signals(
    status_: SignalStatus | None = Query(None, alias="status"),
    category: SignalCategory | None = None,
    symbol: str | None = None,
    strategy: StrategyName | None = None,
    executable: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SignalList:
    try:
        records, total = await list_signals(
            db,
            status=status_.value if status_ else None,
            category=category.value if category else None,
            symbol=symbol.upper() if symbol else None,
            strategy=strategy.value if strategy else None,
            executable=executable,
            limit=limit,
            offset=offset,
        )
    except (SQLAlchemyError, OSError) as exc:
        raise _db_unavailable(exc) from exc
    return SignalList(items=[_stored(r) for r in records], total=total, limit=limit, offset=offset)


@router.get("/signals/config", response_model=SignalEngineConfig)
async def signals_config(request: Request) -> SignalEngineConfig:
    """Scoring weights (the brief's initial model, not assumed optimal),
    category bands, executable threshold, quantity and ranking settings."""

    return _service(request).config


@router.get("/signals/{signal_id}", response_model=StoredSignal)
async def signal_detail(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> StoredSignal:
    try:
        record = await get_signal(db, signal_id)
    except (SQLAlchemyError, OSError) as exc:
        raise _db_unavailable(exc) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found.")
    return _stored(record)
