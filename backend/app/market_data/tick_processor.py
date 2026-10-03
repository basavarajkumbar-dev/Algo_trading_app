"""TickProcessor: raw feed packet -> validated NormalizedTick (Phase 13).

    raw tick (a parsed broker packet: dict)
      -> validation      known instrument token, required fields, price,
                         quantities, timestamps
      -> normalization   NormalizedTick (symbol, exchange, prices, volumes)
      -> timestamps      exchange timestamp when the packet has one (FULL
                         mode, index packets), else the receive time (QUOTE
                         and LTP packets carry no time)
      -> deduplication   same instrument, price, volume and time as the last
                         accepted tick
      -> tick event      NormalizedTick (+ the pipeline Tick) or rejection

Rejected ticks never reach candles or strategies; the reason is counted and
logged, and the feed keeps running.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.market_data.base import utcnow
from app.market_data.schemas import DepthLevel, NormalizedTick

logger = get_logger(__name__)


class TickIssue:
    INVALID_TOKEN = "INVALID_TOKEN"  # missing or non-positive token
    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"  # not a subscribed / known instrument
    MISSING_FIELDS = "MISSING_FIELDS"
    INVALID_PRICE = "INVALID_PRICE"
    NEGATIVE_QUANTITY = "NEGATIVE_QUANTITY"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    STALE_TICK = "STALE_TICK"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DUPLICATE_TICK = "DUPLICATE_TICK"
    PRICE_JUMP = "PRICE_JUMP"  # implausible move from the last accepted price


@dataclass
class TickResult:
    tick: NormalizedTick | None
    issues: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.tick is not None


@dataclass
class _Last:
    price: float
    volume: int | None
    exchange_ts: datetime | None
    timestamp: datetime


@dataclass
class TickProcessor:
    """`resolve` maps an instrument token to (symbol, exchange) for the
    instruments currently subscribed; anything else is rejected."""

    resolve: Callable[[int], tuple[str, str] | None]
    clock: Callable[[], datetime] = utcnow
    stale_after: timedelta = field(default_factory=lambda: timedelta(seconds=30))
    future_tolerance: timedelta = field(default_factory=lambda: timedelta(seconds=5))
    max_move_percent: float = 20.0
    accepted: int = 0
    rejected: int = 0
    rejected_by_issue: Counter[str] = field(default_factory=Counter)
    _last: dict[int, _Last] = field(default_factory=dict)

    def reset(self, token: int | None = None) -> None:
        """Forget dedup / ordering state (e.g. after a reconnect or a new day)."""

        if token is None:
            self._last.clear()
        else:
            self._last.pop(token, None)

    def _reject(self, token: Any, *issues: str) -> TickResult:
        self.rejected += 1
        for issue in issues:
            self.rejected_by_issue[issue] += 1
        logger.debug("Tick %s rejected: %s", token, ",".join(issues))
        return TickResult(tick=None, issues=tuple(issues))

    def process(self, raw: dict[str, Any], received_at: datetime | None = None) -> TickResult:
        received = received_at or self.clock()
        token = raw.get("instrument_token")
        if not isinstance(token, int) or token <= 0:
            return self._reject(token, TickIssue.INVALID_TOKEN)
        known = self.resolve(token)
        if known is None:
            return self._reject(token, TickIssue.UNKNOWN_INSTRUMENT)
        symbol, exchange = known

        price = raw.get("last_price")
        if price is None:
            return self._reject(token, TickIssue.MISSING_FIELDS)
        if (
            not isinstance(price, int | float)
            or math.isnan(price)
            or math.isinf(price)
            or price <= 0
        ):
            return self._reject(token, TickIssue.INVALID_PRICE)

        quantities = {
            k: raw.get(k)
            for k in (
                "last_traded_quantity",
                "volume_traded",
                "total_buy_quantity",
                "total_sell_quantity",
                "oi",
            )
        }
        if any(isinstance(v, int | float) and v < 0 for v in quantities.values()):
            return self._reject(token, TickIssue.NEGATIVE_QUANTITY)

        exchange_ts = raw.get("exchange_timestamp")
        if exchange_ts is not None and not isinstance(exchange_ts, datetime):
            return self._reject(token, TickIssue.INVALID_TIMESTAMP)
        if isinstance(exchange_ts, datetime) and exchange_ts.tzinfo is None:
            exchange_ts = exchange_ts.replace(tzinfo=UTC)
        timestamp = exchange_ts or received
        if timestamp > received + self.future_tolerance:
            return self._reject(token, TickIssue.FUTURE_TIMESTAMP)
        if received - timestamp > self.stale_after:
            return self._reject(token, TickIssue.STALE_TICK)

        volume = raw.get("volume_traded")
        last = self._last.get(token)
        if last is not None:
            if (
                exchange_ts is not None
                and last.exchange_ts is not None
                and exchange_ts < last.exchange_ts
            ):
                return self._reject(token, TickIssue.OUT_OF_ORDER)
            if timestamp < last.timestamp:
                return self._reject(token, TickIssue.OUT_OF_ORDER)
            same_time = exchange_ts is not None and exchange_ts == last.exchange_ts
            if (
                price == last.price
                and volume == last.volume
                and (same_time or exchange_ts is None and volume is not None)
            ):
                return self._reject(token, TickIssue.DUPLICATE_TICK)
            if abs(price / last.price - 1) * 100 > self.max_move_percent:
                return self._reject(token, TickIssue.PRICE_JUMP)
        else:
            prev_close = (raw.get("ohlc") or {}).get("close")
            if prev_close and abs(price / prev_close - 1) * 100 > self.max_move_percent:
                return self._reject(token, TickIssue.PRICE_JUMP)

        delta = 0
        if volume is not None and last is not None and last.volume is not None:
            delta = volume - last.volume if volume >= last.volume else 0  # a reset (new day)

        ohlc = raw.get("ohlc") or {}
        depth = raw.get("depth") or {}
        buys = [DepthLevel(**d) for d in depth.get("buy", [])] if depth else None
        sells = [DepthLevel(**d) for d in depth.get("sell", [])] if depth else None
        ltt = raw.get("last_trade_time")
        tick = NormalizedTick(
            instrument_token=token,
            symbol=symbol,
            exchange=exchange,
            timestamp=timestamp,
            received_at=received,
            exchange_timestamp=exchange_ts,
            last_trade_time=(
                ltt.replace(tzinfo=UTC) if isinstance(ltt, datetime) and ltt.tzinfo is None else ltt
            ),
            last_price=float(price),
            last_quantity=raw.get("last_traded_quantity"),
            average_price=raw.get("average_traded_price"),
            volume=volume,
            volume_delta=delta,
            buy_quantity=raw.get("total_buy_quantity"),
            sell_quantity=raw.get("total_sell_quantity"),
            open=ohlc.get("open"),
            high=ohlc.get("high"),
            low=ohlc.get("low"),
            close=ohlc.get("close"),
            open_interest=raw.get("oi"),
            bid=buys[0].price if buys else None,
            ask=sells[0].price if sells else None,
            depth_buy=buys,
            depth_sell=sells,
            mode=str(raw.get("mode") or "quote"),
            tradable=bool(raw.get("tradable", True)),
        )
        self._last[token] = _Last(
            price=float(price), volume=volume, exchange_ts=exchange_ts, timestamp=timestamp
        )
        self.accepted += 1
        return TickResult(tick=tick)
