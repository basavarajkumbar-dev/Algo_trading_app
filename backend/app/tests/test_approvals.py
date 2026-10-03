"""APPROVAL mode: the complete approval lifecycle (needs Postgres).

ALGO -> SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL -> USER REVIEWS
     -> APPROVE / REJECT -> ORDER (paper broker)
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

from app.approvals.schemas import (
    ApprovalSource,
    ApprovalStatus,
    InvalidTransition,
    check_transition,
)
from app.approvals.service import (
    Actor,
    ApprovalConflict,
    ApprovalRefused,
    ApprovalService,
)
from app.database.models.audit_log import AuditLog
from app.database.models.system_event import SystemEvent
from app.database.models.trade_approval import TradeApproval
from app.database.session import async_session_factory
from app.risk.schemas import RejectionReason as R
from app.signals.repository import sync_scan
from app.tests.paper_helpers import Clock
from app.tests.test_paper_trading_persistence import FakeMarket, service
from app.tests.test_signals import make
from app.trading.engine import EngineError
from app.trading.state import TradingState

TTL = 60
ALICE = Actor(uuid.uuid4(), "alice@example.com")
BOB = Actor(uuid.uuid4(), "bob@example.com")


async def _users() -> None:
    """Approvers must exist (foreign keys to users)."""

    from app.database.models.user import User

    async with async_session_factory() as session:
        for a in (ALICE, BOB):
            session.add(User(id=a.user_id, email=a.email, hashed_password="x"))
        await session.commit()


async def _signal(
    clock: Clock, symbol: str = "RELIANCE", score: float = 92.0
) -> tuple[uuid.UUID, Any]:
    async with async_session_factory() as session:
        signal = make(symbol, score)
        result = await sync_scan(session, [signal], now=clock(), store_min_score=40)
    return next(iter(result.ids.values())), signal


async def setup(
    mode: str = "APPROVAL", signals: Any = None
) -> tuple[Any, ApprovalService, Clock, FakeMarket]:
    await _users()
    clock = Clock()
    market = FakeMarket(clock)
    svc = service(clock, market, signals=signals)
    await svc._restore()
    if mode != "PAPER":
        await svc.set_mode(mode)
    approvals = ApprovalService(svc, async_session_factory, ttl_seconds=TTL, clock=clock)
    return svc, approvals, clock, market


async def _audit_actions() -> list[str]:
    async with async_session_factory() as session:
        rows = (
            (await session.execute(select(AuditLog.action).order_by(AuditLog.created_at)))
            .scalars()
            .all()
        )
    return [a for a in rows if a.startswith("APPROVAL_")]


# --- the full lifecycle --------------------------------------------------------------------


async def test_the_algo_proposes_a_person_approves_and_a_paper_order_follows(db_session) -> None:
    clock = Clock()
    sid, signal = await _signal(clock)
    scan = SimpleNamespace(candidates=[SimpleNamespace(status="READY", signal_id=sid)])
    stub = SimpleNamespace(
        status=lambda: SimpleNamespace(scan_count=1, last_error=None), get_scan=lambda: scan
    )
    svc, approvals, clock, market = await setup(signals=stub)
    market.set("RELIANCE", signal.entry)
    await svc.set_auto_trading(True)

    # ALGO -> SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL; nothing executes on its own
    await svc.tick()
    assert svc.engine.active is None and svc.engine.state is TradingState.SCANNING
    items, total = await approvals.list()
    assert total == 1
    req = items[0]
    assert req.status is ApprovalStatus.PENDING and req.source is ApprovalSource.ALGO
    # everything the approval screen shows
    assert req.symbol == "RELIANCE" and req.direction == "BUY"
    assert req.entry == pytest.approx(signal.entry, abs=0.05)
    assert req.stop_loss == pytest.approx(signal.stop_loss, abs=0.05)
    assert req.target == pytest.approx(signal.target)
    assert req.quantity > 0 and req.risk_amount > 0
    assert req.reward_risk == pytest.approx(
        abs(req.target - req.entry) / abs(req.entry - req.stop_loss), abs=0.01
    )
    assert req.strategy == signal.strategy.value and req.score == signal.score
    assert req.market_regime == signal.market_regime.value and req.reasons == signal.reasons
    assert req.signal_timestamp is not None and req.created_at == clock()
    assert (req.expires_at - req.created_at).total_seconds() == TTL
    assert any(c["name"] == "approval" for c in req.risk_checks)

    # the same signal is not proposed again on the next scan
    await svc.tick()
    assert (await approvals.list())[1] == 1

    # USER REVIEWS -> APPROVE -> ORDER
    clock.advance(5)
    done = await approvals.approve(req.id, ALICE, note="looks fine")
    assert done.status is ApprovalStatus.EXECUTED
    assert done.approved_by == ALICE.user_id and done.approved_by_email == ALICE.email
    assert done.approved_at == clock()
    assert done.note == "looks fine" and done.executed_at is not None
    trade = svc.engine.active
    assert trade is not None and trade.id == done.trade_id and trade.signal_id == sid
    assert trade.quantity <= req.quantity
    assert svc.engine.state is TradingState.POSITION_ACTIVE

    assert await _audit_actions() == [
        "APPROVAL_REQUESTED",
        "APPROVAL_APPROVED",
        "APPROVAL_EXECUTED",
    ]
    events = (await db_session.execute(select(SystemEvent.event_type))).scalars().all()
    assert {"APPROVAL_REQUESTED", "APPROVAL_APPROVED", "APPROVAL_EXECUTED"} <= set(events)
    # a decided request cannot be decided again
    with pytest.raises(ApprovalConflict):
        await approvals.approve(req.id, BOB)
    with pytest.raises(ApprovalConflict):
        await approvals.reject(req.id, BOB, "too late")


async def test_a_rejected_request_is_never_executed(db_session) -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    assert req.requested_by == ALICE.user_id and req.source is ApprovalSource.MANUAL
    clock.advance(3)
    rejected = await approvals.reject(req.id, BOB, "regime looks unreliable today")
    assert rejected.status is ApprovalStatus.REJECTED
    assert (rejected.rejected_by, rejected.rejected_by_email) == (BOB.user_id, BOB.email)
    assert rejected.rejected_at == clock() and rejected.reason == "regime looks unreliable today"
    assert rejected.approved_by is None
    with pytest.raises(ApprovalConflict):
        await approvals.approve(req.id, ALICE)
    assert svc.engine.active is None
    assert await _audit_actions() == ["APPROVAL_REQUESTED", "APPROVAL_REJECTED"]
    row = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "APPROVAL_REJECTED"))
    ).scalar_one()
    assert row.user_id == BOB.user_id and row.before == {"status": "PENDING"}
    assert (
        row.after["status"] == "REJECTED" and row.after["reason"] == "regime looks unreliable today"
    )


async def test_an_expired_request_is_never_executed() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    clock.advance(TTL)  # the window has elapsed exactly
    with pytest.raises(ApprovalConflict, match="expired"):
        await approvals.approve(req.id, ALICE)
    expired = await approvals.get(req.id)
    assert expired.status is ApprovalStatus.EXPIRED and expired.expired_at == clock()
    assert expired.reason == "approval window elapsed" and expired.approved_by is None
    assert svc.engine.active is None
    assert await _audit_actions() == ["APPROVAL_REQUESTED", "APPROVAL_EXPIRED"]


async def test_the_sweep_expires_requests_without_anyone_acting() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    clock.advance(TTL - 1)
    assert await approvals.expire_due() == []
    clock.advance(2)
    (expired,) = await approvals.expire_due()
    assert expired.id == req.id and expired.status is ApprovalStatus.EXPIRED
    async with async_session_factory() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.action == "APPROVAL_EXPIRED"))
        ).scalar_one()
    assert row.user_id is None and row.after is not None and row.after["actor"] == "system"


async def test_an_approval_whose_signal_expired_is_not_executed() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    async with async_session_factory() as session:
        await sync_scan(session, [], now=clock(), store_min_score=40)  # the signal expires
    with pytest.raises(ApprovalConflict, match="Signal has expired"):
        await approvals.approve(req.id, ALICE)
    after = await approvals.get(req.id)
    assert after.status is ApprovalStatus.EXPIRED and after.reason == "signal EXPIRED"
    assert svc.engine.active is None


# --- the risk check around approvals -----------------------------------------------------------


async def test_nothing_executes_in_approval_mode_without_an_approval() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    decision = await svc.execute_signal(sid)  # the direct path (e.g. the old execute button)
    assert not decision.approved and decision.rejection_reasons == [R.APPROVAL_REQUIRED]
    assert svc.engine.active is None


async def test_signals_the_risk_check_refuses_are_not_queued() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock, score=60.0)  # below the executable score
    market.set("RELIANCE", signal.entry)
    with pytest.raises(ApprovalRefused) as err:
        await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    assert "SIGNAL_NOT_EXECUTABLE" in err.value.reasons
    assert (await approvals.list())[1] == 0


async def test_the_risk_check_runs_again_at_approval_time() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    # the price runs away from the entry while the request waits
    market.set("RELIANCE", signal.entry + 3 * abs(signal.entry - signal.stop_loss))
    failed = await approvals.approve(req.id, ALICE)
    assert failed.status is ApprovalStatus.FAILED and "PRICE_MOVED_AWAY" in failed.execution_reasons
    assert failed.approved_by == ALICE.user_id and failed.trade_id is None
    assert svc.engine.active is None
    assert await _audit_actions() == ["APPROVAL_REQUESTED", "APPROVAL_APPROVED", "APPROVAL_FAILED"]


async def test_never_more_than_the_approved_quantity_executes() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    async with async_session_factory() as session:
        await session.execute(
            update(TradeApproval).where(TradeApproval.id == req.id).values(quantity=3)
        )
        await session.commit()
    done = await approvals.approve(req.id, ALICE)
    assert done.status is ApprovalStatus.EXECUTED
    assert svc.engine.active is not None and svc.engine.active.quantity == 3


async def test_a_second_approval_waits_while_a_trade_is_active() -> None:
    svc, approvals, clock, market = await setup()
    async with async_session_factory() as session:
        a, b = make("RELIANCE", 92.0), make("TCS", 92.0)
        result = await sync_scan(session, [a, b], now=clock(), store_min_score=40)
    a_sid, b_sid = (result.ids[s.key] for s in (a, b))
    market.set("RELIANCE", a.entry)
    market.set("TCS", b.entry)
    first = await approvals.propose(a_sid, source=ApprovalSource.MANUAL, actor=ALICE)
    second = await approvals.propose(b_sid, source=ApprovalSource.MANUAL, actor=ALICE)
    assert (await approvals.approve(first.id, ALICE)).status is ApprovalStatus.EXECUTED
    with pytest.raises(ApprovalConflict, match="already active"):
        await approvals.approve(second.id, ALICE)
    assert (await approvals.get(second.id)).status is ApprovalStatus.PENDING


# --- duplicates and races --------------------------------------------------------------------------


async def test_one_open_request_per_signal() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    first = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    again = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=BOB)
    assert again.id == first.id
    both = await asyncio.gather(
        *(approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE) for _ in range(3))
    )
    assert {r.id for r in both} == {first.id}


async def test_two_people_approving_at_once_execute_one_trade() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    results = await asyncio.gather(
        approvals.approve(req.id, ALICE), approvals.approve(req.id, BOB), return_exceptions=True
    )
    ok = [r for r in results if not isinstance(r, BaseException)]
    assert len(ok) == 1 and ok[0].status is ApprovalStatus.EXECUTED
    assert isinstance([r for r in results if isinstance(r, Exception)][0], ApprovalConflict)
    assert (await _audit_actions()).count("APPROVAL_APPROVED") == 1


# --- cancellation and modes ---------------------------------------------------------------------------


async def test_emergency_stop_cancels_open_requests() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    await svc.emergency_stop("drill")
    cancelled = await approvals.get(req.id)
    assert cancelled.status is ApprovalStatus.CANCELLED and "emergency stop" in (
        cancelled.reason or ""
    )
    with pytest.raises(ApprovalConflict):
        await approvals.approve(req.id, ALICE)


async def test_leaving_approval_mode_cancels_open_requests_and_proposals_need_the_mode() -> None:
    svc, approvals, clock, market = await setup()
    sid, signal = await _signal(clock)
    market.set("RELIANCE", signal.entry)
    req = await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    assert await svc.set_mode("PAPER") == "APPROVAL"
    assert (await approvals.get(req.id)).status is ApprovalStatus.CANCELLED
    with pytest.raises(ApprovalConflict, match="not APPROVAL"):
        await approvals.propose(sid, source=ApprovalSource.MANUAL, actor=ALICE)
    with pytest.raises(EngineError):
        await svc.set_mode("LIVE")
    assert (await svc.status()).mode == "PAPER"


def test_the_state_machine_only_allows_documented_transitions() -> None:
    check_transition(ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
    check_transition(ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED)
    for current, new in [
        (ApprovalStatus.PENDING, ApprovalStatus.EXECUTED),  # never without approval
        (ApprovalStatus.EXPIRED, ApprovalStatus.APPROVED),  # never after expiry
        (ApprovalStatus.REJECTED, ApprovalStatus.APPROVED),
        (ApprovalStatus.EXECUTED, ApprovalStatus.FAILED),
    ]:
        with pytest.raises(InvalidTransition):
            check_transition(current, new)
