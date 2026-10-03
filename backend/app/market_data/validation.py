"""Market data validation.

Per PROJECT_CONTRACT.md's Market Data Validation section: before any
tick reaches the candle builder, it is checked for missing/invalid
price, staleness, duplication, timestamp problems, and out-of-order
arrival. Invalid ticks are rejected (not silently coerced) so a bad
upstream feed can never quietly corrupt candles.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.market_data.base import utcnow
from app.market_data.schemas import Tick


class ValidationIssue(str):
    """String issue codes (plain strings so adding new ones doesn't
    require an enum/migration change)."""

    MISSING_PRICE = "MISSING_PRICE"
    INVALID_PRICE = "INVALID_PRICE"
    MISSING_SYMBOL = "MISSING_SYMBOL"
    STALE_TICK = "STALE_TICK"
    DUPLICATE_TICK = "DUPLICATE_TICK"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    OUT_OF_ORDER = "OUT_OF_ORDER"


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    issues: tuple[str, ...] = ()

    @property
    def is_rejected(self) -> bool:
        return not self.is_valid


@dataclass
class TickValidator:
    """Stateful validator: tracks the last accepted tick per symbol so
    it can detect duplicates and out-of-order/stale arrivals.

    Thresholds are constructor parameters (never hardcoded deep inside
    validation logic) so they can be tuned per environment/tests without
    editing this class.
    """

    stale_after: timedelta = field(default_factory=lambda: timedelta(seconds=30))
    future_tolerance: timedelta = field(default_factory=lambda: timedelta(seconds=5))
    clock: Callable[[], datetime] = utcnow
    _last_accepted: dict[str, Tick] = field(default_factory=dict, repr=False)

    def validate(self, tick: Tick | None) -> ValidationResult:
        """Validate a tick without updating internal state."""

        issues: list[str] = []

        if tick is None:
            return ValidationResult(is_valid=False, issues=(ValidationIssue.MISSING_PRICE,))

        if not tick.symbol or not tick.symbol.strip():
            issues.append(ValidationIssue.MISSING_SYMBOL)

        if tick.price is None:
            issues.append(ValidationIssue.MISSING_PRICE)
        elif math.isnan(tick.price) or math.isinf(tick.price) or tick.price <= 0:
            issues.append(ValidationIssue.INVALID_PRICE)

        now = self.clock()
        if tick.timestamp > now + self.future_tolerance:
            issues.append(ValidationIssue.FUTURE_TIMESTAMP)
        elif now - tick.timestamp > self.stale_after:
            issues.append(ValidationIssue.STALE_TICK)

        last = self._last_accepted.get(tick.symbol)
        if last is not None:
            if tick.timestamp < last.timestamp:
                issues.append(ValidationIssue.OUT_OF_ORDER)
            elif tick.timestamp == last.timestamp and tick.price == last.price:
                issues.append(ValidationIssue.DUPLICATE_TICK)

        return ValidationResult(is_valid=len(issues) == 0, issues=tuple(issues))

    def validate_and_update(self, tick: Tick | None) -> ValidationResult:
        """Validate a tick and, if accepted, record it as the new
        "last accepted" tick for its symbol."""

        result = self.validate(tick)
        if result.is_valid and tick is not None:
            self._last_accepted[tick.symbol] = tick
        return result

    def last_accepted(self, symbol: str) -> Tick | None:
        return self._last_accepted.get(symbol)
