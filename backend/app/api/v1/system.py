"""System endpoints: GET /api/v1/system/health.

Route handlers stay thin — all logic lives in
app.services.system_health per PROJECT_CONTRACT.md section 2.
"""

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.system import SystemHealthResponse
from app.services.system_health import get_system_health

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=SystemHealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> SystemHealthResponse:
    """Report API, database, and Redis connectivity.

    This does NOT indicate that any market data, strategy, or trading
    subsystem is healthy — those are introduced in later phases.
    """

    return await get_system_health(settings)
