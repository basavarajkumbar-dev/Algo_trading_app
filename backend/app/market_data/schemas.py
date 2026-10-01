"""Core market data schemas.

Plain Pydantic models shared by the provider interface, validation,
candle building, the service layer, and the API. Kept independent of
any specific provider implementation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    M60 = "60m"

    @property
    def minutes(self) -> int:
        return {
            Timeframe.M1: 1,
            Timeframe.M3: 3,
            Timeframe.M5: 5,
            Timeframe.M15: 15,
            Timeframe.M30: 30,
            Timeframe.M60: 60,
        }[self]


#: Timeframes built by aggregating 1-minute candles, in ascending order.
AGGREGATE_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M3,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.M30,
    Timeframe.M60,
)


class SimulatedRegime(str, Enum):
    """Regimes the mock provider can simulate per symbol.

    Distinct from (and a precursor to) the real `MarketRegime` the
    regime-detection engine will compute from real candle data in
    docs/development-roadmap.md Phase 6. This enum describes what the
    *simulator* is doing to generate prices, not a detected market
    condition.
    """

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class Tick(BaseModel):
    symbol: str
    price: float
    volume: int = Field(ge=0)
    timestamp: datetime

    model_config = {"frozen": True}


class Candle(BaseModel):
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    is_closed: bool = False

    model_config = {"frozen": False}


class InstrumentInfo(BaseModel):
    symbol: str
    name: str
    exchange: str = "NSE"
    sector: str | None = None
    is_active: bool = True


class MarketDataProviderStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"
