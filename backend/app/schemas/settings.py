"""Schemas for the per-user TradingSettings foundation.

Phase 2 scope: read the current user's settings row (created with safe
defaults at registration) and allow updating the handful of fields that
carry no trading-logic risk yet. Fields that actually gate execution
(auto_trading_enabled) are intentionally NOT editable through this
endpoint until the phases that give them real meaning exist — see
docs/development-roadmap.md Phase 11.
"""

from __future__ import annotations

import uuid
from datetime import time

from pydantic import BaseModel, Field

from app.core.constants import TradingMode


class TradingSettingsResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    trading_mode: TradingMode
    paper_capital: float
    auto_trading_enabled: bool
    signal_score_threshold: float
    trading_start_time: time
    trading_end_time: time
    emergency_stop: bool

    model_config = {"from_attributes": True}


class TradingSettingsUpdateRequest(BaseModel):
    """Fields a user may adjust in this phase.

    Notably absent: `trading_mode` and `auto_trading_enabled`. Changing
    trading mode is a safety-critical action (PROJECT_CONTRACT.md
    section 9) that deserves its own explicit, audited endpoint once a
    real trading engine exists to make that setting meaningful — not a
    field on a generic settings PATCH.
    """

    paper_capital: float | None = Field(default=None, gt=0)
    signal_score_threshold: float | None = Field(default=None, ge=0, le=100)
    trading_start_time: time | None = None
    trading_end_time: time | None = None
