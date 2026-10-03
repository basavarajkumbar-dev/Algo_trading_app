"""Backtesting endpoints (Phase 10).

GET    /backtests/options        strategies, parameter spaces, versions, symbols,
                                 data sources, limits and defaults
POST   /backtests                queue a run (TRADER / ADMIN); 202 + run
GET    /backtests                runs, newest first
GET    /backtests/{id}           one run: request, status / progress, results
GET    /backtests/{id}/trades    its trades
GET    /backtests/{id}/equity    its equity curve (per trade) and daily equity
POST   /backtests/{id}/cancel    cancel a queued or running run (TRADER / ADMIN)
DELETE /backtests/{id}           delete a finished run (TRADER / ADMIN)

Results describe how rules behaved on past (or synthetic) data under the
stated assumptions. They do not predict or guarantee future performance.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.backtesting import repository as repo
from app.backtesting.calendar import trading_days
from app.backtesting.data import SyntheticHistorySource, describe_source
from app.backtesting.metrics import BACKTEST_NOTE
from app.backtesting.parameters import (
    MAX_GRID_COMBINATIONS,
    ParameterError,
    ParamSpec,
    StrategyChoice,
    defaults,
    param_space,
    split_scopes,
    strategy_versions,
    validate_params,
)
from app.backtesting.runner import (
    ASSUMPTIONS,
    BacktestError,
    estimate_seconds,
    make_source,
    preflight,
)
from app.backtesting.schemas import (
    MAX_DAYS,
    MAX_SYMBOLS,
    BacktestMode,
    BacktestRequest,
    CostSpec,
    DataSourceKind,
    RiskSpec,
    ValidationSpec,
)
from app.backtesting.service import BacktestService
from app.brokers.zerodha.instruments import InstrumentNotFound
from app.core.config import Settings, get_settings
from app.core.constants import UserRole
from app.database.models.backtest import BacktestRun
from app.database.models.user import User
from app.database.session import get_db
from app.market_data.universe import NIFTY_50_UNIVERSE

router = APIRouter(prefix="/backtests", tags=["backtesting"])
_operator = require_role(UserRole.TRADER, UserRole.ADMIN)

STRATEGY_LABELS = {
    StrategyChoice.ALL: "All strategies (live router)",
    StrategyChoice.TREND_CONTINUATION: "Trend continuation",
    StrategyChoice.BREAKOUT_MOMENTUM: "Breakout momentum",
    StrategyChoice.MEAN_REVERSION: "Mean reversion",
}


def _service(request: Request) -> BacktestService:
    svc: BacktestService | None = getattr(request.app.state, "backtest_service", None)
    if svc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Backtest service is not running.")
    return svc


def _db_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, f"Storage unavailable: {type(exc).__name__}"
    )


# --- options -----------------------------------------------------------------------------------


class VersionOption(BaseModel):
    strategy_choice: StrategyChoice
    label: str
    version_ids: list[str]
    params: dict[str, Any]  # strategy-scope parameters that produce it
    current_default: bool
    stored: bool


class StrategyOption(BaseModel):
    value: StrategyChoice
    label: str
    parameters: list[ParamSpec]
    defaults: dict[str, Any]


class DataSourceOption(BaseModel):
    kind: DataSourceKind
    label: str
    available: bool
    synthetic: bool
    first_day: date | None
    last_day: date | None
    symbols: list[str]
    note: str


class BacktestOptions(BaseModel):
    strategies: list[StrategyOption]
    versions: list[VersionOption]
    data_sources: list[DataSourceOption]
    timeframes: list[str]
    modes: list[BacktestMode]
    limits: dict[str, int]
    risk: RiskSpec
    costs: CostSpec
    validation: ValidationSpec
    assumptions: list[str]
    note: str = BACKTEST_NOTE


@router.get("/options", response_model=BacktestOptions)
async def options(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BacktestOptions:
    strategies = [
        StrategyOption(
            value=c, label=STRATEGY_LABELS[c], parameters=param_space(c), defaults=defaults(c)
        )
        for c in StrategyChoice
    ]
    versions: list[VersionOption] = []
    seen: set[tuple[str, str]] = set()
    for c in StrategyChoice:
        sp, _ = split_scopes(c, defaults(c))
        ids = [v.version_id for v in strategy_versions(c, sp)]
        seen.add((c.value, ",".join(ids)))
        versions.append(
            VersionOption(
                strategy_choice=c,
                label=f"{' / '.join(ids)} (current defaults)",
                version_ids=ids,
                params=sp,
                current_default=True,
                stored=False,
            )
        )
    try:
        stored = await repo.list_versions(db)
    except (SQLAlchemyError, OSError):
        stored = []
    for row in stored:
        choice = StrategyChoice(row.strategy_choice)
        ids = [v.version_id for v in strategy_versions(choice, row.strategy_params)]
        key = (choice.value, ",".join(ids))
        if key in seen:
            continue
        seen.add(key)
        versions.append(
            VersionOption(
                strategy_choice=choice,
                label=" / ".join(ids),
                version_ids=ids,
                params=row.strategy_params,
                current_default=False,
                stored=True,
            )
        )

    sources: list[DataSourceOption] = []
    synthetic = SyntheticHistorySource()
    first, last = synthetic.available_range()
    sources.append(
        DataSourceOption(
            kind=DataSourceKind.SYNTHETIC,
            label="Synthetic NSE sessions (deterministic, seeded)",
            available=True,
            synthetic=True,
            first_day=first,
            last_day=last,
            symbols=synthetic.symbols(),
            note="Simulated prices with regimes and a market factor. Tests the machinery, not a market edge.",
        )
    )
    if settings.backtest_data_dir:
        try:
            req = BacktestRequest(
                symbols=["X"],
                start_date=date.today(),
                end_date=date.today(),
                data_source={"kind": "csv"},  # type: ignore[arg-type]
            )
            csv = make_source(req, settings.backtest_data_dir)
            info = describe_source(csv)
            sources.append(
                DataSourceOption(
                    kind=DataSourceKind.CSV,
                    label=f"CSV data ({settings.backtest_data_dir})",
                    available=True,
                    synthetic=False,
                    first_day=date.fromisoformat(str(info["first_day"])),
                    last_day=date.fromisoformat(str(info["last_day"])),
                    symbols=csv.symbols(),
                    note="Your 5-minute bars. "
                    + (
                        "Point-in-time universe."
                        if csv.universe().point_in_time
                        else "No universe.csv: survivorship bias possible."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            sources.append(
                DataSourceOption(
                    kind=DataSourceKind.CSV,
                    label="CSV data",
                    available=False,
                    synthetic=False,
                    first_day=None,
                    last_day=None,
                    symbols=[],
                    note=f"Not usable: {exc}",
                )
            )
    rt = getattr(request.app.state, "zerodha", None)
    if rt is not None and rt.configured:
        symbols, missing = zerodha_symbols(rt)
        today = date.today()
        last = today - timedelta(days=1)
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        note = (
            "Downloaded from Kite Connect for the chosen range (rate-limited; cached). "
            "Universe = the symbols you pick: survivorship bias possible."
        )
        if missing:
            note += f" Not in the instrument master: {', '.join(missing)}."
        if not rt.auth.authenticated:
            note = "Log in to Zerodha (Settings) to use real history. " + note
        sources.append(
            DataSourceOption(
                kind=DataSourceKind.ZERODHA,
                label="Zerodha historical (real NSE 5-minute candles)",
                available=bool(rt.auth.authenticated),
                synthetic=False,
                # completed sessions only; Kite serves several years of
                # 5-minute history, three are offered here
                first_day=today - timedelta(days=3 * 365),
                last_day=last,
                symbols=symbols,
                note=note,
            )
        )
    return BacktestOptions(
        strategies=strategies,
        versions=versions,
        data_sources=sources,
        timeframes=["5m", "15m"],
        modes=list(BacktestMode),
        limits={
            "max_symbols": MAX_SYMBOLS,
            "max_days": MAX_DAYS,
            "max_grid_combinations": MAX_GRID_COMBINATIONS,
        },
        risk=RiskSpec(),
        costs=CostSpec(),
        validation=ValidationSpec(),
        assumptions=ASSUMPTIONS,
    )


# --- runs ----------------------------------------------------------------------------------------


class RunSummary(BaseModel):
    id: uuid.UUID
    name: str | None
    mode: str
    status: str
    progress: float
    message: str | None
    error: str | None
    strategy: str
    timeframe: str
    start_date: date
    end_date: date
    symbols: list[str]
    capital: float
    data_source: dict[str, Any]
    headline: dict[str, Any] | None  # a few metrics for the list
    flags: int | None
    created_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    estimated_seconds: float | None = None


class RunDetail(RunSummary):
    request: dict[str, Any]
    metrics: dict[str, Any] | None
    robustness: dict[str, Any] | None
    result: dict[str, Any] | None
    note: str = BACKTEST_NOTE


def _summary(row: BacktestRun, svc: BacktestService | None) -> dict[str, Any]:
    live = svc.live(row.id) if svc else None
    m = row.metrics or {}
    return {
        "id": row.id,
        "name": row.name,
        "mode": row.mode,
        "status": row.status,
        "progress": live.progress if live else row.progress,
        "message": live.message if live else row.message,
        "error": row.error,
        "strategy": row.strategy_choice,
        "timeframe": row.timeframe,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "symbols": row.universe.get("symbols", []),
        "capital": row.capital,
        "data_source": row.data_source,
        "headline": (
            {
                k: m.get(k)
                for k in (
                    "total_trades",
                    "win_rate",
                    "profit_factor",
                    "net_pnl",
                    "max_drawdown",
                    "expectancy_r",
                )
            }
            if m
            else None
        ),
        "flags": len((row.robustness or {}).get("flags", [])) if row.robustness else None,
        "created_at": row.created_at,
        "finished_at": row.finished_at,
        "duration_ms": row.duration_ms,
    }


class RunList(BaseModel):
    items: list[RunSummary]
    total: int
    queued: int
    note: str = BACKTEST_NOTE


@router.get("", response_model=RunList)
async def runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunList:
    svc = getattr(request.app.state, "backtest_service", None)
    try:
        rows, total = await repo.list_runs(db, limit=limit, offset=offset)
    except (SQLAlchemyError, OSError) as exc:
        raise _db_error(exc) from exc
    return RunList(
        items=[RunSummary(**_summary(r, svc)) for r in rows],
        total=total,
        queued=svc.queued() if svc else 0,
    )


@router.post("", response_model=RunSummary, status_code=status.HTTP_202_ACCEPTED)
async def create(
    body: BacktestRequest,
    request: Request,
    user: User = Depends(_operator),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunSummary:
    svc = _service(request)
    if body.data_source.kind == DataSourceKind.ZERODHA:
        # Real history is downloaded by the worker; check what we can now.
        rt = getattr(request.app.state, "zerodha", None)
        if rt is None or not rt.configured or svc.history_fetcher is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Zerodha is not configured.")
        if not rt.auth.authenticated:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Zerodha history needs today's Kite login."
            )
        if body.end_date >= date.today():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Zerodha backtests use completed sessions: end_date must be before today.",
            )
        try:
            validate_params(body.strategy, body.parameters)
        except ParameterError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        known, _ = zerodha_symbols(rt)
        unknown = [x for x in body.symbols if x not in known]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Not available from Zerodha: {', '.join(unknown)}",
            )
        days = trading_days(body.start_date, body.end_date)
        data: dict[str, Any] = {
            "kind": "zerodha",
            "synthetic": False,
            "fingerprint": f"zerodha:{body.start_date}:{body.end_date}",
            "seed": None,
        }
    else:
        try:
            source, days = preflight(body, settings.backtest_data_dir)
        except (BacktestError, ParameterError, FileNotFoundError, KeyError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        data = {
            "kind": source.kind,
            "synthetic": source.synthetic,
            "fingerprint": source.fingerprint(),
            "seed": body.data_source.seed if source.synthetic else None,
        }
    try:
        row = await repo.create_run(db, body, user_id=user.id, data=data)
    except (SQLAlchemyError, OSError) as exc:
        raise _db_error(exc) from exc
    await svc.submit(row.id, body)
    out = RunSummary(**_summary(row, svc))
    out.estimated_seconds = estimate_seconds(body, len(days))
    return out


def zerodha_symbols(rt: Any) -> tuple[list[str], list[str]]:
    """The NIFTY 50 constituents Kite can serve: those in the instrument
    master (all of them while the master is not loaded yet; a symbol that
    turns out to be unknown is refused when the run is created)."""

    names = [i.symbol for i in NIFTY_50_UNIVERSE]
    if rt.resolver.size == 0:
        return sorted(names), []
    ok: list[str] = []
    missing: list[str] = []
    for name in names:
        try:
            rt.resolver.resolve_nse_equity(name)
            ok.append(name)
        except InstrumentNotFound:
            missing.append(name)
    return sorted(ok), missing


async def _row(db: AsyncSession, run_id: uuid.UUID) -> BacktestRun:
    try:
        row = await repo.get_run(db, run_id)
    except (SQLAlchemyError, OSError) as exc:
        raise _db_error(exc) from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backtest not found.")
    return row


@router.get("/{run_id}", response_model=RunDetail)
async def detail(
    run_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
) -> RunDetail:
    row = await _row(db, run_id)
    svc = getattr(request.app.state, "backtest_service", None)
    return RunDetail(
        **_summary(row, svc),
        request=row.request,
        metrics=row.metrics,
        robustness=row.robustness,
        result=row.result,
    )


class TradeList(BaseModel):
    items: list[dict[str, Any]]
    total: int


@router.get("/{run_id}/trades", response_model=TradeList)
async def trades(
    run_id: uuid.UUID,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TradeList:
    await _row(db, run_id)
    items, total = await repo.list_trades(db, run_id, limit=limit, offset=offset)
    return TradeList(items=items, total=total)


class EquityPointOut(BaseModel):
    at: datetime
    equity: float
    drawdown: float | None = None
    drawdown_percent: float | None = None
    net_pnl: float | None = None


class EquityResponse(BaseModel):
    trades: list[EquityPointOut] = Field(default_factory=list)  # after each closed trade
    days: list[EquityPointOut] = Field(default_factory=list)  # end of each trading day


@router.get("/{run_id}/equity", response_model=EquityResponse)
async def equity(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> EquityResponse:
    await _row(db, run_id)
    points = await repo.list_equity(db, run_id)
    out = EquityResponse()
    for p in points:
        item = EquityPointOut(
            at=p.at,
            equity=p.equity,
            drawdown=p.drawdown,
            drawdown_percent=p.drawdown_percent,
            net_pnl=p.net_pnl,
        )
        (out.trades if p.kind == "TRADE" else out.days).append(item)
    return out


@router.post("/{run_id}/cancel", response_model=RunSummary)
async def cancel(
    run_id: uuid.UUID,
    request: Request,
    _: User = Depends(_operator),
    db: AsyncSession = Depends(get_db),
) -> RunSummary:
    svc = _service(request)
    row = await _row(db, run_id)
    if row.status not in repo.RUNNING_STATES or not svc.cancel(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Run is {row.status}; nothing to cancel.")
    return RunSummary(**_summary(row, svc))


@router.delete(
    "/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def remove(
    run_id: uuid.UUID,
    request: Request,
    _: User = Depends(_operator),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await _row(db, run_id)
    if row.status in repo.RUNNING_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel the run before deleting it.")
    await repo.delete_run(db, run_id)
