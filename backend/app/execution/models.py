"""Broker-neutral order, fill, position and balance models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL_M = "SL_M"  # stop-market: becomes a market order once the trigger trades


class OrderStatus(str, Enum):
    OPEN = "OPEN"  # working (MARKET/LIMIT), or waiting out the execution delay
    TRIGGER_PENDING = "TRIGGER_PENDING"  # SL_M waiting for its trigger
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def is_working(self) -> bool:
        return self in (
            OrderStatus.OPEN,
            OrderStatus.TRIGGER_PENDING,
            OrderStatus.PARTIALLY_FILLED,
        )


class OrderRequest(BaseModel):
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType = OrderType.MARKET
    price: float | None = None  # LIMIT price
    trigger_price: float | None = None  # SL_M trigger
    client_order_id: str | None = None  # idempotency key: a repeat is rejected
    tag: str | None = None  # free-form purpose label (ENTRY / STOP_LOSS / TARGET / EXIT)


class OrderModification(BaseModel):
    quantity: int | None = None
    price: float | None = None
    trigger_price: float | None = None


class ChargeBreakdown(BaseModel):
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.brokerage + self.stt + self.exchange + self.sebi + self.stamp_duty + self.gst, 2
        )

    def __add__(self, other: ChargeBreakdown) -> ChargeBreakdown:
        return ChargeBreakdown(
            **{
                k: round(getattr(self, k) + getattr(other, k), 4)
                for k in ChargeBreakdown.model_fields
            }
        )


class Fill(BaseModel):
    order_id: uuid.UUID
    symbol: str
    side: Side
    quantity: int
    price: float  # after slippage
    market_price: float  # the price that triggered the fill
    slippage: float  # per share, adverse is positive
    charges: ChargeBreakdown
    timestamp: datetime


class Order(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    client_order_id: str | None = None
    tag: str | None = None
    symbol: str
    side: Side
    order_type: OrderType
    quantity: int
    price: float | None = None
    trigger_price: float | None = None
    status: OrderStatus
    filled_quantity: int = 0
    average_price: float | None = None
    triggered: bool = False
    fills: list[Fill] = Field(default_factory=list)
    charges: ChargeBreakdown = Field(default_factory=ChargeBreakdown)
    rejection_reason: str | None = None
    placed_at: datetime
    updated_at: datetime
    executable_after: datetime  # placed_at + execution delay

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity


class Position(BaseModel):
    symbol: str
    quantity: int  # signed: + long, - short
    average_price: float
    last_price: float | None = None
    realized_pnl: float = 0.0  # gross, before charges
    unrealized_pnl: float = 0.0
    charges: float = 0.0

    @property
    def value(self) -> float:
        return abs(self.quantity) * (self.last_price or self.average_price)


class Balance(BaseModel):
    starting_capital: float
    cash: float  # starting capital + realized P&L - charges
    used_margin: float
    available: float
    realized_pnl: float  # gross
    unrealized_pnl: float
    charges_paid: float
    equity: float  # cash + unrealized P&L
