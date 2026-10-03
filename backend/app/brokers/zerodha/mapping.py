"""Kite Connect v3 <-> broker-neutral models.

Verified against https://kite.trade/docs/connect/v3/ (orders, portfolio,
user, market-quotes, exceptions) and Zerodha's SEBI retail-algo notice of
25 March 2026 (static IP for order APIs, 10 orders/second, market
protection required for MARKET and SL-M orders from 1 April 2026).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.execution.models import (
    Balance,
    BrokerProfile,
    Instrument,
    Margins,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Product,
    Side,
    Trade,
)

IST = ZoneInfo("Asia/Kolkata")
VARIETY = "regular"
TAG_MAX = 20  # Kite: alphanumeric, max 20 characters
MAX_MODIFICATIONS = 25  # Kite: per order
# uuid namespace for orders the app did not place (seen in the order book)
_EXTERNAL_NS = uuid.UUID("6f1d3e0a-55e2-4c55-9a7b-0e7f2f3b9d11")

ORDER_TYPE_TO_KITE = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.SL_M: "SL-M",
}
ORDER_TYPE_FROM_KITE = {v: k for k, v in ORDER_TYPE_TO_KITE.items()}

# Kite status -> broker-neutral status. Transitional statuses keep an order
# working; which working status depends on the order (see map_status).
_FINAL = {
    "COMPLETE": OrderStatus.FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
}
_SUBMITTED = {
    "PUT ORDER REQ RECEIVED",
    "VALIDATION PENDING",
    "OPEN PENDING",
    "AMO REQ RECEIVED",
}
# a modification or cancellation is in flight: the order is still working
_IN_FLIGHT = {
    "MODIFY VALIDATION PENDING",
    "MODIFY PENDING",
    "MODIFIED",
    "CANCEL PENDING",
}
KNOWN_STATUSES = set(_FINAL) | _SUBMITTED | _IN_FLIGHT | {"OPEN", "TRIGGER PENDING"}


def map_status(
    kite_status: str | None,
    *,
    order_type: OrderType,
    filled_quantity: int,
    previous: OrderStatus | None = None,
) -> OrderStatus:
    """Kite order status -> OrderStatus.

    COMPLETE -> FILLED; REJECTED -> REJECTED; CANCELLED -> CANCELLED (the
    filled quantity is kept: a partly filled order can end cancelled);
    OPEN -> OPEN, or PARTIALLY_FILLED once something filled; TRIGGER
    PENDING -> TRIGGER_PENDING; request/validation statuses -> SUBMITTED;
    modify/cancel in flight -> the working status it had. An unknown
    status keeps the previous status (and the raw text is stored)."""

    status = (kite_status or "").strip().upper()
    if status in _FINAL:
        return _FINAL[status]
    if status in _SUBMITTED:
        return OrderStatus.SUBMITTED
    if status == "TRIGGER PENDING":
        return OrderStatus.TRIGGER_PENDING
    if status == "OPEN":
        return OrderStatus.PARTIALLY_FILLED if filled_quantity > 0 else OrderStatus.OPEN
    if status in _IN_FLIGHT:
        if previous is not None and previous.is_working and previous is not OrderStatus.SUBMITTED:
            return previous
        if filled_quantity > 0:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.TRIGGER_PENDING if order_type is OrderType.SL_M else OrderStatus.OPEN
    return previous if previous is not None else OrderStatus.SUBMITTED


def order_tag(client_order_id: str) -> str:
    """Deterministic idempotency tag the broker stores with the order:
    "GZ" + 18 hex characters of sha256(client_order_id). The same client
    order id always gives the same tag, so after a lost response or a
    restart the order can be found in the broker's order book."""

    return "GZ" + hashlib.sha256(client_order_id.encode()).hexdigest()[: TAG_MAX - 2]


def session_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """POST /session/token checksum: sha256(api_key + request_token + api_secret)."""

    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


