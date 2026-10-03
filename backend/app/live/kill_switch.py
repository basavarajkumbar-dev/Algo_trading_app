"""KillSwitch: stop live trading now.

Engaging it
  * blocks every new entry (the execution gate refuses non-reduce-only
    orders) and disarms live trading;
  * cancels a pending (unfilled or partly filled) live entry;
  * leaves an open position's protective stop and target in place, and
    does NOT close the position unless LIVE_CLOSE_POSITIONS_ON_KILL=true or
    the request explicitly asks for it;
  * engages the paper engine's emergency stop as well.

It stays engaged until an admin resets it; a reset does not re-arm.
Every engage and reset is audited.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from app.market_data.base import utcnow


class KillSwitchState(BaseModel):
    engaged: bool
    reason: str | None = None
    engaged_by: uuid.UUID | None = None
    engaged_at: datetime | None = None
    close_positions: bool = False
    entry_cancelled: bool = False
    exit_requested: bool = False


class KillSwitchResult(BaseModel):
    state: KillSwitchState
    was_armed: bool
    entry_cancelled: bool
    exit_requested: bool


class KillSwitch:
    def __init__(
        self,
        *,
        on_engage: Callable[[str, bool], Awaitable[tuple[bool, bool, bool]]],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """`on_engage(reason, close_positions)` does the work and returns
        (was_armed, entry_cancelled, exit_requested)."""

        self._on_engage = on_engage
        self._clock = clock
        self._state = KillSwitchState(engaged=False)

    @property
    def engaged(self) -> bool:
        return self._state.engaged

    @property
    def state(self) -> KillSwitchState:
        return self._state.model_copy()

    async def engage(
        self, *, reason: str, actor: uuid.UUID | None, close_positions: bool
    ) -> KillSwitchResult:
        self._state = KillSwitchState(
            engaged=True,
            reason=reason,
            engaged_by=actor,
            engaged_at=self._clock(),
            close_positions=close_positions,
        )
        was_armed, cancelled, exiting = await self._on_engage(reason, close_positions)
        self._state.entry_cancelled = cancelled
        self._state.exit_requested = exiting
        return KillSwitchResult(
            state=self.state, was_armed=was_armed, entry_cancelled=cancelled, exit_requested=exiting
        )

    def reset(self) -> KillSwitchState:
        previous = self.state
        self._state = KillSwitchState(engaged=False)
        return previous
