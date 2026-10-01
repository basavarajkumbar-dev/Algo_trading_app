"""Trading settings endpoints (foundation only).

Route handlers stay thin — logic lives in app.services.settings_service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database.models.user import User
from app.database.session import get_db
from app.schemas.settings import TradingSettingsResponse, TradingSettingsUpdateRequest
from app.services.settings_service import get_trading_settings, update_trading_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/me", response_model=TradingSettingsResponse)
async def read_my_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TradingSettingsResponse:
    settings_row = await get_trading_settings(db, user_id=current_user.id)
    return TradingSettingsResponse.model_validate(settings_row)


@router.patch("/me", response_model=TradingSettingsResponse)
async def update_my_settings(
    payload: TradingSettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TradingSettingsResponse:
    settings_row = await update_trading_settings(db, user_id=current_user.id, update=payload)
    return TradingSettingsResponse.model_validate(settings_row)
