"""Persistence for backtest runs, versions, parameter sets, trades and equity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.calendar import IST
from app.backtesting.parameters import StrategyChoice, params_hash, split_scopes, strategy_versions
from app.backtesting.schemas import BacktestOutcome, BacktestRequest
from app.database.models.backtest import (
    BacktestEquityPoint,
    BacktestRun,
    BacktestTradeRecord,
    ParameterSet,
    StrategyVersion,
)

RUNNING_STATES = ("QUEUED", "RUNNING")


async def upsert_versions(
    session: AsyncSession, choice: StrategyChoice, strategy_params: dict[str, Any]
) -> list[uuid.UUID]:
    ids = []
    for v in strategy_versions(choice, strategy_params):
        stmt = insert(StrategyVersion).values(
            id=uuid.uuid4(),
            strategy=v.strategy,
            version_id=v.version_id,
            semver=v.semver,
            config_hash=v.config_hash,
            config=v.config,
            strategy_choice=choice.value,
            strategy_params=strategy_params,
        )
        stmt = stmt.on_conflict_do_nothing(constraint="uq_strategy_version")
        await session.execute(stmt)
        row = (
            await session.execute(
                select(StrategyVersion.id).where(
                    StrategyVersion.strategy == v.strategy,
                    StrategyVersion.version_id == v.version_id,
                )
            )
        ).scalar_one()
        ids.append(row)
    return ids


async def upsert_parameter_set(
    session: AsyncSession, choice: StrategyChoice, params: dict[str, Any]
) -> uuid.UUID:
    h = params_hash(params)
    stmt = insert(ParameterSet).values(
        id=uuid.uuid4(), strategy_choice=choice.value, params_hash=h, params=params
    )
    await session.execute(stmt.on_conflict_do_nothing(constraint="uq_parameter_set"))
    return (
        await session.execute(
            select(ParameterSet.id).where(
                ParameterSet.strategy_choice == choice.value, ParameterSet.params_hash == h
            )
        )
    ).scalar_one()


async def create_run(
    session: AsyncSession, req: BacktestRequest, *, user_id: uuid.UUID | None, data: dict[str, Any]
) -> BacktestRun:
    run = BacktestRun(
        id=uuid.uuid4(),
        name=req.name,
        mode=req.mode.value,
        status="QUEUED",
        progress=0.0,
        message="Queued",
        strategy_choice=req.strategy.value,
        timeframe=req.timeframe,
        start_date=req.start_date,
        end_date=req.end_date,
        universe={"symbols": req.symbols},
        data_source=data,
        capital=req.capital,
        request=req.model_dump(mode="json"),
        created_by=user_id,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def set_status(session: AsyncSession, run_id: uuid.UUID, **fields: Any) -> None:
    await session.execute(update(BacktestRun).where(BacktestRun.id == run_id).values(**fields))
    await session.commit()


def _day_time(d: Any) -> datetime:
    return datetime.combine(d, time(15, 30), tzinfo=IST).astimezone(UTC)


async def save_result(
    session: AsyncSession,
    run_id: uuid.UUID,
    req: BacktestRequest,
    outcome: BacktestOutcome,
    *,
    duration_ms: int,
) -> None:
    strategy_params, _ = split_scopes(req.strategy, outcome.selected_params)
    version_ids = await upsert_versions(session, req.strategy, strategy_params)
    param_id = await upsert_parameter_set(session, req.strategy, outcome.selected_params)
    # trades and equity go to their own tables
    result = outcome.model_dump(mode="json", exclude={"trades", "equity_curve", "daily_equity"})
    await session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id)
        .values(
            status="COMPLETED",
            progress=1.0,
            message="Completed",
            metrics=outcome.metrics.model_dump(mode="json"),
            robustness=outcome.robustness.model_dump(mode="json"),
            result=result,
            universe={
                "symbols": req.symbols,
                "membership": outcome.data.get("universe"),
                "point_in_time": outcome.data.get("point_in_time_universe"),
            },
            strategy_version_id=version_ids[0] if len(version_ids) == 1 else None,
            parameter_set_id=param_id,
            finished_at=datetime.now(UTC),
            duration_ms=duration_ms,
        )
    )
    for t in outcome.trades:
        session.add(
            BacktestTradeRecord(
                run_id=run_id,
                segment=t.segment,
                symbol=t.symbol,
                side=t.side,
                strategy=t.strategy,
                opened_at=t.opened_at,
                closed_at=t.closed_at,
                quantity=t.quantity,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                exit_reason=t.exit_reason,
                net_pnl=t.net_pnl,
                r_multiple=t.r_multiple,
                payload=t.model_dump(mode="json"),
            )
        )
    for i, p in enumerate(outcome.equity_curve):
        session.add(
            BacktestEquityPoint(
                run_id=run_id,
                kind="TRADE",
                seq=i,
                at=p.closed_at,
                equity=p.equity,
                drawdown=p.drawdown,
                drawdown_percent=p.drawdown_percent,
                net_pnl=p.net_pnl,
            )
        )
    for i, d in enumerate(outcome.daily_equity):
        session.add(
            BacktestEquityPoint(
                run_id=run_id, kind="DAY", seq=i, at=_day_time(d.date), equity=d.equity
            )
        )
    await session.commit()


async def mark_interrupted(session: AsyncSession) -> int:
    res = await session.execute(
        update(BacktestRun)
        .where(BacktestRun.status.in_(RUNNING_STATES))
        .values(
            status="FAILED", message="Interrupted by a restart", error="Interrupted by a restart"
        )
    )
    await session.commit()
    return res.rowcount or 0


async def list_runs(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> tuple[list[BacktestRun], int]:
    total = (await session.execute(select(func.count()).select_from(BacktestRun))).scalar_one()
    rows = (
        (
            await session.execute(
                select(BacktestRun)
                .order_by(BacktestRun.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> BacktestRun | None:
    return await session.get(BacktestRun, run_id)


async def list_trades(
    session: AsyncSession, run_id: uuid.UUID, *, limit: int = 500, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    q = select(BacktestTradeRecord).where(BacktestTradeRecord.run_id == run_id)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                q.order_by(BacktestTradeRecord.closed_at).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [r.payload for r in rows], total


async def list_equity(session: AsyncSession, run_id: uuid.UUID) -> list[BacktestEquityPoint]:
    rows = (
        (
            await session.execute(
                select(BacktestEquityPoint)
                .where(BacktestEquityPoint.run_id == run_id)
                .order_by(BacktestEquityPoint.kind, BacktestEquityPoint.seq)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def delete_run(session: AsyncSession, run_id: uuid.UUID) -> bool:
    res = await session.execute(delete(BacktestRun).where(BacktestRun.id == run_id))
    await session.commit()
    return bool(res.rowcount)


async def list_versions(session: AsyncSession) -> list[StrategyVersion]:
    rows = (
        (await session.execute(select(StrategyVersion).order_by(StrategyVersion.created_at.desc())))
        .scalars()
        .all()
    )
    return list(rows)
