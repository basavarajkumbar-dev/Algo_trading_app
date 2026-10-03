"""Approval requests (Phase 12).

    ALGO -> SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL -> USER REVIEWS
         -> APPROVE / REJECT -> ORDER (paper broker)

State machine (PROJECT_CONTRACT.md section 9.4: explicit states):

    PENDING  -> APPROVED   a TRADER / ADMIN approved it (transient: execution follows)
    PENDING  -> REJECTED   a TRADER / ADMIN rejected it (reason stored)
    PENDING  -> EXPIRED    the approval window elapsed, or the signal expired
    PENDING  -> CANCELLED  emergency stop, or the mode left APPROVAL
    APPROVED -> EXECUTED   the risk check passed again and the paper entry was placed
    APPROVED -> FAILED     the risk check refused it at execution time (reasons stored)

EXECUTED, FAILED, REJECTED, EXPIRED and CANCELLED are final.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

APPROVAL_NOTE = (
    "Approving sends a PAPER order (simulated broker, no real money). The risk "
    "checks run again at approval time; an expired request is never executed. "
    "A score is not a prediction of profit."
)


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"

    @property
    def is_final(self) -> bool:
        return self not in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)


TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.PENDING: {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
    },
    ApprovalStatus.APPROVED: {ApprovalStatus.EXECUTED, ApprovalStatus.FAILED},
}


class InvalidTransition(Exception):
    pass


def check_transition(current: ApprovalStatus, new: ApprovalStatus) -> None:
    if new not in TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"approval is {current.value}; cannot become {new.value}")


class ApprovalSource(str, Enum):
    ALGO = "ALGO"  # proposed by the automatic loop from a READY signal
    MANUAL = "MANUAL"  # a user asked for one from a signal


class ApprovalRequest(BaseModel):
    id: uuid.UUID
    status: ApprovalStatus
    source: ApprovalSource
    signal_id: uuid.UUID | None
    symbol: str
    direction: str  # BUY / SELL
    side: str  # LONG / SHORT
    entry: float
    stop_loss: float
    target: float
    quantity: int
    risk_amount: float  # quantity x risk per share, rupees
    risk_per_share: float
    position_value: float
    reward_risk: float
    strategy: str | None
    strategy_version: str | None
    score: float | None
    category: str | None
    market_regime: str | None
    regime_confidence: float | None
    reasons: list[str] = Field(default_factory=list)
    risk_checks: list[dict[str, Any]] = Field(default_factory=list)
    signal_timestamp: datetime | None
    created_at: datetime
    expires_at: datetime
    requested_by: uuid.UUID | None = None
    requested_by_email: str | None = None
    approved_by: uuid.UUID | None = None
    approved_by_email: str | None = None
    approved_at: datetime | None = None
    rejected_by: uuid.UUID | None = None
    rejected_by_email: str | None = None
    rejected_at: datetime | None = None
    expired_at: datetime | None = None
    executed_at: datetime | None = None
    reason: str | None = None  # rejection reason, or why it expired / failed / was cancelled
    note: str | None = None  # optional comment from the approver
    trade_id: uuid.UUID | None = None
    execution_reasons: list[str] = Field(default_factory=list)  # risk rejection at execution

    def seconds_left(self, now: datetime) -> float:
        return max(0.0, (self.expires_at - now).total_seconds())
