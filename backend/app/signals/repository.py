"""Signal persistence.

`sync_scan` applies one scan's signals to the database:

- a setup (symbol, strategy, direction) with no ACTIVE row -> new row
- a setup that already has an ACTIVE row -> the row is kept as created;
  only last_seen_at / last_score / last_rank / times_seen change
- an ACTIVE row whose setup is absent from this scan -> EXPIRED

Signals below `store_min_score` are not stored (they still appear in
the scanner), so the table holds setups worth reviewing rather than
every weak candidate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.signal import Signal
from app.signals.schemas import SignalStatus, TradingSignal

_PAYLOAD_EXCLUDE = {"id", "status"}


@dataclass
class SyncResult:
    ids: dict[tuple[str, str, str], uuid.UUID]  # setup key -> signal id
    created: int
    updated: int
    expired: int


def _payload(signal: TradingSignal) -> dict:
    return signal.model_dump(mode="json", exclude=_PAYLOAD_EXCLUDE)


def to_model(row: Signal) -> TradingSignal:
    """Rebuild the TradingSignal as generated, with its stored id and
    current status."""

    signal = TradingSignal.model_validate(row.payload)
    signal.id = row.id
    signal.status = SignalStatus(row.status)
    return signal


async def sync_scan(
    session: AsyncSession,
    signals: list[TradingSignal],
    *,
    now: datetime,
    store_min_score: float,
    ranks: dict[tuple[str, str, str], int] | None = None,
) -> SyncResult:
    ranks = ranks or {}
    storable = {s.key: s for s in signals if s.score >= store_min_score}

    active_rows = (
        (await session.execute(select(Signal).where(Signal.status == SignalStatus.ACTIVE.value)))
        .scalars()
        .all()
    )
    active = {(r.symbol, r.strategy, r.direction): r for r in active_rows}

    created = updated = expired = 0
    ids: dict[tuple[str, str, str], uuid.UUID] = {}
    for key, row in active.items():
        if key in storable:
            signal = storable[key]
            row.last_seen_at = now
            row.last_score = signal.score
            row.last_rank = ranks.get(key)
            row.times_seen += 1
            ids[key] = row.id
            updated += 1
        else:
            row.status = SignalStatus.EXPIRED.value
            row.expired_at = now
            row.last_rank = None
            expired += 1

    for key, signal in storable.items():
        if key in active:
            continue
        row = Signal(
            id=uuid.uuid4(),
            symbol=signal.symbol,
            direction=signal.direction.value,
            strategy=signal.strategy.value,
            strategy_version=signal.strategy_version,
            timeframe=signal.timeframe.value,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            target=signal.target,
            risk_reward=signal.risk_reward,
            quantity_candidate=signal.quantity_candidate,
            score=signal.score,
            category=signal.category.value,
            executable=signal.executable,
            confidence=signal.confidence,
            market_regime=signal.market_regime.value,
            regime_confidence=signal.regime_confidence,
            status=SignalStatus.ACTIVE.value,
            candle_time=signal.candle_time,
            generated_at=signal.timestamp,
            last_seen_at=now,
            last_score=signal.score,
            last_rank=ranks.get(key),
            times_seen=1,
            payload=_payload(signal),
        )
        session.add(row)
        ids[key] = row.id
        created += 1

    await session.commit()
    return SyncResult(ids=ids, created=created, updated=updated, expired=expired)


@dataclass
class SignalRecord:
    signal: TradingSignal
    generated_at: datetime
    last_seen_at: datetime
    expired_at: datetime | None
    last_score: float
    last_rank: int | None
    times_seen: int


def to_record(row: Signal) -> SignalRecord:
    return SignalRecord(
        signal=to_model(row),
        generated_at=row.generated_at,
        last_seen_at=row.last_seen_at,
        expired_at=row.expired_at,
        last_score=float(row.last_score),
        last_rank=row.last_rank,
        times_seen=row.times_seen,
    )


async def list_signals(
    session: AsyncSession,
    *,
    status: str | None = None,
    category: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    executable: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SignalRecord], int]:
    query = select(Signal)
    if status:
        query = query.where(Signal.status == status)
    if category:
        query = query.where(Signal.category == category)
    if symbol:
        query = query.where(Signal.symbol == symbol)
    if strategy:
        query = query.where(Signal.strategy == strategy)
    if executable is not None:
        query = query.where(Signal.executable.is_(executable))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(Signal.generated_at.desc(), Signal.score.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [to_record(r) for r in rows], total


async def get_signal(session: AsyncSession, signal_id: uuid.UUID) -> SignalRecord | None:
    row = await session.get(Signal, signal_id)
    return to_record(row) if row is not None else None
