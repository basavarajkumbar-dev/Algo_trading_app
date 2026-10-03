"""Kite WebSocket: binary parser, the asyncio ticker client against a fake
Kite WebSocket server (connect, subscribe, modes, heartbeats, reconnect,
auth failure) and the SubscriptionManager. No network beyond 127.0.0.1."""

from __future__ import annotations

import asyncio
import struct
from datetime import UTC, datetime
from typing import Any

import pytest

from app.brokers.zerodha.exceptions import KiteWebSocketError
from app.brokers.zerodha.ticker import (
    KiteTickerClient,
    SubscriptionManager,
    TickerState,
    TickMode,
    encode_frame,
    encode_index,
    encode_ltp,
    encode_quote,
    parse_binary,
    parse_packet,
    split_packets,
)
from app.tests.fake_kite_ws import FakeKiteWS, wait_for

API_KEY = "testkey123"
TOKEN = "tok-very-secret-access-token"
NIFTY = 256265  # segment 9 (indices)
RELIANCE = 738561  # segment 1 (NSE)
CDS_TOKEN = (1234 << 8) | 3  # segment 3 (currency): price / 1e7

TS = datetime(2026, 10, 1, 4, 30, 15, tzinfo=UTC)


# --- parser -----------------------------------------------------------------------


def test_ltp_packet() -> None:
    (tick,) = parse_binary(encode_frame([encode_ltp(RELIANCE, 1402.35)]))
    assert tick == {
        "mode": "ltp",
        "tradable": True,
        "instrument_token": RELIANCE,
        "last_price": 1402.35,
    }


def test_index_quote_and_full_packets() -> None:
    quote = parse_packet(
        encode_index(NIFTY, 25010.5, high=25100, low=24900, open_=24950, close=24980)
    )
    assert quote is not None and quote["mode"] == "quote" and quote["tradable"] is False
    assert quote["ohlc"] == {"high": 25100.0, "low": 24900.0, "open": 24950.0, "close": 24980.0}
    assert "exchange_timestamp" not in quote
    full = parse_packet(
        encode_index(
            NIFTY, 25010.5, high=25100, low=24900, open_=24950, close=24980, exchange_timestamp=TS
        )
    )
    assert full is not None and full["mode"] == "full"
    assert full["exchange_timestamp"] == TS  # timezone-aware UTC, unlike the SDK's naive local


def test_quote_and_full_packets_with_depth() -> None:
    q = parse_packet(
        encode_quote(
            RELIANCE,
            1400.05,
            last_quantity=7,
            average_price=1399.5,
            volume=123456,
            buy_quantity=900,
            sell_quantity=800,
            open_=1390,
            high=1410,
            low=1385,
            close=1395,
        )
    )
    assert q is not None and len(encode_quote(RELIANCE, 1.0)) == 44
    assert q["mode"] == "quote" and q["last_price"] == 1400.05
    assert q["last_traded_quantity"] == 7 and q["volume_traded"] == 123456
    assert q["average_traded_price"] == 1399.5
    assert q["ohlc"] == {"open": 1390.0, "high": 1410.0, "low": 1385.0, "close": 1395.0}
    assert "exchange_timestamp" not in q  # QUOTE packets carry no time
    packet = encode_quote(
        RELIANCE,
        1400.0,
        full=True,
        last_trade_time=TS,
        exchange_timestamp=TS,
        oi=0,
        depth=([(10, 1399.95, 2), (20, 1399.9, 3)], [(5, 1400.05, 1)]),
    )
    assert len(packet) == 184
    f = parse_packet(packet)
    assert f is not None and f["mode"] == "full"
    assert f["exchange_timestamp"] == TS and f["last_trade_time"] == TS
    assert f["depth"]["buy"][0] == {"quantity": 10, "price": 1399.95, "orders": 2}
    assert f["depth"]["sell"][0] == {"quantity": 5, "price": 1400.05, "orders": 1}
    assert len(f["depth"]["buy"]) == 5 and len(f["depth"]["sell"]) == 5


def test_currency_divisor_and_unknown_lengths() -> None:
    raw = struct.pack(">II", CDS_TOKEN, 835_012_500)  # 83.50125 INR
    assert parse_packet(raw)["last_price"] == pytest.approx(83.50125)  # type: ignore[index]
    assert parse_packet(b"\x00" * 13) is None  # unknown layout: ignored, not guessed


def test_heartbeat_and_truncated_frames() -> None:
    assert split_packets(b"\x00") == []
    good = encode_frame([encode_ltp(RELIANCE, 1.0), encode_ltp(NIFTY, 2.0)])
    assert len(split_packets(good)) == 2
    with pytest.raises(KiteWebSocketError):
        split_packets(good[:-3])


# --- client + server ----------------------------------------------------------------------


@pytest.fixture
async def server() -> Any:
    s = FakeKiteWS(api_key=API_KEY, token=TOKEN)
    await s.start()
    yield s
    await s.stop()


def ticker(server: FakeKiteWS, token: str | None = TOKEN, **kw: Any) -> KiteTickerClient:
    async def fast_sleep(_: float) -> None:
        await asyncio.sleep(0.01)

    return KiteTickerClient(
        api_key=API_KEY,
        token_provider=lambda: token,
        url=server.url,
        sleep=fast_sleep,
        **kw,
    )


