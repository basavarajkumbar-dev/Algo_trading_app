"""Live safety-layer configuration (Phase 14), built from settings."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.risk.config import RiskLimits, SessionMode, risk_limits_from_settings


class LiveConfig(BaseModel):
    configured: bool = False  # EXECUTION_MODE=ZERODHA and LIVE_TRADING_ENABLED=true
    max_active_positions: int = Field(default=1, ge=1, le=1)
    entry_timeout_seconds: float = 60.0
    max_position_value: float = 25_000.0
    max_order_quantity: int = 5_000
    max_stop_percent: float = 3.0
    min_risk_reward: float = 1.0
    signal_max_age_seconds: float = 120.0
    close_positions_on_kill: bool = False
    poll_seconds: float = 2.0
    health_max_age_seconds: float = 20.0  # a health report older than this counts as failed


def live_config_from_settings(settings: Any) -> LiveConfig:
    return LiveConfig(
        configured=bool(settings.is_live_trading_possible),
        max_active_positions=settings.live_max_active_positions,
        entry_timeout_seconds=settings.live_entry_timeout_seconds,
        max_position_value=settings.live_max_position_value,
        max_order_quantity=settings.live_max_order_quantity,
        max_stop_percent=settings.live_max_stop_percent,
        min_risk_reward=settings.live_min_risk_reward,
        signal_max_age_seconds=settings.live_signal_max_age_seconds,
        close_positions_on_kill=settings.live_close_positions_on_kill,
        poll_seconds=settings.live_poll_seconds,
    )


def live_risk_limits(settings: Any, config: LiveConfig) -> RiskLimits:
    """The paper limits, tightened for live: the real NSE session always
    (never SIMULATED_24H) and the live per-trade value and quantity caps."""

    base = risk_limits_from_settings(settings)
    session = base.session.model_copy(update={"mode": SessionMode.EXCHANGE})
    return base.model_copy(
        update={
            "session": session,
            "max_position_value": min(base.max_position_value, config.max_position_value),
            "max_exposure": min(base.max_exposure, config.max_position_value),
            "max_order_quantity": min(base.max_order_quantity, config.max_order_quantity),
            "margin_multiplier": 1.0,
            "max_signal_age_seconds": min(
                base.max_signal_age_seconds, config.signal_max_age_seconds
            ),
        }
    )
