"""Risk engine models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

RISK_NOTE = (
    "Risk checks limit how much a single paper trade and a trading day can "
    "lose under the configured assumptions. Passing every check does not "
    "make a trade profitable, and slippage or gaps can push a loss past the "
    "planned risk."
)


class TradeSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class RejectionReason(str, Enum):
    TRADING_MODE_NOT_PAPER = "TRADING_MODE_NOT_PAPER"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    SYSTEM_UNHEALTHY = "SYSTEM_UNHEALTHY"
    MARKET_CLOSED = "MARKET_CLOSED"
    DATA_STALE = "DATA_STALE"
    ACTIVE_POSITION_EXISTS = "ACTIVE_POSITION_EXISTS"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_TRADES_REACHED = "MAX_TRADES_REACHED"
    MAX_CONSECUTIVE_LOSSES = "MAX_CONSECUTIVE_LOSSES"
    SIGNAL_NOT_EXECUTABLE = "SIGNAL_NOT_EXECUTABLE"
    SHORT_NOT_ALLOWED = "SHORT_NOT_ALLOWED"
    INVALID_STOP = "INVALID_STOP"
    INVALID_TARGET = "INVALID_TARGET"
    PRICE_MOVED_AWAY = "PRICE_MOVED_AWAY"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"
    MAX_EXPOSURE = "MAX_EXPOSURE"


class TradeIntent(BaseModel):
    """What the engine wants to do, before any order exists."""

    signal_id: uuid.UUID | None = None
    symbol: str
    side: TradeSide
    entry: float  # planned entry from the signal
    stop_loss: float
    target: float
    score: float | None = None
    strategy: str | None = None
    signal_last_seen_at: datetime | None = None  # when a scan last produced the signal
    signal_active: bool = True


class MarketConditions(BaseModel):
    """Runtime facts the risk check needs; gathered by the trading service."""

    now: datetime
    last_price: float | None
    price_timestamp: datetime | None
    system_healthy: bool
    health_detail: str = ""
    trading_mode: str = "PAPER"


class PositionSize(BaseModel):
    quantity: int
    raw_quantity: float  # risk_amount / per-unit risk, before constraints
    risk_amount: float  # capital x risk percentage
    risk_per_unit: float  # |entry - stop| after tick rounding
    actual_risk: float  # quantity x risk per unit
    position_value: float  # quantity x entry
    entry: float  # tick-rounded reference entry
    stop_loss: float  # tick-rounded stop
    constraints: list[str] = Field(default_factory=list)  # which caps reduced the quantity


class RiskCheck(BaseModel):
    name: str
    passed: bool
    detail: str
    reason: RejectionReason | None = None  # set when the check failed


class RiskDecision(BaseModel):
    approved: bool
    intent: TradeIntent
    size: PositionSize | None
    checks: list[RiskCheck]
    rejection_reasons: list[RejectionReason]
    evaluated_at: datetime
    note: str = RISK_NOTE

    @property
    def summary(self) -> str:
        if self.approved:
            assert self.size is not None
            return (
                f"Approved {self.intent.side.value} {self.size.quantity} {self.intent.symbol} "
                f"risking ₹{self.size.actual_risk:,.2f}"
            )
        return "Rejected: " + ", ".join(r.value for r in self.rejection_reasons)


class RiskState(BaseModel):
    """Today's counters (IST trading day)."""

    trading_day: date
    day_start_capital: float
    trades_today: int = 0
    realized_pnl_today: float = 0.0  # net of charges
    consecutive_losses: int = 0
    wins_today: int = 0
    losses_today: int = 0
    executed_signal_ids: list[uuid.UUID] = Field(default_factory=list)


class RiskSnapshot(BaseModel):
    """The risk picture shown by the API."""

    state: RiskState
    capital: float
    available_capital: float
    open_exposure: float
    daily_loss_limit_amount: float
    daily_loss_remaining: float
    risk_per_trade_amount: float
    trades_remaining: int
    consecutive_losses_remaining: int
    entries_blocked: bool
    blocked_reasons: list[RejectionReason]
    note: str = RISK_NOTE