def session_expiry(login_time: datetime) -> datetime:
    """Kite access tokens expire at 6 AM (IST) on the next day."""

    local = login_time.astimezone(IST)
    expiry = datetime.combine(local.date(), time(6, 0), tzinfo=IST)
    if local >= expiry:
        expiry += timedelta(days=1)
    return expiry.astimezone(UTC)


def mask(value: str | None, keep: int = 2) -> str | None:
    if not value:
        return value
    return value[:keep] + "*" * max(len(value) - keep, 2)


def order_params(request: OrderRequest, *, tag: str, market_protection: float) -> dict[str, Any]:
    """Form parameters for POST /orders/regular."""

    params: dict[str, Any] = {
        "tradingsymbol": request.symbol,
        "exchange": request.exchange,
        "transaction_type": request.side.value,
        "order_type": ORDER_TYPE_TO_KITE[request.order_type],
        "quantity": request.quantity,
        "product": request.product.value,
        "validity": "DAY",
        "tag": tag,
    }
    if request.order_type is OrderType.LIMIT:
        params["price"] = request.price
    if request.order_type is OrderType.SL_M:
        params["trigger_price"] = request.trigger_price
    if request.order_type in (OrderType.MARKET, OrderType.SL_M):
        protection = (
            request.market_protection
            if request.market_protection is not None
            else market_protection
        )
        params["market_protection"] = protection
    return params


def _dt(value: Any) -> datetime | None:
    """Kite timestamps are IST wall-clock strings ("2026-10-01 09:20:01")."""

    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(UTC)


def external_order_id(broker_order_id: str) -> uuid.UUID:
    return uuid.uuid5(_EXTERNAL_NS, broker_order_id)


def order_from_kite(
    data: dict[str, Any],
    *,
    local_id: uuid.UUID | None = None,
    client_order_id: str | None = None,
    purpose_tag: str | None = None,
    previous: OrderStatus | None = None,
    now: datetime,
) -> Order:
    order_type = ORDER_TYPE_FROM_KITE.get(str(data.get("order_type")), OrderType.LIMIT)
    filled = int(data.get("filled_quantity") or 0)
    quantity = int(data.get("quantity") or 0)
    status = map_status(
        data.get("status"), order_type=order_type, filled_quantity=filled, previous=previous
    )
    placed = _dt(data.get("order_timestamp")) or now
    updated = _dt(data.get("exchange_update_timestamp")) or _dt(data.get("exchange_timestamp"))
    message = data.get("status_message") or None
    product_raw = str(data.get("product") or "MIS")
    broker_id = str(data["order_id"])
    return Order(
        id=local_id or external_order_id(broker_id),
        client_order_id=client_order_id,
        tag=purpose_tag,
        symbol=str(data.get("tradingsymbol")),
        side=Side(str(data.get("transaction_type", "BUY")).upper()),
        order_type=order_type,
        quantity=quantity,
        price=float(data["price"]) if data.get("price") else None,
        trigger_price=float(data["trigger_price"]) if data.get("trigger_price") else None,
        status=status,
        filled_quantity=filled,
        average_price=float(data["average_price"]) if data.get("average_price") else None,
        triggered=bool(
            order_type is OrderType.SL_M
            and status not in (OrderStatus.TRIGGER_PENDING, OrderStatus.SUBMITTED)
            and filled > 0
        ),
        rejection_reason=message if status is OrderStatus.REJECTED else None,
        placed_at=placed,
        updated_at=updated or placed,
        executable_after=placed,
        exchange=str(data.get("exchange") or "NSE"),
        product=Product(product_raw) if product_raw in Product.__members__ else Product.MIS,
        broker_order_id=broker_id,
        exchange_order_id=data.get("exchange_order_id") or None,
        broker_status=str(data.get("status")) if data.get("status") else None,
        broker_tag=data.get("tag") or None,
        cancelled_quantity=int(data.get("cancelled_quantity") or 0),
        status_message=message,
    )


