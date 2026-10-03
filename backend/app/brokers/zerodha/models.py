"""Kite-specific data models (instrument master rows, historical candles).

Broker-independent models live elsewhere: NormalizedTick in
app.market_data.schemas, orders / positions in app.execution.models.
"""

from __future__ import annotations

import csv
import gzip
import io
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

# Kite tradingsymbols of the indices this app uses, by app symbol.
INDEX_ALIASES: dict[str, str] = {
    "NIFTY50": "NIFTY 50",
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "INDIAVIX": "INDIA VIX",
}


class InstrumentRecord(BaseModel):
    """One row of the Kite instrument dump (GET /instruments[/:exchange])."""

    instrument_token: int = Field(gt=0)
    exchange_token: int | None = None
    tradingsymbol: str = Field(min_length=1, max_length=64)
    name: str | None = None
    last_price: float | None = None
    expiry: date | None = None
    strike: float | None = None
    tick_size: float = Field(default=0.05, ge=0)
    lot_size: int = Field(default=1, ge=0)
    instrument_type: str  # EQ, FUT, CE, PE (indices: EQ in segment INDICES)
    segment: str  # NSE, INDICES, NFO-FUT, NFO-OPT, BSE, ...
    exchange: str  # NSE, NFO, BSE, ...

    @property
    def is_index(self) -> bool:
        return self.segment == "INDICES"


class InstrumentParseResult(BaseModel):
    records: list[InstrumentRecord]
    total_rows: int
    invalid_rows: int
    invalid_examples: list[str] = Field(default_factory=list)


def parse_instrument_dump(
    body: bytes, *, exchanges: set[str] | None = None
) -> InstrumentParseResult:
    """Validate and parse the (possibly gzipped) CSV dump. Rows that fail
    validation are counted and skipped, never stored."""

    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    records: list[InstrumentRecord] = []
    seen: set[int] = set()
    total = invalid = 0
    examples: list[str] = []
    for row in csv.DictReader(io.StringIO(body.decode("utf-8"))):
        total += 1
        try:
            exchange = (row.get("exchange") or "").strip()
            if exchanges and exchange not in exchanges:
                continue
            expiry_raw = (row.get("expiry") or "").strip()
            record = InstrumentRecord(
                instrument_token=int(row["instrument_token"]),
                exchange_token=int(row["exchange_token"]) if row.get("exchange_token") else None,
                tradingsymbol=row["tradingsymbol"].strip(),
                name=(row.get("name") or "").strip() or None,
                last_price=float(row["last_price"]) if row.get("last_price") else None,
                expiry=date.fromisoformat(expiry_raw) if expiry_raw else None,
                strike=float(row["strike"]) if row.get("strike") else None,
                tick_size=float(row.get("tick_size") or 0.05),
                lot_size=int(float(row.get("lot_size") or 1)),
                instrument_type=(row.get("instrument_type") or "").strip(),
                segment=(row.get("segment") or "").strip(),
                exchange=exchange,
            )
            if not record.instrument_type or not record.segment or not record.exchange:
                raise ValueError("missing type / segment / exchange")
            if record.instrument_token in seen:
                raise ValueError("duplicate instrument_token")
        except (KeyError, ValueError, TypeError) as exc:
            invalid += 1
            if len(examples) < 5:
                examples.append(f"row {total}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        seen.add(record.instrument_token)
        records.append(record)
    return InstrumentParseResult(
        records=records, total_rows=total, invalid_rows=invalid, invalid_examples=examples
    )


class KiteInterval(str, Enum):
    """Historical candle intervals and the most days Kite returns per request
    (Zerodha's published limits; the stricter of the published values)."""

    MINUTE = "minute"
    MINUTE_3 = "3minute"
    MINUTE_5 = "5minute"
    MINUTE_10 = "10minute"
    MINUTE_15 = "15minute"
    MINUTE_30 = "30minute"
    MINUTE_60 = "60minute"
    DAY = "day"

    @property
    def max_days(self) -> int:
        return {
            "minute": 30,
            "3minute": 90,
            "5minute": 90,
            "10minute": 90,
            "15minute": 180,
            "30minute": 180,
            "60minute": 365,
            "day": 2000,
        }[self.value]

    @property
    def minutes(self) -> int:
        return {
            "minute": 1,
            "3minute": 3,
            "5minute": 5,
            "10minute": 10,
            "15minute": 15,
            "30minute": 30,
            "60minute": 60,
            "day": 375,
        }[self.value]


class HistoricalCandle(BaseModel):
    timestamp: datetime  # candle start, timezone-aware
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)
    oi: int | None = None
