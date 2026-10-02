"""Risk limits and market-session configuration.

Every value here is a configurable assumption, read from environment
variables by `risk_limits_from_settings`. The defaults mirror
PROJECT_CONTRACT.md ("Risk Engine") and the risk_configs table defaults:
1% risk per trade, 3% daily loss limit, 5 trades a day, stop after 3
consecutive losses, ₹50,000 maximum position value.
"""

from __future__ import annotations

from datetime import date, time
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from app.core.config import Settings


class SessionMode(str, Enum):
    # NSE cash-market hours, Monday to Friday, in IST. Exchange holidays
    # are not built in; list them in TRADING_HOLIDAYS.
    EXCHANGE = "EXCHANGE"
    # The simulated feed runs around the clock. This mode treats every
    # moment as in-session (no square-off) so the paper lifecycle can be
    # exercised outside exchange hours. Never the default.
    SIMULATED_24H = "SIMULATED_24H"


class MarketSessionConfig(BaseModel):
    mode: SessionMode = SessionMode.EXCHANGE
    open_time: time = time(9, 15)
    close_time: time = time(15, 30)
    # No new entries at or after this time.
    last_entry_time: time = time(15, 0)
    # Open positions are closed at market at or after this time.
    square_off_time: time = time(15, 15)
    holidays: list[date] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> MarketSessionConfig:
        if not (self.open_time < self.last_entry_time <= self.square_off_time <= self.close_time):
            raise ValueError("Session times must satisfy open < last entry <= square-off <= close")
        return self


class RiskLimits(BaseModel):
    """Configurable limits enforced by the RiskManager."""

    # --- sizing ----------------------------------------------------------
    risk_per_trade_percent: float = Field(default=1.0, gt=0, le=5)
    lot_size: int = Field(default=1, ge=1)  # shares per lot (1 for NSE cash equities)
    tick_size: float = Field(default=0.05, gt=0)
    max_order_quantity: int = Field(default=100_000, ge=1)  # broker freeze-quantity style cap
    max_position_value: float = Field(default=50_000.0, gt=0)  # per trade, rupees
    # Margin multiplier applied to available capital. 1.0 = no leverage,
    # the conservative paper default.
    margin_multiplier: float = Field(default=1.0, ge=1, le=5)

    # --- account limits --------------------------------------------------
    daily_loss_limit_percent: float = Field(default=3.0, gt=0, le=20)
    max_trades_per_day: int = Field(default=5, ge=1)
    max_consecutive_losses: int = Field(default=3, ge=1)
    max_exposure: float = Field(default=50_000.0, gt=0)  # total open position value

    # --- signal and data quality -----------------------------------------
    min_signal_score: float = Field(default=85.0, ge=0, le=100)
    max_data_age_seconds: float = Field(default=30.0, gt=0)  # latest price
    max_signal_age_seconds: float = Field(default=120.0, gt=0)  # signal last seen in a scan
    # Reject when the live price has moved this far against the signal
    # entry, as a fraction of the planned stop distance.
    max_entry_deviation_r: float = Field(default=0.25, ge=0, le=1)
    allow_short: bool = True

    session: MarketSessionConfig = Field(default_factory=MarketSessionConfig)


def risk_limits_from_settings(settings: Settings) -> RiskLimits:
    """Build limits from the environment-driven settings."""

    def g(name: str):  # noqa: ANN202 - plain attribute read
        return getattr(settings, name)

    return RiskLimits(
        risk_per_trade_percent=g("risk_per_trade"),
        daily_loss_limit_percent=g("max_daily_loss"),
        max_trades_per_day=g("max_trades_per_day"),
        max_consecutive_losses=g("risk_max_consecutive_losses"),
        max_position_value=g("risk_max_position_value"),
        max_exposure=g("risk_max_exposure"),
        lot_size=g("risk_lot_size"),
        tick_size=g("risk_tick_size"),
        max_order_quantity=g("risk_max_order_quantity"),
        margin_multiplier=g("risk_margin_multiplier"),
        min_signal_score=g("signal_executable_threshold"),
        max_data_age_seconds=g("risk_max_data_age_seconds"),
        max_signal_age_seconds=g("risk_max_signal_age_seconds"),
        max_entry_deviation_r=g("risk_max_entry_deviation_r"),
        allow_short=g("risk_allow_short"),
        session=MarketSessionConfig(
            mode=g("trading_session_mode"),
            last_entry_time=g("trading_last_entry_time"),
            square_off_time=g("trading_square_off_time"),
            holidays=g("trading_holidays"),
        ),
    )