def position_from_kite(data: dict[str, Any]) -> Position:
    return Position(
        symbol=str(data["tradingsymbol"]),
        quantity=int(data.get("quantity") or 0),
        average_price=float(data.get("average_price") or 0.0),
        last_price=float(data["last_price"]) if data.get("last_price") is not None else None,
        realized_pnl=float(data.get("realised") or 0.0),
        unrealized_pnl=float(data.get("unrealised") or 0.0),
    )


def balance_from_kite(equity: dict[str, Any]) -> Balance:
    """GET /user/margins/equity. Charges are not reported by this endpoint."""

    available = equity.get("available") or {}
    utilised = equity.get("utilised") or {}
    cash = float(available.get("live_balance", available.get("cash", 0.0)) or 0.0)
    unrealised = float(utilised.get("m2m_unrealised") or 0.0)
    return Balance(
        starting_capital=float(available.get("opening_balance") or 0.0),
        cash=cash,
        used_margin=float(utilised.get("debits") or 0.0),
        available=float(equity.get("net") or 0.0),
        realized_pnl=float(utilised.get("m2m_realised") or 0.0),
        unrealized_pnl=unrealised,
        charges_paid=0.0,
        equity=round(cash + unrealised, 2),
    )


def trade_from_kite(data: dict[str, Any], *, local_id: uuid.UUID | None = None) -> Trade:
    """GET /trades or /orders/:id/trades row."""

    ts = _dt(data.get("fill_timestamp")) or _dt(data.get("exchange_timestamp"))
    return Trade(
        trade_id=str(data.get("trade_id")),
        order_id=local_id,
        broker_order_id=str(data["order_id"]) if data.get("order_id") else None,
        symbol=str(data.get("tradingsymbol")),
        exchange=str(data.get("exchange") or "NSE"),
        side=Side(str(data.get("transaction_type", "BUY")).upper()),
        quantity=int(data.get("quantity") or 0),
        price=float(data.get("average_price") or 0.0),
        timestamp=ts or datetime.now(UTC),
        is_paper=False,
    )


def margins_from_kite(equity: dict[str, Any]) -> Margins:
    available = equity.get("available") or {}
    utilised = equity.get("utilised") or {}
    return Margins(
        segment="equity",
        available_cash=float(available.get("live_balance", available.get("cash", 0.0)) or 0.0),
        opening_balance=(
            float(available["opening_balance"])
            if available.get("opening_balance") is not None
            else None
        ),
        used=float(utilised.get("debits") or 0.0),
        net=float(equity.get("net") or 0.0),
        is_paper=False,
    )


def profile_from_kite(data: dict[str, Any]) -> BrokerProfile:
    return BrokerProfile(
        broker="zerodha",
        user_id=mask(data.get("user_id")),
        user_name=data.get("user_shortname") or data.get("user_name"),
        exchanges=list(data.get("exchanges") or []),
        products=list(data.get("products") or []),
        order_types=list(data.get("order_types") or []),
        is_paper=False,
    )


def instruments_from_csv(body: bytes) -> list[Instrument]:
    """GET /instruments[/:exchange]: CSV (possibly gzipped) with columns
    instrument_token, exchange_token, tradingsymbol, name, last_price,
    expiry, strike, tick_size, lot_size, instrument_type, segment, exchange."""

    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    out: list[Instrument] = []
    for row in csv.DictReader(io.StringIO(body.decode("utf-8"))):
        try:
            out.append(
                Instrument(
                    symbol=row["tradingsymbol"],
                    exchange=row["exchange"],
                    instrument_token=int(row["instrument_token"]),
                    name=row.get("name") or None,
                    segment=row.get("segment") or None,
                    instrument_type=row.get("instrument_type") or None,
                    tick_size=float(row.get("tick_size") or 0.05),
                    lot_size=int(float(row.get("lot_size") or 1)),
                )
            )
        except (KeyError, ValueError):
            continue
    return out
