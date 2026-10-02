"""Paper trading models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.risk.schemas import TradeSide

PAPER_NOTE = (
    "PAPER trading: simulated orders and fills against simulated market data. "
    "No broker is connected and no real money is used. Fills, slippage and "
    "charges are configurable assumptions, so paper results will differ from "
    "live results."
)


class TradeStatus(str, Enum):
    PENDING = "PENDING"  # entry order working, nothing filled
    OPEN = "OPEN"  # position held, protective orders working
    EXITING = "EXITING"  # exit order working
    CLOSED = "CLOSED"  # flat; P&L final
    CANCELLED = "CANCELLED"  # entry never filled
    REJECTED = "REJECTED"  # broker rejected the entry
    INTERRUPTED = "INTERRUPTED"  # the process stopped with the trade open


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"  # the protective stop after it was trailed
    TARGET = "TARGET"
    TIME_EXIT = "TIME_EXIT"  # square-off time
    MANUAL = "MANUAL"
    EMERGENCY = "EMERGENCY"
    ENTRY_EXPIRED = "ENTRY_EXPIRED"  # entry not filled in time
    ENTRY_REJECTED = "ENTRY_REJECTED"


class OrderPurpose(str, Enum):
    ENTRY = "ENTRY"
    STOP_LOSS = "STOP_LOSS"
    TARGET = "TARGET"
    EXIT = "EXIT"


class PaperTrade(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    signal_id: uuid.UUID | None = None
    symbol: str
    side: TradeSide
    strategy: str | None = None
    score: float | None = None
    status: TradeStatus = TradeStatus.PENDING
    planned_entry: float
    stop_loss: float
    target: float
    quantity: int  # approved quantity
    filled_quantity: int = 0
    open_quantity: int = 0
    entry_price: float | None = None  # average fill
    exit_price: float | None = None  # average exit fill
    last_price: float | None = None
    initial_risk: float  # approved quantity x |entry - stop|
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    r_multiple: float | None = None  # net P&L / planned risk (planned entry to stop) x filled qty
    exit_reason: ExitReason | None = None
    trailing_stop: float | None = (
        None  # current stop once trailed; stop_loss stays the initial stop
    )
    created_at: datetime
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    order_ids: dict[str, list[uuid.UUID]] = Field(default_factory=dict)  # purpose -> ids
    risk_summary: str = ""
    risk_checks: list[dict[str, Any]] = Field(default_factory=list)
    note: str = PAPER_NOTE


class EngineEvent(BaseModel):
    """A notable engine event, persisted as a system event."""

    event_type: str
    severity: str = "INFO"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    at: datetime
