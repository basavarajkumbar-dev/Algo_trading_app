"""Minimal async Kite Connect v3 HTTP client.

    base URL       https://api.kite.trade (ZERODHA_API_BASE_URL)
    headers        X-Kite-Version: 3
                   Authorization: token <api_key>:<access_token>
    requests       form-encoded; responses JSON {status, data} or
                   {status: "error", message, error_type}; /instruments is CSV

Errors map onto the broker-neutral exceptions:

    403 / TokenException                    BrokerAuthError
    429                                     BrokerRateLimitError (not processed)
    400 Input/Order/Margin/Holding/User/... KiteRejectedError (a BrokerError)
    5xx, NetworkException, DataException,
    transport errors and timeouts           KiteNetworkError (BrokerUnavailableError)

KiteNetworkError.maybe_processed tells whether the request may have been
acted on: False only when the connection could not be opened, so nothing
was sent. Reads (GET) are retried on network errors; writes (POST / PUT /
DELETE) never are: an order is never sent twice by this client.

Rate limits (Kite): 10 requests/second (orders: 10/second per account,
400/minute, 5000/day; quotes 1/second). Order requests are paced by a
client-side limiter below that cap.

Secrets: the access token and API secret are only placed in headers or the
session request body; they are never logged or included in exceptions.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import get_logger
from app.execution.broker import (
    BrokerAuthError,
    BrokerError,
    BrokerRateLimitError,
    BrokerUnavailableError,
)

logger = get_logger(__name__)

KITE_VERSION = "3"


class KiteRejectedError(BrokerError):
    def __init__(self, message: str, error_type: str | None, status_code: int) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


class KiteNetworkError(BrokerUnavailableError):
    def __init__(self, message: str, *, maybe_processed: bool) -> None:
        super().__init__(message)
        self.maybe_processed = maybe_processed


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
        self.capacity = float(rate)
        self.tokens = float(rate)
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


_REJECT_TYPES = {
    "InputException",
    "OrderException",
    "MarginException",
    "HoldingException",
    "UserException",
    "PermissionException",
}
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
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._sleep = sleep
        self._read_retries = read_retries
        self.order_limiter = RateLimiter(orders_per_second, sleep=sleep)
        self.request_limiter = RateLimiter(10, sleep=sleep)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            transport=transport,
            headers={"X-Kite-Version": KITE_VERSION, "User-Agent": "gainzalgo-pro-max"},
        )

    @property
    def has_token(self) -> bool:
        return bool(self._access_token)

    def set_access_token(self, token: str | None) -> None:
        self._access_token = token

    async def aclose(self) -> None:
        await self._http.aclose()

    def _auth(self) -> dict[str, str]:
        if not self._access_token:
            raise BrokerAuthError("no Zerodha access token: log in first")
        return {"Authorization": f"token {self._api_key}:{self._access_token}"}

    # --- core ----------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        is_order: bool = False,
        raw: bool = False,
    ) -> Any:
        read = method == "GET"
        attempts = 1 + (self._read_retries if read else 0)
        last: KiteNetworkError | None = None
        for attempt in range(attempts):
            if attempt:
                await self._sleep(0.5 * attempt)
            await (self.order_limiter if is_order else self.request_limiter).acquire()
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
                    return self._parse(method, path, response, raw=raw)
            if not read:
                break
            logger.warning("Kite %s %s failed (attempt %d): %s", method, path, attempt + 1, last)
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
                raise KiteNetworkError(
                    f"{method} {path}: unreadable response", maybe_processed=True
                ) from exc
            return body.get("data") if isinstance(body, dict) else body
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = str(body.get("message") or f"HTTP {status}")
        error_type = body.get("error_type")
        if status == 403 or error_type == "TokenException":
            raise BrokerAuthError(f"Zerodha session rejected: {message}")
        if status == 429:
            raise BrokerRateLimitError(f"Zerodha rate limit: {message}")
        if error_type in ("NetworkException", "DataException") or status >= 500:
            raise KiteNetworkError(f"{method} {path}: {message}", maybe_processed=True)
        raise KiteRejectedError(message, error_type, status)

    # --- endpoints -------------------------------------------------------------

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

    async def profile(self) -> dict[str, Any]:
        return dict(await self.request("GET", "/user/profile") or {})

    async def margins_equity(self) -> dict[str, Any]:
        return dict(await self.request("GET", "/user/margins/equity") or {})

    async def positions(self) -> dict[str, Any]:
        return dict(await self.request("GET", "/portfolio/positions") or {})

    async def orders(self) -> list[dict[str, Any]]:
        return list(await self.request("GET", "/orders") or [])

    async def order_history(self, broker_order_id: str) -> list[dict[str, Any]]:
        return list(await self.request("GET", f"/orders/{broker_order_id}") or [])

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

    async def instruments(self, exchange: str | None = None) -> bytes:
        path = f"/instruments/{exchange}" if exchange else "/instruments"
        return bytes(await self.request("GET", path, raw=True))
