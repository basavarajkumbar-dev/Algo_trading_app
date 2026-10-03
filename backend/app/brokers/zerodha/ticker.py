"""Kite Ticker: the WebSocket streaming feed (Phase 13).

Protocol (Kite Connect v3 WebSocket documentation, cross-checked against
the official pykiteconnect 5.2.2 `KiteTicker`):

    URL       wss://ws.kite.trade?api_key=...&access_token=...
    requests  {"a": "subscribe",   "v": [token, ...]}
              {"a": "unsubscribe", "v": [token, ...]}
              {"a": "mode",        "v": ["ltp"|"quote"|"full", [token, ...]]}
    binary    2-byte packet count, then per packet a 2-byte length + packet;
              all integers big-endian. Packet layouts by length:
                8    LTP              token, last price
                28   index quote      token, ltp, high, low, open, close, change
                32   index full       + exchange timestamp
                44   quote            token, ltp, last qty, avg price, volume,
                                      buy qty, sell qty, open, high, low, close
                184  full             + last trade time, OI, OI high, OI low,
                                      exchange timestamp, 10 depth entries
                                      (5 buy, 5 sell; qty, price, orders, pad)
              prices are paise (/100); CDS /1e7; BCD and NCO /1e4.
              a 1-byte binary message is a heartbeat.
    text      JSON {"type": "order"|"error"|"message", "data": ...}
    limits    3000 instruments per connection, 3 connections per API key.

Why not the SDK's `KiteTicker`: it runs Twisted / Autobahn in a background
thread, and Twisted's reactor cannot be restarted inside one process, so a
logout followed by a fresh login (every trading day) would need a process
restart. This client is a small asyncio implementation of the same
documented protocol on the `websockets` library, with the same reconnect
policy (exponential backoff capped at 60 s, at most 50 attempts).

The access token is put in the connection URL only; it is never logged
(error texts are scrubbed) and never leaves the backend.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import struct
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlencode

from app.brokers.zerodha.exceptions import KiteWebSocketError
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_TOKENS_PER_CONNECTION = 3000

# Exchange segment = instrument_token & 0xff.
SEGMENT_NSE = 1
SEGMENT_NFO = 2
SEGMENT_CDS = 3
SEGMENT_BSE = 4
SEGMENT_BFO = 5
SEGMENT_BCD = 6
SEGMENT_MCX = 7
SEGMENT_INDICES = 9
SEGMENT_NCO = 12


class TickMode(str, Enum):
    LTP = "ltp"
    QUOTE = "quote"
    FULL = "full"


class TickerState(str, Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"  # gave up (auth failure / too many attempts) or disconnect()


# --- binary protocol -----------------------------------------------------------


def _u32(data: bytes, start: int) -> int:
    return int(struct.unpack(">I", data[start : start + 4])[0])


def _u16(data: bytes, start: int) -> int:
    return int(struct.unpack(">H", data[start : start + 2])[0])


def _ts(value: int) -> datetime | None:
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def price_divisor(token: int) -> float:
    segment = token & 0xFF
    if segment == SEGMENT_CDS:
        return 10_000_000.0
    if segment in (SEGMENT_BCD, SEGMENT_NCO):
        return 10_000.0
    return 100.0


def split_packets(message: bytes) -> list[bytes]:
    """Split one binary frame into packets. Heartbeats (< 2 bytes) give []."""

    if len(message) < 2:
        return []
    count = _u16(message, 0)
    packets: list[bytes] = []
    pos = 2
    for _ in range(count):
        if pos + 2 > len(message):
            raise KiteWebSocketError("truncated tick frame (packet header)")
        length = _u16(message, pos)
        pos += 2
        if pos + length > len(message):
            raise KiteWebSocketError("truncated tick frame (packet body)")
        packets.append(message[pos : pos + length])
        pos += length
    return packets


def parse_packet(packet: bytes) -> dict[str, Any] | None:
    """One packet -> a raw tick dict (the SDK's key names). None for an
    unknown packet length (ignored, never guessed at)."""

    n = len(packet)
    if n < 8:
        return None
    token = _u32(packet, 0)
    div = price_divisor(token)
    tradable = (token & 0xFF) != SEGMENT_INDICES
    if n == 8:
        return {
            "mode": TickMode.LTP.value,
            "tradable": tradable,
            "instrument_token": token,
            "last_price": _u32(packet, 4) / div,
        }
    if n in (28, 32):
        d: dict[str, Any] = {
            "mode": (TickMode.QUOTE if n == 28 else TickMode.FULL).value,
            "tradable": tradable,
            "instrument_token": token,
            "last_price": _u32(packet, 4) / div,
            "ohlc": {
                "high": _u32(packet, 8) / div,
                "low": _u32(packet, 12) / div,
                "open": _u32(packet, 16) / div,
                "close": _u32(packet, 20) / div,
            },
        }
        if n == 32:
            d["exchange_timestamp"] = _ts(_u32(packet, 28))
        return d
    if n in (44, 184):
        d = {
            "mode": (TickMode.QUOTE if n == 44 else TickMode.FULL).value,
            "tradable": tradable,
            "instrument_token": token,
            "last_price": _u32(packet, 4) / div,
            "last_traded_quantity": _u32(packet, 8),
            "average_traded_price": _u32(packet, 12) / div,
            "volume_traded": _u32(packet, 16),
            "total_buy_quantity": _u32(packet, 20),
            "total_sell_quantity": _u32(packet, 24),
            "ohlc": {
                "open": _u32(packet, 28) / div,
                "high": _u32(packet, 32) / div,
                "low": _u32(packet, 36) / div,
                "close": _u32(packet, 40) / div,
            },
        }
        if n == 184:
            d["last_trade_time"] = _ts(_u32(packet, 44))
            d["oi"] = _u32(packet, 48)
            d["oi_day_high"] = _u32(packet, 52)
            d["oi_day_low"] = _u32(packet, 56)
            d["exchange_timestamp"] = _ts(_u32(packet, 60))
            depth: dict[str, list[dict[str, Any]]] = {"buy": [], "sell": []}
            for i, p in enumerate(range(64, 184, 12)):
                depth["sell" if i >= 5 else "buy"].append(
                    {
                        "quantity": _u32(packet, p),
                        "price": _u32(packet, p + 4) / div,
                        "orders": _u16(packet, p + 8),
                    }
                )
            d["depth"] = depth
        return d
    return None


def parse_binary(message: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for packet in split_packets(message):
        parsed = parse_packet(packet)
        if parsed is not None:
            out.append(parsed)
    return out


# Encoders, used by the tests' fake Kite WebSocket server.


def _p(price: float, token: int) -> int:
    return int(round(price * price_divisor(token)))


def encode_ltp(token: int, last_price: float) -> bytes:
    return struct.pack(">II", token, _p(last_price, token))


def encode_index(
    token: int,
    last_price: float,
    *,
    high: float,
    low: float,
    open_: float,
    close: float,
    exchange_timestamp: datetime | None = None,
) -> bytes:
    change = int(round((last_price - close) / close * 100 * 100)) if close else 0
    body = struct.pack(
        ">IIIIIIi",
        token,
        _p(last_price, token),
        _p(high, token),
        _p(low, token),
        _p(open_, token),
        _p(close, token),
        change,
    )
    if exchange_timestamp is not None:
        body += struct.pack(">I", int(exchange_timestamp.timestamp()))
    return body


def encode_quote(
    token: int,
    last_price: float,
    *,
    last_quantity: int = 1,
    average_price: float | None = None,
    volume: int = 0,
    buy_quantity: int = 0,
    sell_quantity: int = 0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    close: float | None = None,
    full: bool = False,
    last_trade_time: datetime | None = None,
    exchange_timestamp: datetime | None = None,
    oi: int = 0,
    depth: tuple[list[tuple[int, float, int]], list[tuple[int, float, int]]] | None = None,
) -> bytes:
    lp = last_price
    body = struct.pack(
        ">IIIIIIIIIII",
        token,
        _p(lp, token),
        last_quantity,
        _p(average_price or lp, token),
        volume,
        buy_quantity,
        sell_quantity,
        _p(open_ or lp, token),
        _p(high or lp, token),
        _p(low or lp, token),
        _p(close or lp, token),
    )
    if not full:
        return body
    body += struct.pack(
        ">IIIII",
        int(last_trade_time.timestamp()) if last_trade_time else 0,
        oi,
        oi,
        oi,
        int(exchange_timestamp.timestamp()) if exchange_timestamp else 0,
    )
    buys, sells = depth or ([], [])
    for side in (buys, sells):
        levels = list(side)[:5] + [(0, 0.0, 0)] * (5 - min(len(side), 5))
        for qty, price, orders in levels:
            body += struct.pack(">IIHxx", qty, _p(price, token), orders)
    return body


def encode_frame(packets: Iterable[bytes]) -> bytes:
    items = list(packets)
    out = struct.pack(">H", len(items))
    for packet in items:
        out += struct.pack(">H", len(packet)) + packet
    return out


# --- WebSocket client ------------------------------------------------------------

Callback = Callable[..., Any]
Connector = Callable[[str], Awaitable[Any]]


async def _default_connector(url: str) -> Any:
    from websockets.asyncio.client import connect

    # Kite sends a 1-byte heartbeat every second; protocol pings are kept as
    # a second liveness check. No compression: the feed is binary.
    return await connect(
        url, open_timeout=15, ping_interval=20, ping_timeout=20, max_size=2**22, compression=None
    )


def _is_auth_failure(exc: BaseException) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return True
    return isinstance(exc, KiteWebSocketError) and "auth" in str(exc).lower()


class KiteTickerClient:
    """Asyncio Kite WebSocket client.

    Callbacks (sync or async, all optional, set as attributes):
      on_connect()                       every successful (re)connection
      on_ticks(ticks, received_at)       list of raw tick dicts
      on_error(message)                  scrubbed text
      on_close(code, reason)
      on_reconnect(attempt)              before each reconnection attempt
      on_noreconnect(reason)             gave up; no further attempts
      on_text(payload)                   parsed text messages (orders, errors)

    `subscribe` / `unsubscribe` / `set_mode` send immediately when connected
    and report whether they did; the SubscriptionManager owns the desired
    set and replays it after every reconnect.
    """

    def __init__(
        self,
        *,
        api_key: str,
        token_provider: Callable[[], str | None],
        url: str = "wss://ws.kite.trade",
        connector: Connector | None = None,
        heartbeat_timeout: float = 10.0,
        reconnect_max_delay: float = 60.0,
        reconnect_max_tries: int = 50,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock
        self._api_key = api_key
        self._token_provider = token_provider
        self._url = url
        self._connector = connector or _default_connector
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_max_delay = reconnect_max_delay
        self.reconnect_max_tries = reconnect_max_tries
        self._sleep = sleep
        self.state = TickerState.IDLE
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._connected_event = asyncio.Event()
        self.attempts = 0
        self.connections = 0
        self.reconnects = 0
        self.frames = 0
        self.heartbeats = 0
        self.parse_errors = 0
        self.last_message_at: float | None = None  # monotonic
        self.connected_at: datetime | None = None
        self.last_error: str | None = None
        self.stop_reason: str | None = None
        self.on_connect: Callback | None = None
        self.on_ticks: Callback | None = None
        self.on_error: Callback | None = None
        self.on_close: Callback | None = None
        self.on_reconnect: Callback | None = None
        self.on_noreconnect: Callback | None = None
        self.on_text: Callback | None = None

    def __repr__(self) -> str:
        return f"KiteTickerClient(state={self.state.value})"

    @property
    def connected(self) -> bool:
        return self.state is TickerState.CONNECTED

    def _scrub(self, text: str) -> str:
        token = self._token_provider()
        if token:
            text = text.replace(token, "***")
        return text[:300]

    async def _fire(self, callback: Callback | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - a callback bug must not kill the feed
            logger.exception("Kite ticker callback failed")

    # --- lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Start the connection loop (returns at once; see wait_connected)."""

        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self.stop_reason = None
        self.attempts = 0
        self._task = asyncio.create_task(self._run(), name="kite-ticker")

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def disconnect(self) -> None:
        self._stopping = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._connected_event.clear()
        if self.state is not TickerState.STOPPED or self.stop_reason is None:
            self.state = TickerState.STOPPED
            self.stop_reason = self.stop_reason or "disconnect requested"
        logger.info("WEBSOCKET_DISCONNECTED reason=%s", self.stop_reason)

    def _connection_url(self) -> str:
        token = self._token_provider()
        if not token:
            raise KiteWebSocketError("no access token: auth required")
        return f"{self._url}?{urlencode({'api_key': self._api_key, 'access_token': token})}"

    async def _run(self) -> None:
        while not self._stopping:
            self.state = (
                TickerState.CONNECTING if self.connections == 0 else TickerState.RECONNECTING
            )
            try:
                ws = await self._connector(self._connection_url())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if await self._handle_failure(exc, during="connect"):
                    return
                continue
            self._ws = ws
            self.attempts = 0
            self.connections += 1
            if self.connections > 1:
                self.reconnects += 1
            self.state = TickerState.CONNECTED
            self.connected_at = self._clock()
            self.last_message_at = time.monotonic()
            self._connected_event.set()
            logger.info("WEBSOCKET_CONNECTED connection=%d", self.connections)
            await self._fire(self.on_connect)
            try:
                await self._read_loop(ws)
                failure: BaseException = KiteWebSocketError("server closed the connection")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                failure = exc
            finally:
                self._connected_event.clear()
                self._ws = None
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
            if self._stopping:
                return
            code = getattr(getattr(failure, "rcvd", None), "code", None)
            reason = getattr(getattr(failure, "rcvd", None), "reason", "") or str(failure)
            logger.warning("WEBSOCKET_DISCONNECTED code=%s reason=%s", code, self._scrub(reason))
            await self._fire(self.on_close, code, self._scrub(reason))
            if await self._handle_failure(failure, during="read"):
                return

    async def _handle_failure(self, exc: BaseException, *, during: str) -> bool:
        """Record a failure and back off. True when the loop must stop."""

        message = self._scrub(f"{type(exc).__name__}: {exc}")
        self.last_error = message
        await self._fire(self.on_error, message)
        if self._stopping:
            return True
        if _is_auth_failure(exc):
            return await self._give_up(f"auth failure during {during}")
        self.attempts += 1
        if self.attempts > self.reconnect_max_tries:
            return await self._give_up("too many reconnection attempts")
        delay = min(2 ** (self.attempts - 1), self.reconnect_max_delay)
        self.state = TickerState.RECONNECTING
        logger.warning(
            "BROKER_ERROR websocket %s failed (%s); reconnect attempt %d in %.0fs",
            during,
            message,
            self.attempts,
            delay,
        )
        await self._fire(self.on_reconnect, self.attempts)
        await self._sleep(delay)
        return self._stopping

    async def _give_up(self, reason: str) -> bool:
        self.state = TickerState.STOPPED
        self.stop_reason = reason
        logger.warning("WEBSOCKET_DISCONNECTED no reconnect: %s", reason)
        await self._fire(self.on_noreconnect, reason)
        return True

    async def _read_loop(self, ws: Any) -> None:
        while not self._stopping:
            try:
                message = await asyncio.wait_for(ws.recv(), self.heartbeat_timeout)
            except TimeoutError as exc:
                raise KiteWebSocketError(
                    f"no data or heartbeat for {self.heartbeat_timeout:.0f}s"
                ) from exc
            self.last_message_at = time.monotonic()
            if isinstance(message, bytes | bytearray):
                data = bytes(message)
                if len(data) < 2:
                    self.heartbeats += 1
                    continue
                received_at = self._clock()
                self.frames += 1
                try:
                    ticks = parse_binary(data)
                except (KiteWebSocketError, struct.error) as exc:
                    self.parse_errors += 1
                    await self._fire(self.on_error, f"bad tick frame: {exc}")
                    continue
                if ticks:
                    await self._fire(self.on_ticks, ticks, received_at)
            else:
                await self._handle_text(str(message))

    async def _handle_text(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if data.get("type") == "error":
            text = self._scrub(str(data.get("data")))
            self.last_error = text
            await self._fire(self.on_error, text)
        await self._fire(self.on_text, data)

    # --- requests --------------------------------------------------------------

    async def _send(self, payload: dict[str, Any]) -> bool:
        ws = self._ws
        if ws is None or self.state is not TickerState.CONNECTED:
            return False
        try:
            await ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001 - the read loop will reconnect
            await self._fire(self.on_error, self._scrub(f"send failed: {exc}"))
            return False
        return True

    async def subscribe(self, tokens: list[int]) -> bool:
        return bool(tokens) and await self._send({"a": "subscribe", "v": list(tokens)})

    async def unsubscribe(self, tokens: list[int]) -> bool:
        return bool(tokens) and await self._send({"a": "unsubscribe", "v": list(tokens)})

    async def set_mode(self, mode: TickMode | str, tokens: list[int]) -> bool:
        value = mode.value if isinstance(mode, TickMode) else TickMode(mode).value
        return bool(tokens) and await self._send({"a": "mode", "v": [value, list(tokens)]})

    def status(self) -> dict[str, Any]:
        age = None if self.last_message_at is None else time.monotonic() - self.last_message_at
        return {
            "state": self.state.value,
            "connections": self.connections,
            "reconnects": self.reconnects,
            "attempts": self.attempts,
            "frames": self.frames,
            "heartbeats": self.heartbeats,
            "parse_errors": self.parse_errors,
            "last_message_age_seconds": None if age is None else round(age, 2),
            "connected_at": self.connected_at,
            "last_error": self.last_error,
            "stop_reason": self.stop_reason,
        }


# --- subscriptions ----------------------------------------------------------------


class SubscriptionManager:
    """The desired set of subscriptions, by instrument token, with a mode per
    token. Never sends a token twice; replays everything after a reconnect."""

    def __init__(
        self,
        ticker: KiteTickerClient,
        *,
        default_mode: TickMode = TickMode.QUOTE,
        max_tokens: int = MAX_TOKENS_PER_CONNECTION,
    ) -> None:
        self.ticker = ticker
        self.default_mode = default_mode
        self.max_tokens = max_tokens
        self._active: dict[int, TickMode] = {}
        self.updates = 0

    def get_active_subscriptions(self) -> dict[int, str]:
        return {t: m.value for t, m in self._active.items()}

    def __len__(self) -> int:
        return len(self._active)

    async def _send(self, tokens: list[int], mode: TickMode) -> None:
        if tokens and await self.ticker.subscribe(tokens):
            await self.ticker.set_mode(mode, tokens)

    async def subscribe_instrument(self, token: int, mode: TickMode | None = None) -> bool:
        return bool(await self.subscribe_many([token], mode))

    async def subscribe_many(
        self, tokens: Iterable[int], mode: TickMode | None = None
    ) -> list[int]:
        mode = mode or self.default_mode
        new: list[int] = []
        remode: list[int] = []
        for token in dict.fromkeys(tokens):
            if token in self._active:
                if self._active[token] is not mode:
                    self._active[token] = mode
                    remode.append(token)
                continue
            if len(self._active) >= self.max_tokens:
                raise KiteWebSocketError(
                    f"subscription limit reached ({self.max_tokens} instruments per connection)"
                )
            self._active[token] = mode
            new.append(token)
        await self._send(new, mode)
        if remode:
            await self.ticker.set_mode(mode, remode)
        if new or remode:
            self.updates += 1
            logger.info(
                "SUBSCRIPTION_UPDATED added=%d mode=%s total=%d", len(new), mode.value, len(self)
            )
        return new

    async def unsubscribe_instrument(self, token: int) -> bool:
        return bool(await self.unsubscribe_many([token]))

    async def unsubscribe_many(self, tokens: Iterable[int]) -> list[int]:
        gone = [t for t in dict.fromkeys(tokens) if self._active.pop(t, None) is not None]
        if gone:
            await self.ticker.unsubscribe(gone)
            self.updates += 1
            logger.info("SUBSCRIPTION_UPDATED removed=%d total=%d", len(gone), len(self))
        return gone

    async def update_subscriptions(self, desired: dict[int, TickMode]) -> dict[str, list[int]]:
        """Make the active set equal `desired` (token -> mode)."""

        removed = await self.unsubscribe_many([t for t in self._active if t not in desired])
        added: list[int] = []
        by_mode: dict[TickMode, list[int]] = {}
        for token, mode in desired.items():
            by_mode.setdefault(mode, []).append(token)
        for mode, tokens in by_mode.items():
            added.extend(await self.subscribe_many(tokens, mode))
        return {"added": added, "removed": removed}

    async def recover_subscriptions_after_reconnect(self) -> int:
        """Re-send every desired subscription on a fresh connection."""

        by_mode: dict[TickMode, list[int]] = {}
        for token, mode in self._active.items():
            by_mode.setdefault(mode, []).append(token)
        for mode, tokens in by_mode.items():
            await self._send(tokens, mode)
        return len(self._active)

    def clear(self) -> None:
        self._active.clear()
