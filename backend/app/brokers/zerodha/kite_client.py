"""Async Kite Connect v3 REST client (one shared instance per app).

    base URL       https://api.kite.trade (KITE_API_BASE_URL)
    headers        X-Kite-Version: 3
                   Authorization: token <api_key>:<access_token>
    requests       form-encoded; responses JSON {status, data} or
                   {status: "error", message, error_type}; /instruments is CSV

Why not the official `kiteconnect` SDK for REST: it is synchronous
(requests) and would need a thread per call inside this asyncio server;
this client covers the same documented endpoints natively async, with one
rate limiter and one error mapping shared by every caller. The endpoints,
parameters and payloads follow https://kite.trade/docs/connect/v3/.

Errors (app.brokers.zerodha.exceptions):

    403 / TokenException                    KiteAuthError (on_auth_error is called)
    429                                     KiteRateLimitError (not processed)
    400-class Input/Order/Margin/...        KiteRejectedError
    5xx, Network/DataException, transport   KiteNetworkError(maybe_processed)
    unreadable JSON                         KiteDataError

Reads (GET) are retried on network errors and 502/503/504; writes never
are, so an order is never sent twice by this client.

Rate limits (Kite): quotes 1 request/second, historical 3/second, orders
10/second per account (paced below that by ZERODHA_ORDERS_PER_SECOND),
everything else 10/second.

Order safety: `orders_allowed` is False unless EXECUTION_MODE=ZERODHA and
LIVE_TRADING_ENABLED=true; then any order request raises
KiteOrdersNotAllowedError before anything is sent.

Secrets: the access token, API secret and request token only travel in
headers or the session request body; they are never logged and never
appear in exception messages.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import httpx

from app.brokers.zerodha.exceptions import (
    KiteAuthError,
    KiteDataError,
    KiteNetworkError,
    KiteOrdersNotAllowedError,
    KiteRateLimitError,
    KiteRejectedError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

KITE_VERSION = "3"
QUOTE_MAX = 500  # instruments per /quote call
LTP_MAX = 1000  # instruments per /quote/ltp and /quote/ohlc call


class RateLimiter:
    """Token bucket: at most `rate` acquisitions per second, bursts of `rate`."""

    def __init__(
        self,
        rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rate = float(rate)
        self.capacity = float(max(rate, 1.0))
        self.tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
                self._last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await self._sleep((1 - self.tokens) / self.rate)


_RETRY_STATUS = {502, 503, 504}


class KiteClient:
    def __init__(
        self,
        *,
        api_key: str,
        access_token: str | None,
        base_url: str = "https://api.kite.trade",
        timeout: float = 10.0,
        orders_per_second: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        read_retries: int = 2,
        orders_allowed: bool = False,
        on_auth_error: Callable[[str], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._sleep = sleep
        self._read_retries = read_retries
        self.orders_allowed = orders_allowed
        self.on_auth_error = on_auth_error
        self.order_limiter = RateLimiter(orders_per_second, sleep=sleep)
        self.request_limiter = RateLimiter(10, sleep=sleep)
        self.quote_limiter = RateLimiter(1, sleep=sleep)
        self.historical_limiter = RateLimiter(3, sleep=sleep)
        self.last_success_at: float | None = None  # monotonic
        self.last_error: str | None = None
        self.requests_made = 0
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            transport=transport,
            headers={"X-Kite-Version": KITE_VERSION, "User-Agent": "gainzalgo-pro-max"},
        )

    def __repr__(self) -> str:  # never include the token
        return f"KiteClient(has_token={self.has_token}, orders_allowed={self.orders_allowed})"

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def has_token(self) -> bool:
        return bool(self._access_token)

    def set_access_token(self, token: str | None) -> None:
        self._access_token = token

    def access_token_for_ticker(self) -> str | None:
        """The ticker connection needs the token in its URL; nothing else may read it."""

        return self._access_token

    async def aclose(self) -> None:
        await self._http.aclose()

    def _auth(self) -> dict[str, str]:
        if not self._access_token:
            raise KiteAuthError("no Kite access token: log in first")
        return {"Authorization": f"token {self._api_key}:{self._access_token}"}

    # --- core ----------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: Any = None,
        auth: bool = True,
        is_order: bool = False,
        limiter: RateLimiter | None = None,
        raw: bool = False,
    ) -> Any:
        if is_order and not self.orders_allowed:
            raise KiteOrdersNotAllowedError(
                "this Kite client may not place, modify or cancel orders "
                "(EXECUTION_MODE=PAPER or LIVE_TRADING_ENABLED=false)"
            )
        read = method == "GET"
        attempts = 1 + (self._read_retries if read else 0)
        last: KiteNetworkError | None = None
        bucket = limiter or (self.order_limiter if is_order else self.request_limiter)
        for attempt in range(attempts):
            if attempt:
                await self._sleep(0.5 * attempt)
            await bucket.acquire()
            self.requests_made += 1
            try:
                response = await self._http.request(
                    method,
                    path,
                    data={k: v for k, v in (data or {}).items() if v is not None} or None,
                    params=params,
                    headers=self._auth() if auth else None,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = KiteNetworkError(
                    f"{method} {path}: cannot connect ({type(exc).__name__})",
                    maybe_processed=False,
                )
            except httpx.TransportError as exc:  # read timeout, reset, protocol error
                last = KiteNetworkError(
                    f"{method} {path}: {type(exc).__name__}", maybe_processed=True
                )
            else:
                if response.status_code in _RETRY_STATUS:
                    last = KiteNetworkError(
                        f"{method} {path}: HTTP {response.status_code} from Kite",
                        maybe_processed=response.status_code != 503,
                    )
                else:
                    result = self._parse(method, path, response, raw=raw)
                    self.last_success_at = time.monotonic()
                    self.last_error = None
                    return result
            self.last_error = str(last)
            if not read:
                break
            logger.warning(
                "BROKER_ERROR Kite %s %s failed (attempt %d): %s", method, path, attempt + 1, last
            )
        assert last is not None
        raise last

    def _parse(self, method: str, path: str, response: httpx.Response, *, raw: bool) -> Any:
        status = response.status_code
        if status < 400:
            if raw:
                return response.content
            try:
                body = response.json()
            except ValueError as exc:
                raise KiteDataError(f"{method} {path}: unreadable response") from exc
            if not isinstance(body, dict) or "data" not in body:
                raise KiteDataError(f"{method} {path}: unexpected response shape")
            return body.get("data")
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = str(body.get("message") or f"HTTP {status}")
        error_type = body.get("error_type")
        if status == 403 or error_type == "TokenException":
            if self.on_auth_error is not None:
                self.on_auth_error(message)
            raise KiteAuthError(f"Kite session rejected: {message}")
        if status == 429:
            raise KiteRateLimitError(f"Kite rate limit: {message}")
        if error_type in ("NetworkException", "DataException") or status >= 500:
            raise KiteNetworkError(f"{method} {path}: {message}", maybe_processed=True)
        raise KiteRejectedError(message, error_type, status)

    # --- session ---------------------------------------------------------------

    async def create_session(self, request_token: str, checksum: str) -> dict[str, Any]:
        data = await self.request(
            "POST",
            "/session/token",
            data={"api_key": self._api_key, "request_token": request_token, "checksum": checksum},
            auth=False,
        )
        return dict(data or {})

    async def invalidate_session(self) -> None:
        if not self._access_token:
            return
        await self.request(
            "DELETE",
            "/session/token",
            params={"api_key": self._api_key, "access_token": self._access_token},
            auth=False,
        )

    # --- account ----------------------------------------------------------------

    async def profile(self) -> dict[str, Any]:
        return dict(await self.request("GET", "/user/profile") or {})

    async def margins(self, segment: str | None = None) -> dict[str, Any]:
        path = f"/user/margins/{segment}" if segment else "/user/margins"
        return dict(await self.request("GET", path) or {})

    async def margins_equity(self) -> dict[str, Any]:
        return await self.margins("equity")

    async def positions(self) -> dict[str, Any]:
        return dict(await self.request("GET", "/portfolio/positions") or {})

    async def holdings(self) -> list[dict[str, Any]]:
        return list(await self.request("GET", "/portfolio/holdings") or [])

    # --- orders (reads) -----------------------------------------------------------

    async def orders(self) -> list[dict[str, Any]]:
        return list(await self.request("GET", "/orders") or [])

    async def order_history(self, broker_order_id: str) -> list[dict[str, Any]]:
        return list(await self.request("GET", f"/orders/{broker_order_id}") or [])

    async def trades(self) -> list[dict[str, Any]]:
        return list(await self.request("GET", "/trades") or [])

    async def order_trades(self, broker_order_id: str) -> list[dict[str, Any]]:
        return list(await self.request("GET", f"/orders/{broker_order_id}/trades") or [])

    # --- orders (writes; refused unless orders_allowed) ------------------------------

    async def place_order(self, variety: str, params: dict[str, Any]) -> str:
        data = await self.request("POST", f"/orders/{variety}", data=params, is_order=True)
        return str(data["order_id"])

    async def modify_order(self, variety: str, broker_order_id: str, params: dict[str, Any]) -> str:
        data = await self.request(
            "PUT", f"/orders/{variety}/{broker_order_id}", data=params, is_order=True
        )
        return str(data["order_id"])

    async def cancel_order(self, variety: str, broker_order_id: str) -> str:
        data = await self.request("DELETE", f"/orders/{variety}/{broker_order_id}", is_order=True)
        return str(data["order_id"])

    # --- market data --------------------------------------------------------------------

    async def instruments(self, exchange: str | None = None) -> bytes:
        path = f"/instruments/{exchange}" if exchange else "/instruments"
        return bytes(await self.request("GET", path, raw=True))

    async def _quote_call(
        self, path: str, instruments: Sequence[str], chunk: int
    ) -> dict[str, Any]:
        """`instruments` are "EXCHANGE:TRADINGSYMBOL" keys (or instrument tokens)."""

        out: dict[str, Any] = {}
        keys = list(instruments)
        for i in range(0, len(keys), chunk):
            params = [("i", k) for k in keys[i : i + chunk]]
            data = await self.request("GET", path, params=params, limiter=self.quote_limiter)
            if not isinstance(data, dict):
                raise KiteDataError(f"GET {path}: expected an object")
            out.update(data)
        return out

    async def quote(self, instruments: Sequence[str]) -> dict[str, Any]:
        return await self._quote_call("/quote", instruments, QUOTE_MAX)

    async def ltp(self, instruments: Sequence[str]) -> dict[str, Any]:
        return await self._quote_call("/quote/ltp", instruments, LTP_MAX)

    async def ohlc(self, instruments: Sequence[str]) -> dict[str, Any]:
        return await self._quote_call("/quote/ohlc", instruments, LTP_MAX)

    async def historical(
        self,
        instrument_token: int,
        interval: str,
        start: datetime,
        end: datetime,
        *,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[list[Any]]:
        """GET /instruments/historical/:token/:interval. `start` / `end` are IST
        wall-clock datetimes (the API takes "yyyy-mm-dd hh:mm:ss" in IST)."""

        fmt = "%Y-%m-%d %H:%M:%S"
        data = await self.request(
            "GET",
            f"/instruments/historical/{instrument_token}/{interval}",
            params={
                "from": start.strftime(fmt),
                "to": end.strftime(fmt),
                "continuous": 1 if continuous else 0,
                "oi": 1 if oi else 0,
            },
            limiter=self.historical_limiter,
        )
        if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
            raise KiteDataError("historical: expected {candles: [...]}")
        return list(data["candles"])
