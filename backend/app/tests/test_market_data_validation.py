"""Tick validation tests.

Fully deterministic — no real time, network, or database dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market_data.schemas import Tick
from app.market_data.validation import TickValidator, ValidationIssue


def _tick(symbol: str = "RELIANCE", price: float = 1200.0, volume: int = 100, ts=None) -> Tick:
    return Tick(symbol=symbol, price=price, volume=volume, timestamp=ts or datetime.now(UTC))


def test_valid_tick_is_accepted() -> None:
    validator = TickValidator()
    result = validator.validate_and_update(_tick())
    assert result.is_valid
    assert result.issues == ()


def test_missing_tick_is_rejected() -> None:
    validator = TickValidator()
    result = validator.validate(None)
    assert result.is_rejected
    assert ValidationIssue.MISSING_PRICE in result.issues


def test_missing_symbol_is_rejected() -> None:
    validator = TickValidator()
    tick = _tick(symbol="")
    result = validator.validate(tick)
    assert result.is_rejected
    assert ValidationIssue.MISSING_SYMBOL in result.issues


def test_invalid_price_zero_negative_nan_inf_are_rejected() -> None:
    validator = TickValidator()
    for bad_price in (0.0, -5.0, float("nan"), float("inf")):
        tick = _tick(price=bad_price)
        result = validator.validate(tick)
        assert result.is_rejected, f"price={bad_price} should be rejected"
        assert ValidationIssue.INVALID_PRICE in result.issues


def test_stale_tick_is_rejected() -> None:
    validator = TickValidator(stale_after=timedelta(seconds=30))
    old_ts = datetime.now(UTC) - timedelta(minutes=5)
    result = validator.validate(_tick(ts=old_ts))
    assert result.is_rejected
    assert ValidationIssue.STALE_TICK in result.issues


def test_future_timestamp_is_rejected() -> None:
    validator = TickValidator(future_tolerance=timedelta(seconds=5))
    future_ts = datetime.now(UTC) + timedelta(minutes=1)
    result = validator.validate(_tick(ts=future_ts))
    assert result.is_rejected
    assert ValidationIssue.FUTURE_TIMESTAMP in result.issues


def test_duplicate_tick_is_rejected() -> None:
    validator = TickValidator()
    ts = datetime.now(UTC)
    first = _tick(price=1200.0, ts=ts)
    duplicate = _tick(price=1200.0, ts=ts)

    assert validator.validate_and_update(first).is_valid
    result = validator.validate_and_update(duplicate)
    assert result.is_rejected
    assert ValidationIssue.DUPLICATE_TICK in result.issues


def test_same_timestamp_but_different_price_is_not_a_duplicate() -> None:
    """A genuine same-millisecond trade at a different price should not
    be treated as a duplicate — only identical (timestamp, price) is."""

    validator = TickValidator()
    ts = datetime.now(UTC)
    assert validator.validate_and_update(_tick(price=1200.0, ts=ts)).is_valid
    result = validator.validate_and_update(_tick(price=1201.0, ts=ts))
    assert result.is_valid


def test_out_of_order_tick_is_rejected() -> None:
    validator = TickValidator()
    now = datetime.now(UTC)
    later = _tick(ts=now)
    earlier = _tick(ts=now - timedelta(seconds=10))

    assert validator.validate_and_update(later).is_valid
    result = validator.validate_and_update(earlier)
    assert result.is_rejected
    assert ValidationIssue.OUT_OF_ORDER in result.issues


def test_validation_is_per_symbol() -> None:
    """A duplicate/out-of-order check for one symbol must not be
    affected by ticks seen for a different symbol."""

    validator = TickValidator()
    ts = datetime.now(UTC)
    assert validator.validate_and_update(_tick(symbol="RELIANCE", ts=ts)).is_valid
    # Same timestamp, different symbol — must be independently valid.
    assert validator.validate_and_update(_tick(symbol="TCS", ts=ts)).is_valid


def test_last_accepted_returns_none_for_unknown_symbol() -> None:
    validator = TickValidator()
    assert validator.last_accepted("UNKNOWN") is None


def test_rejected_tick_does_not_update_last_accepted_state() -> None:
    """An invalid tick must not become the new baseline for future
    duplicate/out-of-order comparisons."""

    validator = TickValidator()
    ts = datetime.now(UTC)
    validator.validate_and_update(_tick(price=1200.0, ts=ts))

    # This is out-of-order and rejected...
    validator.validate_and_update(_tick(ts=ts - timedelta(seconds=1)))

    # ...so the last accepted tick for the symbol must still be the first one.
    last = validator.last_accepted("RELIANCE")
    assert last is not None
    assert last.timestamp == ts
