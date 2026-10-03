"""ApprovalService: the human step of APPROVAL mode.

    ALGO -> SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL -> USER REVIEWS
         -> APPROVE / REJECT -> ORDER (paper broker)

* propose(): builds the trade a stored signal describes, runs every risk
  check and the position size as a preview (nothing executes), and stores
  a PENDING request that expires after APPROVAL_TTL_SECONDS. A signal the
  risk check refuses is not queued. One open request per signal.
* approve(): only a PENDING, unexpired request whose signal is still
  ACTIVE can be approved, and only while no paper trade is open (it then
  stays PENDING). The risk checks run again with the current price and
  the approved quantity as a cap; the order goes to the PAPER broker.
  Result: EXECUTED (entry placed) or FAILED (refused, reasons stored).
* reject(): PENDING -> REJECTED with the reason.
* Expiry: a background loop marks requests EXPIRED when their window
  elapses or their signal expires. An expired request is never executed.
* Emergency stop, or leaving APPROVAL mode, cancels open requests.

Every change is written to audit_logs (who, what, when, before, after)
and pushed to dashboards as a trading event. There is no live execution.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import repository as repo
from app.approvals.schemas import ApprovalRequest, ApprovalSource, ApprovalStatus
from app.core.logging import get_logger
from app.database.models.system_event import SystemEvent
from app.market_data.base import utcnow
from app.risk.schemas import RiskDecision
from app.signals.schemas import SignalStatus
from app.trading.service import SignalNotFound, TradingService

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]
MAX_OPEN_REQUESTS = 3  # the algo proposes at most this many at a time


class ApprovalError(Exception):
    status_code = 409


class ApprovalNotFound(ApprovalError):
    status_code = 404


class ApprovalConflict(ApprovalError):
    status_code = 409


class ApprovalRefused(ApprovalError):
    """The risk check refused the trade: no request is created."""

    status_code = 422

    def __init__(self, message: str, reasons: list[str]) -> None:
        super().__init__(message)
        self.reasons = reasons


class Actor:
    def __init__(self, user_id: uuid.UUID | None, email: str | None) -> None:
        self.user_id = user_id
        self.email = email


SYSTEM = Actor(None, None)


class ApprovalService:
    def __init__(
        self,
        trading: TradingService,
        session_factory: SessionFactory,
        *,
        ttl_seconds: int,
        clock: Callable[[], datetime] = utcnow,
        broadcast: Callable[[dict[str, Any]], Any] | None = None,
        sweep_seconds: float = 1.0,
    ) -> None:
        self.trading = trading
        self._session_factory = session_factory
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._broadcast = broadcast
        self._sweep_seconds = sweep_seconds
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        trading.approval_hook = self._algo_proposal
        trading.on_mode_change = self._mode_changed
        trading.on_emergency_stop = self._emergency_stop

    # --- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        try:
            await self.expire_due()
        except Exception:  # noqa: BLE001 - the database may come back later
            logger.warning("Could not expire stale approval requests at startup")
        self._task = asyncio.create_task(self._loop(), name="approvals")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_seconds)
            try:
                await self.expire_due()
            except Exception:  # noqa: BLE001 - the sweep must keep running
                logger.exception("Approval expiry sweep failed")

    # --- events ----------------------------------------------------------------

    async def _record(
        self,
        session: AsyncSession,
        action: str,
        approval: ApprovalRequest,
        actor: Actor,
        before: ApprovalStatus | None,
        message: str,
        severity: str = "INFO",
        **extra: Any,
    ) -> None:
        await repo.audit(
            session,
            action,
            approval,
            user_id=actor.user_id,
            before=before.value if before else None,
            extra={"by": actor.email, **extra},
        )
        meta = {
            "approval_id": str(approval.id),
            "status": approval.status.value,
            "symbol": approval.symbol,
            "by": actor.email,
        }
        session.add(
            SystemEvent(
                event_type=action,
                severity=severity,
                message=message[:1000],
                event_metadata=meta,
                created_at=self._clock(),
            )
        )
        await session.commit()
        logger.info("%s: %s", action, message)
        if self._broadcast is not None:
            try:
                await self._broadcast(
                    {
                        "type": "trading_event",
                        "event_type": action,
                        "severity": severity,
                        "message": message,
                        "at": self._clock().isoformat(),
                        "metadata": meta,
                    }
                )
            except Exception:  # noqa: BLE001 - a broadcast must never break approvals
                logger.warning("Approval event broadcast failed")

    # --- propose -------------------------------------------------------------------

    async def _algo_proposal(self, signal_id: uuid.UUID) -> bool:
        """Called by the trading loop (already holding its lock) in APPROVAL mode.
        Returns False to have the loop try the signal again on a later scan."""

        async with self._session_factory() as session:
            if len(await repo.pending(session)) >= MAX_OPEN_REQUESTS:
                return False
        try:
            await self.propose(signal_id, source=ApprovalSource.ALGO, actor=SYSTEM, locked=True)
        except (ApprovalRefused, ApprovalConflict, SignalNotFound) as exc:
            logger.info("Signal %s not queued for approval: %s", signal_id, exc)
        return True  # handled (queued, or refused by the risk check): not proposed again

    async def propose(
        self,
        signal_id: uuid.UUID,
        *,
        source: ApprovalSource,
        actor: Actor,
        locked: bool = False,
    ) -> ApprovalRequest:
        if self.trading.mode != "APPROVAL":
            raise ApprovalConflict(f"trading mode is {self.trading.mode}, not APPROVAL")
        intent, record = await self.trading.build_intent(signal_id)
        signal = record.signal
        if signal.status != SignalStatus.ACTIVE:
            raise ApprovalConflict(f"signal {signal_id} is {signal.status.value}")
        async with self._session_factory() as session:
            existing = await repo.open_for_signal(session, signal_id)
        if existing is not None:
            return existing
        # Lock order: the trading lock is never taken while holding self._lock.
        decision = await self.trading.preview_intent(intent, locked=locked)
        if not decision.approved or decision.size is None:
            reasons = [r.value for r in decision.rejection_reasons]
            raise ApprovalRefused(f"risk check refused it: {', '.join(reasons)}", reasons)
        size = decision.size
        risk_per_share = abs(size.entry - size.stop_loss)
        reward = abs(signal.target - size.entry)
        async with self._lock:
            now = self._clock()
            async with self._session_factory() as session:
                existing = await repo.open_for_signal(session, signal_id)
                if existing is not None:
                    return existing
                approval = await repo.create(
                    session,
                    created_at=now,
                    source=source.value,
                    signal_id=signal_id,
                    symbol=signal.symbol,
                    direction=signal.direction.value,
                    side=intent.side.value,
                    entry=size.entry,
                    stop_loss=size.stop_loss,
                    target=signal.target,
                    quantity=size.quantity,
                    risk_amount=size.actual_risk,
                    risk_per_share=round(risk_per_share, 4),
                    position_value=size.position_value,
                    reward_risk=round(reward / risk_per_share, 2) if risk_per_share else 0.0,
                    strategy=signal.strategy.value,
                    strategy_version=signal.strategy_version,
                    score=signal.score,
                    category=signal.category.value,
                    market_regime=signal.market_regime.value,
                    regime_confidence=signal.regime_confidence,
                    reasons=list(signal.reasons),
                    risk_checks=[c.model_dump(mode="json") for c in decision.checks],
                    signal_timestamp=signal.timestamp,
                    expires_at=now + timedelta(seconds=self.ttl_seconds),
                    requested_by=actor.user_id,
                    requested_by_email=actor.email,
                )
                await self._record(
                    session,
                    "APPROVAL_REQUESTED",
                    approval,
                    actor,
                    None,
                    f"Approval needed: {approval.direction} {approval.quantity} {approval.symbol} "
                    f"(score {approval.score}, risk ₹{approval.risk_amount:,.2f}); "
                    f"expires in {self.ttl_seconds}s",
                    source=source.value,
                )
        return approval

    # --- decide -----------------------------------------------------------------------

    async def _load_pending(self, session: AsyncSession, approval_id: uuid.UUID) -> ApprovalRequest:
        approval = await repo.get(session, approval_id)
        if approval is None:
            raise ApprovalNotFound(f"Approval request {approval_id} not found.")
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalConflict(f"Approval request is {approval.status.value}.")
        return approval

    async def _expire(
        self, session: AsyncSession, approval: ApprovalRequest, reason: str
    ) -> ApprovalRequest | None:
        expired = await repo.transition(
            session,
            approval.id,
            ApprovalStatus.PENDING,
            ApprovalStatus.EXPIRED,
            expired_at=self._clock(),
            reason=reason,
        )
        if expired is not None:
            await self._record(
                session,
                "APPROVAL_EXPIRED",
                expired,
                SYSTEM,
                ApprovalStatus.PENDING,
                f"{expired.direction} {expired.quantity} {expired.symbol} expired: {reason}; "
                "not executed",
                severity="WARNING",
            )
        return expired

    async def approve(
        self, approval_id: uuid.UUID, actor: Actor, note: str | None = None
    ) -> ApprovalRequest:
        # 1. decide (under the approval lock; database work only)
        async with self._lock, self._session_factory() as session:
            approval = await self._load_pending(session, approval_id)
            now = self._clock()
            if now >= approval.expires_at:
                await self._expire(session, approval, "approval window elapsed")
                raise ApprovalConflict("Approval request expired; it was not executed.")
            if self.trading.mode != "APPROVAL":
                raise ApprovalConflict(f"Trading mode is {self.trading.mode}, not APPROVAL.")
            if approval.signal_id is None:
                raise ApprovalConflict("Approval request has no signal.")
            try:
                intent, record = await self.trading.build_intent(approval.signal_id)
            except SignalNotFound:
                await self._expire(session, approval, "signal no longer exists")
                raise ApprovalConflict("Signal no longer exists; not executed.") from None
            if record.signal.status != SignalStatus.ACTIVE:
                await self._expire(session, approval, f"signal {record.signal.status.value}")
                raise ApprovalConflict("Signal has expired; it was not executed.")
            if self.trading.trade_active:
                raise ApprovalConflict(
                    "A paper trade is already active; approve this request after it closes."
                )
            approved = await repo.transition(
                session,
                approval.id,
                ApprovalStatus.PENDING,
                ApprovalStatus.APPROVED,
                approved_by=actor.user_id,
                approved_by_email=actor.email,
                approved_at=now,
                note=note,
            )
            if approved is None:
                raise ApprovalConflict("Someone else decided this request first.")
            await self._record(
                session,
                "APPROVAL_APPROVED",
                approved,
                actor,
                ApprovalStatus.PENDING,
                f"{actor.email} approved {approved.direction} {approved.quantity} {approved.symbol}",
                note=note,
            )

        # 2. execute (paper broker; the trading lock is taken without self._lock held).
        # The risk checks run again; the approved quantity is the most that executes.
        intent = intent.model_copy(
            update={"approval_id": approved.id, "max_quantity": approved.quantity}
        )
        decision: RiskDecision | None = None
        trade_id: uuid.UUID | None = None
        failure: str | None
        try:
            decision, trade_id = await self.trading.execute_approved(intent)
        except Exception as exc:  # noqa: BLE001 - recorded as a failed approval
            logger.exception("Approved request %s failed to execute", approved.id)
            failure = f"execution error: {type(exc).__name__}: {exc}"
        else:
            failure = None if decision.approved and trade_id else decision.summary

        # 3. record the outcome
        async with self._lock, self._session_factory() as session:
            if failure is None:
                assert decision is not None and decision.size is not None
                result = await repo.transition(
                    session,
                    approved.id,
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.EXECUTED,
                    executed_at=self._clock(),
                    trade_id=trade_id,
                )
                assert result is not None
                await self._record(
                    session,
                    "APPROVAL_EXECUTED",
                    result,
                    actor,
                    ApprovalStatus.APPROVED,
                    f"Paper entry placed: {result.direction} {decision.size.quantity} "
                    f"{result.symbol} (trade {trade_id})",
                    trade_id=str(trade_id),
                    executed_quantity=decision.size.quantity,
                )
            else:
                reasons = [r.value for r in decision.rejection_reasons] if decision else []
                result = await repo.transition(
                    session,
                    approved.id,
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.FAILED,
                    reason=failure[:1000],
                    execution_reasons=reasons,
                )
                assert result is not None
                await self._record(
                    session,
                    "APPROVAL_FAILED",
                    result,
                    actor,
                    ApprovalStatus.APPROVED,
                    f"Approved {result.symbol} not executed: {failure}",
                    severity="WARNING",
                    reasons=reasons,
                )
            return result

    async def reject(self, approval_id: uuid.UUID, actor: Actor, reason: str) -> ApprovalRequest:
        async with self._lock:
            async with self._session_factory() as session:
                approval = await self._load_pending(session, approval_id)
                rejected = await repo.transition(
                    session,
                    approval.id,
                    ApprovalStatus.PENDING,
                    ApprovalStatus.REJECTED,
                    rejected_by=actor.user_id,
                    rejected_by_email=actor.email,
                    rejected_at=self._clock(),
                    reason=reason,
                )
                if rejected is None:
                    raise ApprovalConflict("Someone else decided this request first.")
                await self._record(
                    session,
                    "APPROVAL_REJECTED",
                    rejected,
                    actor,
                    ApprovalStatus.PENDING,
                    f"{actor.email} rejected {rejected.direction} {rejected.quantity} "
                    f"{rejected.symbol}: {reason}",
                )
                return rejected

    # --- expiry and cancellation --------------------------------------------------------

    async def expire_due(self) -> list[ApprovalRequest]:
        """PENDING requests whose window elapsed or whose signal expired."""

        out: list[ApprovalRequest] = []
        async with self._lock:
            async with self._session_factory() as session:
                for approval in await repo.pending(session):
                    reason: str | None = None
                    if self._clock() >= approval.expires_at:
                        reason = "approval window elapsed"
                    elif approval.signal_id is not None:
                        try:
                            _, record = await self.trading.build_intent(approval.signal_id)
                            if record.signal.status != SignalStatus.ACTIVE:
                                reason = f"signal {record.signal.status.value}"
                        except SignalNotFound:
                            reason = "signal no longer exists"
                    if reason is not None:
                        expired = await self._expire(session, approval, reason)
                        if expired is not None:
                            out.append(expired)
        return out

    async def cancel_open(self, reason: str, actor: Actor = SYSTEM) -> list[ApprovalRequest]:
        out: list[ApprovalRequest] = []
        async with self._lock:
            async with self._session_factory() as session:
                for approval in await repo.pending(session):
                    cancelled = await repo.transition(
                        session,
                        approval.id,
                        ApprovalStatus.PENDING,
                        ApprovalStatus.CANCELLED,
                        reason=reason,
                    )
                    if cancelled is not None:
                        out.append(cancelled)
                        await self._record(
                            session,
                            "APPROVAL_CANCELLED",
                            cancelled,
                            actor,
                            ApprovalStatus.PENDING,
                            f"{cancelled.symbol} request cancelled: {reason}",
                            severity="WARNING",
                        )
        return out

    async def _mode_changed(self, previous: str, mode: str) -> None:
        if previous == "APPROVAL" and mode != "APPROVAL":
            await self.cancel_open(f"trading mode changed to {mode}")

    async def _emergency_stop(self, reason: str) -> None:
        await self.cancel_open(f"emergency stop: {reason}")

    # --- read -------------------------------------------------------------------------

    async def list(
        self, statuses: list[ApprovalStatus] | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[ApprovalRequest], int]:
        async with self._session_factory() as session:
            return await repo.list_requests(session, statuses=statuses, limit=limit, offset=offset)

    async def get(self, approval_id: uuid.UUID) -> ApprovalRequest:
        async with self._session_factory() as session:
            approval = await repo.get(session, approval_id)
        if approval is None:
            raise ApprovalNotFound(f"Approval request {approval_id} not found.")
        return approval

    async def counts(self) -> dict[str, int]:
        async with self._session_factory() as session:
            return await repo.counts(session)

    def now(self) -> datetime:
        return self._clock()
