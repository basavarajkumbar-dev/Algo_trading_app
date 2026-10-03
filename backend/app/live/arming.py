"""Live arming: the explicit user action that live orders require.

Arming needs the live configuration (EXECUTION_MODE=ZERODHA and
LIVE_TRADING_ENABLED=true), an ADMIN with their password and a typed
confirmation (checked by the API), a disengaged kill switch and an open
NSE session. It is held in memory only and ends at the session close
(15:30 IST), on a disarm, on the kill switch, on a lost Kite session and
on a restart. Nothing re-arms automatically.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.market_data.base import utcnow

IST = ZoneInfo("Asia/Kolkata")
SESSION_CLOSE = time(15, 30)
ARM_CONFIRMATION = "ARM LIVE TRADING"


class ArmingError(Exception):
    pass


class ArmingState(BaseModel):
    armed: bool
    armed_by: uuid.UUID | None = None
    armed_by_email: str | None = None
    armed_at: datetime | None = None
    expires_at: datetime | None = None
    last_disarm_reason: str | None = None
    last_disarmed_at: datetime | None = None


class LiveArming:
    def __init__(
        self,
        *,
        configured: Callable[[], bool],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._configured = configured
        self._clock = clock
        self._state = ArmingState(armed=False)

    @property
    def is_armed(self) -> bool:
        s = self._state
        if not s.armed:
            return False
        if not self._configured():
            return False
        return s.expires_at is None or self._clock() < s.expires_at

    def expired(self) -> bool:
        s = self._state
        return s.armed and s.expires_at is not None and self._clock() >= s.expires_at

    def arm(self, *, user_id: uuid.UUID, email: str, blockers: list[str]) -> ArmingState:
        if not self._configured():
            raise ArmingError(
                "Live trading is not configured: set EXECUTION_MODE=ZERODHA and "
                "LIVE_TRADING_ENABLED=true on the backend"
            )
        if blockers:
            raise ArmingError("Cannot arm: " + "; ".join(blockers))
        now = self._clock()
        local = now.astimezone(IST)
        expires = datetime.combine(local.date(), SESSION_CLOSE, tzinfo=IST)
        if local >= expires:
            raise ArmingError("Cannot arm after the session close (15:30 IST)")
        self._state = ArmingState(
            armed=True,
            armed_by=user_id,
            armed_by_email=email,
            armed_at=now,
            expires_at=expires,
        )
        return self.state

    def disarm(self, reason: str) -> bool:
        """True when it was armed."""

        was = self._state.armed
        self._state = ArmingState(
            armed=False, last_disarm_reason=reason, last_disarmed_at=self._clock()
        )
        return was

    @property
    def state(self) -> ArmingState:
        s = self._state.model_copy()
        s.armed = self.is_armed
        return s
