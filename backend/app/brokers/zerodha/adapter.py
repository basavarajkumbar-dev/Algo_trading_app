"""ZerodhaBrokerAdapter: Kite Connect v3 behind the BrokerAdapter interface.

Safe by default:

  * Not configured (no ZERODHA_API_KEY / ZERODHA_API_SECRET): every call
    reports NOT_CONFIGURED; the app keeps running in PAPER mode.
  * Configured: connect() verifies the session with GET /user/profile.
    Reads (profile, balance, positions, orders, instruments) then work.
  * Order operations (place / modify / cancel) are refused unless live
    orders were explicitly armed (`arm_live_orders`), which only the
    execution gate does once every live-trading condition in
    PROJECT_CONTRACT.md section 9 holds. Nothing in this phase arms it.

Order safety:

  * Duplicate protection: every order needs a client_order_id; its
    deterministic tag (mapping.order_tag) is sent as the Kite `tag`. A
    client_order_id seen before is rejected locally, and the tag lets a
    lost order be found in the broker's order book.
  * Unknown outcome: if a placement request may have reached Kite but no
    answer came back (timeout, reset, 5xx), the order is returned
    SUBMITTED with needs_reconciliation=True and is never re-sent. The
    next get_orders() adopts the broker's order with the same tag.
  * Market and SL-M orders carry market_protection (-1 = Zerodha's
    automatic protection by default; 0 is refused, as Zerodha does).
  * At most 25 modifications per order (Kite's limit) are attempted.

Connection states: NOT_CONFIGURED, AUTH_REQUIRED (no / expired / rejected
token; tokens expire at 06:00 IST the next day), CONNECTING, CONNECTED,
DEGRADED (recent failures), DISCONNECTED (3+ consecutive failures).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from app.brokers.zerodha import mapping
from app.brokers.zerodha.exceptions import KiteNetworkError, KiteRejectedError
from app.brokers.zerodha.kite_client import KiteClient
from app.core.logging import get_logger
from app.execution.broker import (
    BrokerAdapter,
    BrokerAuthError,
    BrokerError,
    BrokerNotConfiguredError,
    BrokerRateLimitError,
    BrokerUnavailableError,
    LiveOrdersDisabledError,
)
from app.execution.models import (
    Balance,
    BrokerHealth,
    BrokerProfile,
    ConnectionState,
    Instrument,
    Margins,
    Order,
    OrderModification,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Trade,
)
from app.market_data.base import utcnow

logger = get_logger(__name__)

DISCONNECT_AFTER_FAILURES = 3


class ZerodhaBrokerAdapter(BrokerAdapter):
    name = "zerodha"
    is_paper = False

    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        access_token: str | None = None,
        base_url: str = "https://api.kite.trade",
        login_url: str = "https://kite.zerodha.com/connect/login",
        market_protection: float = -1,
        orders_per_second: int = 8,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Any = None,
        client: KiteClient | None = None,
    ) -> None:
        """`client`: the app-wide KiteClient owned by KiteAuthService (Phase 13).
        Without one the adapter builds its own (tests, scripts)."""

        if market_protection == 0:
            raise ValueError("market_protection cannot be 0 (Zerodha rejects it)")
        self._api_key = api_key or None
        self._api_secret = api_secret or None
        self._login_url = login_url
        self.market_protection = market_protection
        self._clock = clock
        self._client: KiteClient | None = client
        if client is not None:
            access_token = access_token or client.access_token_for_ticker()
        elif self.configured:
            assert self._api_key is not None
            kwargs: dict[str, Any] = {}
            if sleep is not None:
                kwargs["sleep"] = sleep
            self._client = KiteClient(
                api_key=self._api_key,
                access_token=access_token or None,
                base_url=base_url,
                timeout=timeout,
                orders_per_second=orders_per_second,
                transport=transport,
                # this private client is gated by the adapter's own arming
                orders_allowed=True,
                **kwargs,
            )
        self._state = (
            ConnectionState.DISCONNECTED if self.configured else ConnectionState.NOT_CONFIGURED
        )
        if self.configured and not access_token:
            self._state = ConnectionState.AUTH_REQUIRED
        self._session_expires_at: datetime | None = (
            mapping.session_expiry(clock()) if access_token else None
        )
        self._live_orders_armed = False
        self._manual_disconnect = False
        self._failures = 0
        self._last_success: datetime | None = None
        self._last_error: str | None = None
        self._latency_ms: float | None = None
        # local order registry: our uuid <-> Kite order id / tag
        self._orders: dict[uuid.UUID, Order] = {}
        self._by_broker_id: dict[str, uuid.UUID] = {}
        self._by_tag: dict[str, uuid.UUID] = {}

    def __repr__(self) -> str:  # never include secrets
        return f"ZerodhaBrokerAdapter(configured={self.configured}, state={self._state.value})"

    # --- configuration / session ------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    @property
    def live_orders_armed(self) -> bool:
        return self._live_orders_armed

    def arm_live_orders(self) -> None:
        """Allow order operations. Called only by the execution gate."""

        self._live_orders_armed = True

    def disarm_live_orders(self) -> None:
        self._live_orders_armed = False

    @property
    def session_expires_at(self) -> datetime | None:
        return self._session_expires_at

    def attach_session(self, expires_at: datetime | None) -> None:
        """KiteAuthService logged in (the shared client now has a token)."""

        self._session_expires_at = expires_at
        self._manual_disconnect = False
        if self.configured and self._state is ConnectionState.AUTH_REQUIRED:
            self._state = ConnectionState.DISCONNECTED

    def detach_session(self) -> None:
        """Logged out or the token expired."""

        self.disarm_live_orders()
        self._session_expires_at = None
        if self.configured:
            self._state = ConnectionState.AUTH_REQUIRED

    def login_url(self) -> str:
        if not self.configured:
            raise BrokerNotConfiguredError("Zerodha API key/secret are not configured")
        return f"{self._login_url}?v=3&api_key={self._api_key}"

    async def generate_session(self, request_token: str) -> BrokerProfile:
        """Exchange the request_token from the login redirect for an access
        token (kept in memory only, never returned or logged), then connect."""

        client = self._require_client()
        assert self._api_key is not None and self._api_secret is not None
        checksum = mapping.session_checksum(self._api_key, request_token, self._api_secret)
        data = await client.create_session(request_token, checksum)
        token = data.get("access_token")
        if not token:
            raise BrokerAuthError("Zerodha returned no access token")
        client.set_access_token(str(token))
        login = mapping._dt(data.get("login_time")) or self._clock()
        self._session_expires_at = mapping.session_expiry(login)
        await self.connect()
        return mapping.profile_from_kite(data)

    def _require_client(self) -> KiteClient:
        if self._client is None:
            raise BrokerNotConfiguredError("Zerodha API key/secret are not configured")
        return self._client

    def _session_expired(self) -> bool:
        return self._session_expires_at is not None and self._clock() >= self._session_expires_at

    # --- call bookkeeping ----------------------------------------------------------

    async def _call(self, op: str, fn: Callable[[KiteClient], Any]) -> Any:
        client = self._require_client()
        if self._manual_disconnect:
            raise BrokerUnavailableError("Zerodha disconnected by request: connect first")
        if not client.has_token or self._session_expired():
            self._state = ConnectionState.AUTH_REQUIRED
            raise BrokerAuthError("Zerodha session missing or expired: log in again")
        started = time.perf_counter()
        try:
            result = await fn(client)
        except BrokerAuthError as exc:
            self._state = ConnectionState.AUTH_REQUIRED
            self._last_error = f"{op}: {exc}"
            self.disarm_live_orders()
            raise
        except BrokerUnavailableError as exc:
            self._record_failure(op, exc)
            raise
        except (KiteRejectedError, BrokerRateLimitError):
            self._record_success(started)  # the broker answered
            raise
        self._record_success(started)
        return result

    def _record_success(self, started: float) -> None:
        self._latency_ms = round((time.perf_counter() - started) * 1000, 1)
        self._failures = 0
        self._last_success = self._clock()
        if self._state in (
            ConnectionState.DEGRADED,
            ConnectionState.DISCONNECTED,
            ConnectionState.CONNECTING,
        ):
            self._state = ConnectionState.CONNECTED

    def _record_failure(self, op: str, exc: Exception) -> None:
        self._failures += 1
        self._last_error = f"{op}: {exc}"
        if self._state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED):
            self._state = (
                ConnectionState.DISCONNECTED
                if self._failures >= DISCONNECT_AFTER_FAILURES
                else ConnectionState.DEGRADED
            )
        if self._state is ConnectionState.DISCONNECTED:
            self.disarm_live_orders()
        logger.warning("Zerodha %s failed (%d in a row): %s", op, self._failures, exc)

    # --- connection --------------------------------------------------------------

    @property
    def connection_state(self) -> ConnectionState:
        if self._state is ConnectionState.CONNECTED and self._session_expired():
            return ConnectionState.AUTH_REQUIRED
        return self._state

    async def connect(self) -> ConnectionState:
        if not self.configured:
            self._state = ConnectionState.NOT_CONFIGURED
            return self._state
        client = self._require_client()
        self._manual_disconnect = False
        if not client.has_token or self._session_expired():
            self._state = ConnectionState.AUTH_REQUIRED
            return self._state
        self._state = ConnectionState.CONNECTING
        try:
            await self._call("connect", lambda c: c.profile())
        except BrokerAuthError:
            return self._state
        except BrokerError as exc:
            self._state = ConnectionState.DISCONNECTED
            self._last_error = f"connect: {exc}"
            return self._state
        self._state = ConnectionState.CONNECTED
        if self._session_expires_at is None:
            self._session_expires_at = mapping.session_expiry(self._clock())
        return self._state

    async def disconnect(self) -> None:
        """Stop using the session: no calls until connect() again. The token
        stays valid at Zerodha until it expires (06:00 IST)."""

        self.disarm_live_orders()
        if self.configured:
            self._manual_disconnect = True
            self._state = ConnectionState.DISCONNECTED

    async def aclose(self) -> None:
        """Release the HTTP client (application shutdown)."""

        await self.disconnect()
        if self._client is not None:
            await self._client.aclose()

    async def health_check(self) -> BrokerHealth:
        now = self._clock()
        if not self.configured:
            return BrokerHealth(
                broker=self.name,
                state=ConnectionState.NOT_CONFIGURED,
                healthy=False,
                detail="Zerodha not configured: running in PAPER mode (no live broker needed)",
                checked_at=now,
            )
        if self._client is None or self._manual_disconnect:
            return self._health(now, "Disconnected by request")
        if self.connection_state is ConnectionState.AUTH_REQUIRED:
            self._state = ConnectionState.AUTH_REQUIRED
            return self._health(now, "Login required: no valid access token")
        try:
            await self._call("health", lambda c: c.profile())
        except BrokerError:
            pass
        detail = {
            ConnectionState.CONNECTED: "Connected (read-only unless live orders are armed)",
            ConnectionState.DEGRADED: "Reachable with recent failures",
            ConnectionState.DISCONNECTED: "Unreachable",
            ConnectionState.AUTH_REQUIRED: "Login required: session rejected",
        }.get(self._state, self._state.value)
        return self._health(now, detail)

    def _health(self, now: datetime, detail: str) -> BrokerHealth:
        state = self.connection_state
        return BrokerHealth(
            broker=self.name,
            state=state,
            healthy=state is ConnectionState.CONNECTED,
            detail=detail,
            checked_at=now,
            latency_ms=self._latency_ms,
            last_success_at=self._last_success,
            last_error=self._last_error,
            consecutive_failures=self._failures,
            session_expires_at=self._session_expires_at,
        )

    # --- account -------------------------------------------------------------------

    async def get_profile(self) -> BrokerProfile:
        return mapping.profile_from_kite(await self._call("profile", lambda c: c.profile()))

    async def get_balance(self) -> Balance:
        return mapping.balance_from_kite(await self._call("margins", lambda c: c.margins_equity()))

    async def get_positions(self) -> list[Position]:
        data = await self._call("positions", lambda c: c.positions())
        return [mapping.position_from_kite(p) for p in data.get("net", [])]

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        body = await self._call("instruments", lambda c: c.instruments(exchange))
        return mapping.instruments_from_csv(body)

    # --- orders ----------------------------------------------------------------------

    def _adopt(self, data: dict[str, Any]) -> Order:
        """Map a Kite order onto our registry (by order id, then by tag)."""

        broker_id = str(data["order_id"])
        local_id = self._by_broker_id.get(broker_id)
        tag = data.get("tag")
        if local_id is None and tag and tag in self._by_tag:
            local_id = self._by_tag[tag]
            self._by_broker_id[broker_id] = local_id
        known = self._orders.get(local_id) if local_id else None
        order = mapping.order_from_kite(
            data,
            local_id=local_id,
            client_order_id=known.client_order_id if known else None,
            purpose_tag=known.tag if known else None,
            previous=known.status if known else None,
            now=self._clock(),
        )
        if known is not None:
            order.modifications = known.modifications
            order.placed_at = known.placed_at
            order.strategy_signal_id = known.strategy_signal_id
            order.correlation_id = known.correlation_id
            self._orders[order.id] = order
        return order

    async def get_orders(self) -> list[Order]:
        rows = await self._call("orders", lambda c: c.orders())
        return [self._adopt(r) for r in rows if r.get("order_id")]

    async def get_order_status(self, order_id: uuid.UUID) -> Order:
        known = self._orders.get(order_id)
        if known is None:
            raise BrokerError(f"unknown order {order_id}")
        if known.broker_order_id is None:
            # placement outcome unknown: look for its tag in the order book
            for order in await self.get_orders():
                if order.id == order_id:
                    return order.model_copy(deep=True)
            return known.model_copy(deep=True)
        broker_id = known.broker_order_id
        history = await self._call("order", lambda c: c.order_history(broker_id))
        if not history:
            return known.model_copy(deep=True)
        return self._adopt(history[-1]).model_copy(deep=True)

    async def get_trades(self) -> list[Trade]:
        rows = await self._call("trades", lambda c: c.trades())
        return [
            mapping.trade_from_kite(r, local_id=self._by_broker_id.get(str(r.get("order_id"))))
            for r in rows
            if r.get("trade_id")
        ]

    async def get_order_trades(self, order_id: uuid.UUID) -> list[Trade]:
        known = self._orders.get(order_id)
        if known is None or known.broker_order_id is None:
            return []
        broker_id = known.broker_order_id
        rows = await self._call("order_trades", lambda c: c.order_trades(broker_id))
        return [mapping.trade_from_kite(r, local_id=order_id) for r in rows if r.get("trade_id")]

    async def get_margins(self) -> Margins:
        return mapping.margins_from_kite(await self._call("margins", lambda c: c.margins_equity()))

    def _check_armed(self) -> None:
        if not self._live_orders_armed:
            raise LiveOrdersDisabledError(
                "Live orders are not armed: Zerodha is read-only (PAPER mode)"
            )

    async def place_order(self, request: OrderRequest) -> Order:
        now = self._clock()
        order = Order(
            client_order_id=request.client_order_id,
            tag=request.tag,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            trigger_price=request.trigger_price,
            status=OrderStatus.SUBMITTED,
            placed_at=now,
            updated_at=now,
            executable_after=now,
            exchange=request.exchange,
            product=request.product,
        )

        def reject(reason: str) -> Order:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = reason
            return order

        try:
            self._check_armed()
            self._require_client()
        except BrokerError as exc:
            return reject(str(exc))
        if not request.client_order_id:
            return reject("client_order_id is required for live orders (duplicate protection)")
        tag = mapping.order_tag(request.client_order_id)
        order.broker_tag = tag
        if tag in self._by_tag:
            return reject(f"duplicate client_order_id {request.client_order_id!r}")
        problem = _validate(request)
        if problem:
            return reject(problem)
        if request.market_protection == 0:
            return reject("market_protection 0 is not allowed (Zerodha rejects it)")
        if self.connection_state is not ConnectionState.CONNECTED:
            return reject(f"Zerodha is {self.connection_state.value}: order not sent")

        params = mapping.order_params(request, tag=tag, market_protection=self.market_protection)
        # registered before sending, so a lost response can still be matched
        self._orders[order.id] = order
        self._by_tag[tag] = order.id
        try:
            broker_id = await self._call("place", lambda c: c.place_order(mapping.VARIETY, params))
        except KiteNetworkError as exc:
            if not exc.maybe_processed:
                return reject(f"not sent: {exc}")
            order.needs_reconciliation = True
            order.status_message = f"outcome unknown, will reconcile: {exc}"
            logger.error(
                "Zerodha order %s outcome unknown (%s); it will be reconciled, not re-sent",
                request.client_order_id,
                exc,
            )
            return order.model_copy(deep=True)
        except (KiteRejectedError, BrokerRateLimitError, BrokerAuthError) as exc:
            return reject(str(exc))
        except BrokerError as exc:
            return reject(str(exc))

        order.broker_order_id = broker_id
        self._by_broker_id[broker_id] = order.id
        try:
            history = await self._call("order", lambda c: c.order_history(broker_id))
        except BrokerError:
            return order.model_copy(deep=True)  # placed; status follows on reconciliation
        if history:
            return self._adopt(history[-1]).model_copy(deep=True)
        return order.model_copy(deep=True)

    def _working(self, order_id: uuid.UUID) -> Order:
        self._check_armed()
        known = self._orders.get(order_id)
        if known is None:
            raise BrokerError(f"unknown order {order_id}")
        if known.broker_order_id is None:
            raise BrokerError("order not confirmed by Zerodha yet (reconciling)")
        if not known.status.is_working:
            raise BrokerError(f"order is {known.status.value}")
        return known

    async def modify_order(self, order_id: uuid.UUID, changes: OrderModification) -> Order:
        known = self._working(order_id)
        if known.modifications >= mapping.MAX_MODIFICATIONS:
            raise BrokerError(f"Zerodha allows {mapping.MAX_MODIFICATIONS} modifications per order")
        params: dict[str, Any] = {"order_type": mapping.ORDER_TYPE_TO_KITE[known.order_type]}
        if changes.quantity is not None:
            if changes.quantity <= known.filled_quantity:
                raise BrokerError("quantity must exceed the filled quantity")
            params["quantity"] = changes.quantity
        if changes.price is not None:
            if known.order_type is not OrderType.LIMIT:
                raise BrokerError("only LIMIT orders have a price")
            params["price"] = changes.price
        if changes.trigger_price is not None:
            if known.order_type is not OrderType.SL_M:
                raise BrokerError("only SL_M orders have a trigger price")
            params["trigger_price"] = changes.trigger_price
        if known.order_type in (OrderType.MARKET, OrderType.SL_M):
            params["market_protection"] = self.market_protection
        broker_id = known.broker_order_id
        assert broker_id is not None
        try:
            await self._call("modify", lambda c: c.modify_order(mapping.VARIETY, broker_id, params))
        except KiteRejectedError as exc:
            raise BrokerError(f"Zerodha refused the modification: {exc}") from exc
        known.modifications += 1
        return await self.get_order_status(order_id)

    async def cancel_order(self, order_id: uuid.UUID) -> Order:
        known = self._working(order_id)
        broker_id = known.broker_order_id
        assert broker_id is not None
        try:
            await self._call("cancel", lambda c: c.cancel_order(mapping.VARIETY, broker_id))
        except KiteRejectedError as exc:
            raise BrokerError(f"Zerodha refused the cancellation: {exc}") from exc
        return await self.get_order_status(order_id)


def _validate(request: OrderRequest) -> str | None:
    if request.quantity <= 0:
        return "quantity must be positive"
    if request.order_type is OrderType.LIMIT and not request.price:
        return "LIMIT order needs a price"
    if request.order_type is OrderType.SL_M and not request.trigger_price:
        return "SL_M order needs a trigger price"
    return None


def zerodha_from_settings(settings: Any, **overrides: Any) -> ZerodhaBrokerAdapter:
    secret = settings.zerodha_api_secret
    token = settings.zerodha_access_token
    kwargs: dict[str, Any] = {
        "api_key": settings.zerodha_api_key,
        "api_secret": secret.get_secret_value() if secret else None,
        "access_token": token.get_secret_value() if token else None,
        "base_url": settings.zerodha_api_base_url,
        "login_url": settings.zerodha_login_url,
        "market_protection": settings.zerodha_market_protection,
        "orders_per_second": settings.zerodha_orders_per_second,
        "timeout": settings.zerodha_timeout_seconds,
    }
    kwargs.update(overrides)
    return ZerodhaBrokerAdapter(**kwargs)
