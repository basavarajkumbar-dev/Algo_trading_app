"""Trading settings service (foundation only — see
app.schemas.settings for what this phase intentionally does not expose).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.trading_settings import TradingSettings
from app.schemas.settings import TradingSettingsUpdateRequest


class TradingSettingsNotFoundError(Exception):
    pass


async def get_trading_settings(db: AsyncSession, *, user_id: uuid.UUID) -> TradingSettings:
    result = await db.execute(select(TradingSettings).where(TradingSettings.user_id == user_id))
    settings_row = result.scalar_one_or_none()
    if settings_row is None:
        # Should be unreachable — register_user always creates this row —
        # but surfaced explicitly rather than silently returning None,
        # per PROJECT_CONTRACT.md's fail-closed principle.
        raise TradingSettingsNotFoundError(str(user_id))
    return settings_row


async def update_trading_settings(
    db: AsyncSession, *, user_id: uuid.UUID, update: TradingSettingsUpdateRequest
) -> TradingSettings:
    settings_row = await get_trading_settings(db, user_id=user_id)

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings_row, field, value)

    await db.commit()
    await db.refresh(settings_row)
    return settings_row
