"""Stock universes: definitions, constituents, the active scanner universe."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import require_role
from app.core.constants import UserRole
from app.universe.loader import (
    ConstituentList,
    UniverseReport,
    UniverseStore,
    UniverseUnavailable,
    resolve_universe,
)
from app.universe.registry import UNIVERSES

router = APIRouter(prefix="/universes", tags=["universes"])
_operator = require_role(UserRole.ADMIN, UserRole.TRADER)


def _store(request: Request) -> UniverseStore:
    store = getattr(request.app.state, "universe_store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Universe store not started")
    return store


class ResolveRequest(BaseModel):
    universes: list[str] = Field(default_factory=list, max_length=20)
    symbols: list[str] = Field(default_factory=list, max_length=3000)
    limit: int | None = Field(default=None, ge=1, le=3000)


@router.get("")
async def list_universes(request: Request) -> dict[str, Any]:
    store = _store(request)
    active: UniverseReport | None = getattr(request.app.state, "universe_report", None)
    return {
        "universes": await store.status(),
        "active": (
            {
                "requested_universes": active.requested_universes,
                "requested_count": active.requested_count,
                "eligible_count": active.eligible_count,
                "excluded_count": active.excluded_count,
                "processed_count": active.processed_count,
                "truncated": active.truncated,
                "limit": active.limit,
            }
            if active
            else None
        ),
        "note": (
            "Index lists come from the official NSE Indices files and are cached; the "
            "scanner universe is set with SCANNER_UNIVERSES and applied at startup."
        ),
    }


@router.get("/active", response_model=UniverseReport)
async def active_universe(request: Request) -> UniverseReport:
    report = getattr(request.app.state, "universe_report", None)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No universe resolved yet")
    return report


@router.post("/resolve", response_model=UniverseReport)
async def resolve(body: ResolveRequest, request: Request) -> UniverseReport:
    """Counts for a combined universe (duplicates removed), without an
    eligibility check against a data source."""

    unknown = [u for u in body.universes if u.upper() not in UNIVERSES]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown universe: {unknown}")
    try:
        return await resolve_universe(
            _store(request), body.universes, symbols=body.symbols, limit=body.limit
        )
    except UniverseUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/{universe_id}", response_model=ConstituentList)
async def constituents(universe_id: str, request: Request) -> ConstituentList:
    if universe_id.upper() not in UNIVERSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown universe {universe_id}")
    try:
        return await _store(request).get(universe_id)
    except UniverseUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.post("/{universe_id}/refresh", response_model=ConstituentList)
async def refresh(
    universe_id: str, request: Request, _: Any = Depends(_operator)
) -> ConstituentList:
    if universe_id.upper() not in UNIVERSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown universe {universe_id}")
    try:
        return await _store(request).get(universe_id, refresh=True)
    except UniverseUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
