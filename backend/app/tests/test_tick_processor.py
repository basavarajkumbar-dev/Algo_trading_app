"""TickProcessor: validation, normalization, timestamps, deduplication."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.market_data.tick_processor import TickIssue, TickProcessor

NOW = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)  # 10:00 IST
KNOWN = {738561: ("RELIANCE", "NSE"), 256265: ("NIFTY50", "NSE")}


def proc(**kw: Any) -> TickProcessor:
    return TickProcessor(resolve=KNOWN.get, clock=lambda: NOW, **kw)


def quote(price: float = 1400.0, volume: int = 1000, **extra: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "mode": "quote",
        "tradable": True,
        "instrument_token": 738561,
        "last_price": price,
        "last_traded_quantity": 5,
        "average_traded_price": 1399.0,
        "volume_traded": volume,
        "total_buy_quantity": 100,
        "total_sell_quantity": 90,
        "ohlc": {"open": 1390.0, "high": 1410.0, "low": 1385.0, "close": 1395.0},
    }
    raw.update(extra)
    return raw


def test_a_quote_tick_is_normalized_with_the_receive_time() -> None:
    r = proc().process(quote(), NOW)
    assert r.accepted and r.issues == ()
    t = r.tick
    assert t is not None
    assert (t.symbol, t.exchange, t.instrument_token) == ("RELIANCE", "NSE", 738561)
    assert t.timestamp == NOW and t.exchange_timestamp is None  # QUOTE carries no time
    assert t.last_price == 1400.0 and t.volume == 1000 and t.volume_delta == 0
    assert (t.open, t.high, t.low, t.close) == (1390.0, 1410.0, 1385.0, 1395.0)
    assert t.to_tick().symbol == "RELIANCE" and t.to_tick().volume == 0


def test_volume_delta_comes_from_cumulative_volume() -> None:
    p = proc()
    p.process(quote(volume=1000), NOW)
    r = p.process(quote(price=1400.5, volume=1250), NOW + timedelta(seconds=1))
    assert r.tick is not None and r.tick.volume_delta == 250 and r.tick.to_tick().volume == 250
    reset = p.process(quote(price=1401.0, volume=10), NOW + timedelta(seconds=2))
    assert reset.tick is not None and reset.tick.volume_delta == 0  # a reset, not negative


def test_full_mode_uses_the_exchange_timestamp() -> None:
    exch = NOW - timedelta(seconds=1)
    r = proc().process(quote(exchange_timestamp=exch, mode="full"), NOW)
    assert r.tick is not None and r.tick.timestamp == exch and r.tick.received_at == NOW


def test_invalid_ticks_are_rejected_with_a_reason() -> None:
    p = proc()
    cases = {
        TickIssue.INVALID_TOKEN: {**quote(), "instrument_token": None},
        TickIssue.UNKNOWN_INSTRUMENT: {**quote(), "instrument_token": 999},
        TickIssue.MISSING_FIELDS: {k: v for k, v in quote().items() if k != "last_price"},
        TickIssue.INVALID_PRICE: quote(price=0.0),
        TickIssue.NEGATIVE_QUANTITY: quote(volume=-5),
        TickIssue.INVALID_TIMESTAMP: quote(exchange_timestamp="10:00"),
        TickIssue.FUTURE_TIMESTAMP: quote(exchange_timestamp=NOW + timedelta(minutes=5)),
        TickIssue.STALE_TICK: quote(exchange_timestamp=NOW - timedelta(minutes=5)),
    }
    for issue, raw in cases.items():
        r = p.process(raw, NOW)
        assert not r.accepted and r.issues == (issue,), (issue, r.issues)
    assert not p.process(quote(price=math.nan), NOW).accepted
    assert p.rejected == len(cases) + 1 and p.accepted == 0


def test_duplicates_and_out_of_order_ticks() -> None:
    p = proc()
    t1 = NOW - timedelta(seconds=2)
    assert p.process(quote(exchange_timestamp=t1), NOW).accepted
    dup = p.process(quote(exchange_timestamp=t1), NOW)
    assert dup.issues == (TickIssue.DUPLICATE_TICK,)
    old = p.process(quote(price=1401, exchange_timestamp=t1 - timedelta(seconds=1)), NOW)
    assert old.issues == (TickIssue.OUT_OF_ORDER,)
    # quote mode: same price + same cumulative volume = nothing new happened
    q = proc()
    assert q.process(quote(), NOW).accepted
    assert q.process(quote(), NOW + timedelta(seconds=1)).issues == (TickIssue.DUPLICATE_TICK,)
    assert q.process(quote(volume=1001), NOW + timedelta(seconds=1)).accepted


def test_implausible_price_jumps_are_rejected() -> None:
    p = proc(max_move_percent=10)
    assert p.process(quote(price=2000.0), NOW).issues == (TickIssue.PRICE_JUMP,)  # vs prev close
    assert p.process(quote(price=1400.0), NOW).accepted
    assert p.process(quote(price=1700.0, volume=1100), NOW).issues == (TickIssue.PRICE_JUMP,)


def test_reset_forgets_ordering_after_a_reconnect() -> None:
    p = proc()
    p.process(quote(exchange_timestamp=NOW), NOW)
    p.reset()
    assert p.process(quote(exchange_timestamp=NOW - timedelta(seconds=1)), NOW).accepted


def test_depth_gives_best_bid_and_ask() -> None:
    raw = quote(
        mode="full",
        exchange_timestamp=NOW,
        depth={
            "buy": [{"price": 1399.95, "quantity": 10, "orders": 2}],
            "sell": [{"price": 1400.05, "quantity": 7, "orders": 1}],
        },
    )
    t = proc().process(raw, NOW).tick
    assert t is not None and t.bid == 1399.95 and t.ask == 1400.05
    assert t.depth_buy is not None and t.depth_buy[0].orders == 2
