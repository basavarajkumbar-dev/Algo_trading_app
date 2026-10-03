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


class Product(str, Enum):
    """Broker product. Intraday (MIS) is the only one the trading engine uses."""

    MIS = "MIS"  # intraday, squared off by the broker
    CNC = "CNC"  # delivery


class ConnectionState(str, Enum):
    """Explicit broker connection states (contract section 9.4)."""

    NOT_CONFIGURED = "NOT_CONFIGURED"  # no credentials: the app runs in PAPER mode
    DISCONNECTED = "DISCONNECTED"  # configured, not connected (yet / any more)
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"  # connected, but recent calls failed
    AUTH_REQUIRED = "AUTH_REQUIRED"  # token missing, expired or rejected: log in again


class OrderStatus(str, Enum):
    # sent, accepted by the broker, not yet confirmed working at the exchange;
    # also the state of an order whose placement outcome is unknown (network
    # failure) until reconciliation finds it at the broker or gives up
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"  # working (MARKET/LIMIT), or waiting out the execution delay
    TRIGGER_PENDING = "TRIGGER_PENDING"  # SL_M waiting for its trigger
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def is_working(self) -> bool:
        return self in (
            OrderStatus.SUBMITTED,
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
    # Phase 13 traceability: the signal that produced the order and an id
    # shared by every order of one trade (entry, stop, target, exit).
    strategy_signal_id: str | None = None
    correlation_id: str | None = None
    exchange: str = "NSE"
    product: Product = Product.MIS
    # live brokers: price band for MARKET / SL_M orders. Zerodha rejects 0 since
    # 1 April 2026; -1 asks for the broker's automatic protection.
    market_protection: float | None = None


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
    strategy_signal_id: str | None = None
    correlation_id: str | None = None
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
    # live brokers
    exchange: str = "NSE"
    product: Product = Product.MIS
    broker_order_id: str | None = None
    exchange_order_id: str | None = None
    broker_status: str | None = None  # the broker's own status text, unmapped
    broker_tag: str | None = None  # what the broker sees (idempotency tag)
    cancelled_quantity: int = 0
    status_message: str | None = None
    modifications: int = 0
    needs_reconciliation: bool = False  # placement outcome unknown (network failure)

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


class Trade(BaseModel):
    """One execution (a fill) as the broker reports it."""

    trade_id: str
    order_id: uuid.UUID | None = None  # ours, when the order is known
    broker_order_id: str | None = None
    symbol: str
    exchange: str = "NSE"
    side: Side
    quantity: int
    price: float
    timestamp: datetime
    is_paper: bool


class Margins(BaseModel):
    """Equity segment funds."""

    segment: str = "equity"
    available_cash: float
    opening_balance: float | None = None
    used: float
    net: float
    is_paper: bool


class BrokerProfile(BaseModel):
    broker: str
    user_id: str | None = None  # masked for live brokers
    user_name: str | None = None
    exchanges: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    order_types: list[str] = Field(default_factory=list)
    is_paper: bool


class Instrument(BaseModel):
    symbol: str  # trading symbol
    exchange: str
    instrument_token: int | None = None
    name: str | None = None
    segment: str | None = None
    instrument_type: str | None = None
    tick_size: float = 0.05
    lot_size: int = 1


class BrokerHealth(BaseModel):
    broker: str
    state: ConnectionState
    healthy: bool  # usable for orders right now
    detail: str
    checked_at: datetime
    latency_ms: float | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    session_expires_at: datetime | None = None
