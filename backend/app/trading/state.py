"""The trading state machine.

    IDLE / SCANNING -> SIGNAL_FOUND -> RISK_CHECK -> ORDER_PENDING
        -> POSITION_ACTIVE -> EXIT -> CLOSED -> IDLE / SCANNING

IDLE means automatic trading is off; SCANNING means it is on and the
engine is waiting for a READY signal. A rejected risk check returns to
the resting state. An entry that expires or is cancelled without any
fill goes straight to CLOSED. Only one trade can be anywhere between
SIGNAL_FOUND and EXIT at a time — that is what "one active trade"
means here, enforced by the transition table itself.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TradingState(str, Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    SIGNAL_FOUND = "SIGNAL_FOUND"
    RISK_CHECK = "RISK_CHECK"
    ORDER_PENDING = "ORDER_PENDING"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    EXIT = "EXIT"
    CLOSED = "CLOSED"

    @property
    def is_resting(self) -> bool:
        return self in (TradingState.IDLE, TradingState.SCANNING, TradingState.CLOSED)


S = TradingState

TRANSITIONS: dict[TradingState, frozenset[TradingState]] = {
    S.IDLE: frozenset({S.SCANNING, S.SIGNAL_FOUND}),
    S.SCANNING: frozenset({S.IDLE, S.SIGNAL_FOUND}),
    S.SIGNAL_FOUND: frozenset({S.RISK_CHECK, S.IDLE, S.SCANNING}),
    S.RISK_CHECK: frozenset({S.ORDER_PENDING, S.IDLE, S.SCANNING}),
    # an unfilled entry that is cancelled / expires / is rejected closes
    S.ORDER_PENDING: frozenset({S.POSITION_ACTIVE, S.CLOSED}),
    S.POSITION_ACTIVE: frozenset({S.EXIT}),
    S.EXIT: frozenset({S.CLOSED}),
    S.CLOSED: frozenset({S.IDLE, S.SCANNING, S.SIGNAL_FOUND}),
}


class InvalidTransition(Exception):
    pass


class StateChange(BaseModel):
    from_state: TradingState
    to_state: TradingState
    at: datetime
    reason: str


class TradingStateMachine:
    def __init__(self, now: datetime, *, history_size: int = 200) -> None:
        self.state = TradingState.IDLE
        self.changed_at = now
        self.history: list[StateChange] = []
        self._history_size = history_size

    def can(self, target: TradingState) -> bool:
        return target in TRANSITIONS[self.state]

    def to(self, target: TradingState, now: datetime, reason: str = "") -> None:
        if not self.can(target):
            raise InvalidTransition(f"{self.state.value} -> {target.value} is not allowed")
        self.history.append(
            StateChange(from_state=self.state, to_state=target, at=now, reason=reason)
        )
        if len(self.history) > self._history_size:
            del self.history[: len(self.history) - self._history_size]
        self.state = target
        self.changed_at = now