async def test_connect_subscribe_mode_and_ticks(server: FakeKiteWS) -> None:
    t = ticker(server)
    got: list[dict[str, Any]] = []
    t.on_ticks = lambda ticks, at: got.extend(ticks)
    manager = SubscriptionManager(t)
    await t.connect()
    assert await t.wait_connected(5)
    assert t.state is TickerState.CONNECTED
    await manager.subscribe_many([RELIANCE])
    await manager.subscribe_many([NIFTY], TickMode.FULL)
    await wait_for(lambda: server.subscribed() == {RELIANCE, NIFTY})
    assert server.modes() == {RELIANCE: "quote", NIFTY: "full"}
    await server.push([encode_quote(RELIANCE, 1400.0, volume=10), encode_ltp(NIFTY, 25000.0)])
    await wait_for(lambda: len(got) == 2)
    assert {g["instrument_token"] for g in got} == {RELIANCE, NIFTY}
    await wait_for(lambda: t.heartbeats > 0)  # 1-byte heartbeats are counted, not parsed
    await t.disconnect()
    assert t.state is TickerState.STOPPED


async def test_subscriptions_are_never_sent_twice(server: FakeKiteWS) -> None:
    t = ticker(server)
    manager = SubscriptionManager(t)
    await t.connect()
    await t.wait_connected(5)
    assert await manager.subscribe_many([RELIANCE, RELIANCE, NIFTY]) == [RELIANCE, NIFTY]
    assert await manager.subscribe_many([RELIANCE]) == []
    assert not await manager.subscribe_instrument(NIFTY)
    await asyncio.sleep(0.1)
    subs = [m for m in server.messages if m["a"] == "subscribe"]
    assert subs == [{"a": "subscribe", "v": [RELIANCE, NIFTY]}]
    diff = await manager.update_subscriptions({NIFTY: TickMode.FULL})
    assert diff == {"added": [], "removed": [RELIANCE]}
    await wait_for(lambda: server.subscribed() == {NIFTY} and server.modes()[NIFTY] == "full")
    assert manager.get_active_subscriptions() == {NIFTY: "full"}
    await t.disconnect()


async def test_subscription_limit_per_connection(server: FakeKiteWS) -> None:
    manager = SubscriptionManager(ticker(server), max_tokens=2)
    await manager.subscribe_many([1, 2])  # not connected: recorded, sent on connect
    with pytest.raises(KiteWebSocketError):
        await manager.subscribe_instrument(3)


async def test_reconnects_after_server_disconnect_and_restores_subscriptions(
    server: FakeKiteWS,
) -> None:
    t = ticker(server)
    manager = SubscriptionManager(t)
    connects: list[int] = []
    reconnects: list[int] = []

    async def on_connect() -> None:
        connects.append(1)
        await manager.recover_subscriptions_after_reconnect()

    t.on_connect = on_connect
    t.on_reconnect = reconnects.append
    await manager.subscribe_many([RELIANCE, NIFTY])  # before connecting
    await t.connect()
    await wait_for(lambda: server.subscribed() == {RELIANCE, NIFTY})
    server.clear()
    await server.drop_all()
    await wait_for(lambda: len(connects) == 2 and server.subscribed() == {RELIANCE, NIFTY})
    assert reconnects == [1] and t.reconnects == 1 and t.state is TickerState.CONNECTED
    await t.disconnect()


async def test_heartbeat_timeout_forces_a_reconnect(server: FakeKiteWS) -> None:
    t = ticker(server, heartbeat_timeout=0.5)
    await t.connect()
    await t.wait_connected(5)
    server.heartbeat = False  # the socket stays open but goes silent
    await wait_for(lambda: t.connections >= 2, timeout=5)
    assert "no data or heartbeat" in (t.last_error or "")
    await t.disconnect()


async def test_auth_failure_stops_without_retrying_and_never_shows_the_token(
    server: FakeKiteWS,
) -> None:
    server.valid_token = "some-other-token"
    t = ticker(server)
    gave_up: list[str] = []
    errors: list[str] = []
    t.on_noreconnect = gave_up.append
    t.on_error = errors.append
    await t.connect()
    await wait_for(lambda: bool(gave_up))
    assert t.state is TickerState.STOPPED and "auth" in gave_up[0]
    assert server.rejected_handshakes == 1  # not retried in a loop
    assert all(TOKEN not in e for e in errors) and TOKEN not in (t.last_error or "")
    await t.disconnect()


async def test_gives_up_after_max_attempts() -> None:
    async def refuse(url: str) -> Any:
        raise OSError("connection refused")

    async def no_sleep(_: float) -> None:
        return None

    t = KiteTickerClient(
        api_key=API_KEY,
        token_provider=lambda: TOKEN,
        connector=refuse,
        reconnect_max_tries=3,
        sleep=no_sleep,
    )
    gave_up: list[str] = []
    t.on_noreconnect = gave_up.append
    await t.connect()
    await wait_for(lambda: bool(gave_up))
    assert t.attempts == 4 and "too many" in gave_up[0]


async def test_bad_frames_and_text_errors_do_not_kill_the_socket(server: FakeKiteWS) -> None:
    t = ticker(server)
    got: list[dict[str, Any]] = []
    errors: list[str] = []
    t.on_ticks = lambda ticks, at: got.extend(ticks)
    t.on_error = errors.append
    await t.connect()
    await t.wait_connected(5)
    await server.push_raw(encode_frame([encode_ltp(RELIANCE, 1.0)])[:-2])  # truncated
    await server.push_raw('{"type": "error", "data": "Invalid subscription"}')
    await server.push([encode_ltp(RELIANCE, 1401.0)])
    await wait_for(lambda: len(got) == 1)
    assert t.parse_errors == 1 and any("Invalid subscription" in e for e in errors)
    assert t.state is TickerState.CONNECTED
    await t.disconnect()


async def test_no_token_means_no_connection_attempt_to_the_broker(server: FakeKiteWS) -> None:
    t = ticker(server, token=None)
    gave_up: list[str] = []
    t.on_noreconnect = gave_up.append
    await t.connect()
    await wait_for(lambda: bool(gave_up))
    assert server.connections == 0 and server.urls == []
