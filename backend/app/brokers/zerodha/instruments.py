"""Instrument master: sync, storage and resolution.

Kite market data is subscribed by instrument_token; tokens are never
hardcoded here, they come from the instrument dump.

    InstrumentService.sync_instruments()
        1. GET /instruments/:exchange for each INSTRUMENT_EXCHANGES entry
        2. validate every row (bad rows are counted and skipped)
        3. upsert into broker_instruments (unique per instrument_token, so
           re-syncing never duplicates), mark rows missing from the new dump
           inactive (expired contracts)
        4. record the run (instrument_sync_runs: counts, status, error)
        5. reload the in-memory resolver

    InstrumentResolver
        NSE equities and indices are held in memory (a few thousand rows)
        for O(1) lookups by symbol and token; F&O lookups go to the
        database through indexed columns.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.zerodha.exceptions import KiteError
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.models import INDEX_ALIASES, InstrumentRecord, parse_instrument_dump
from app.core.logging import get_logger
from app.database.models.market_data import BrokerInstrument, InstrumentSyncRun
from app.market_data.base import utcnow

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

BROKER = "ZERODHA"
UPSERT_BATCH = 2000

SessionFactory = Callable[[], AsyncSession]


# --- repository ---------------------------------------------------------------------


def _row(record: InstrumentRecord, now: datetime, run_id: uuid.UUID) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "broker": BROKER,
        "instrument_token": record.instrument_token,
        "exchange_token": record.exchange_token,
        "tradingsymbol": record.tradingsymbol,
        "name": record.name,
        "exchange": record.exchange,
        "segment": record.segment,
        "instrument_type": record.instrument_type,
        "expiry": record.expiry,
        "strike": record.strike,
        "tick_size": record.tick_size,
        "lot_size": record.lot_size,
        "is_active": True,
        "last_updated": now,
        "sync_run_id": run_id,
    }


def _record(row: BrokerInstrument) -> InstrumentRecord:
    return InstrumentRecord(
        instrument_token=int(row.instrument_token),
        exchange_token=int(row.exchange_token) if row.exchange_token is not None else None,
        tradingsymbol=row.tradingsymbol,
        name=row.name,
        expiry=row.expiry,
        strike=float(row.strike) if row.strike is not None else None,
        tick_size=float(row.tick_size),
        lot_size=int(row.lot_size),
        instrument_type=row.instrument_type,
        segment=row.segment,
        exchange=row.exchange,
    )


class InstrumentRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def start_run(self, exchanges: list[str], now: datetime) -> uuid.UUID:
        async with self._session_factory() as s:
            run = InstrumentSyncRun(
                id=uuid.uuid4(),
                broker=BROKER,
                status="RUNNING",
                exchanges=exchanges,
                started_at=now,
            )
            s.add(run)
            await s.commit()
            return run.id

    async def finish_run(self, run_id: uuid.UUID, **fields: Any) -> None:
        async with self._session_factory() as s:
            await s.execute(
                update(InstrumentSyncRun).where(InstrumentSyncRun.id == run_id).values(**fields)
            )
            await s.commit()

    async def upsert(
        self, records: list[InstrumentRecord], run_id: uuid.UUID, now: datetime
    ) -> tuple[int, int]:
        """Insert new tokens, update existing ones. Returns (inserted, updated)."""

        async with self._session_factory() as s:
            before = (
                await s.execute(
                    select(func.count())
                    .select_from(BrokerInstrument)
                    .where(BrokerInstrument.broker == BROKER)
                )
            ).scalar_one()
            for i in range(0, len(records), UPSERT_BATCH):
                rows = [_row(r, now, run_id) for r in records[i : i + UPSERT_BATCH]]
                stmt = insert(BrokerInstrument).values(rows)
                excluded = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_broker_instrument_token",
                    set_={
                        c: getattr(excluded, c)
                        for c in (
                            "exchange_token",
                            "tradingsymbol",
                            "name",
                            "exchange",
                            "segment",
                            "instrument_type",
                            "expiry",
                            "strike",
                            "tick_size",
                            "lot_size",
                            "is_active",
                            "last_updated",
                            "sync_run_id",
                        )
                    },
                )
                await s.execute(stmt)
            await s.commit()
            after = (
                await s.execute(
                    select(func.count())
                    .select_from(BrokerInstrument)
                    .where(BrokerInstrument.broker == BROKER)
                )
            ).scalar_one()
        inserted = after - before
        return inserted, len(records) - inserted

    async def deactivate_missing(self, exchanges: list[str], run_id: uuid.UUID) -> int:
        async with self._session_factory() as s:
            result = await s.execute(
                update(BrokerInstrument)
                .where(
                    BrokerInstrument.broker == BROKER,
                    BrokerInstrument.exchange.in_(exchanges),
                    BrokerInstrument.is_active.is_(True),
                    BrokerInstrument.sync_run_id != run_id,
                )
                .values(is_active=False)
            )
            await s.commit()
            return int(result.rowcount or 0)

    async def last_run(self, *, successful: bool = False) -> InstrumentSyncRun | None:
        async with self._session_factory() as s:
            q = select(InstrumentSyncRun).order_by(InstrumentSyncRun.started_at.desc()).limit(1)
            if successful:
                q = q.where(InstrumentSyncRun.status == "SUCCESS")
            return (await s.execute(q)).scalar_one_or_none()

    async def load_cash_and_indices(self) -> list[InstrumentRecord]:
        async with self._session_factory() as s:
            rows = (
                await s.execute(
                    select(BrokerInstrument).where(
                        BrokerInstrument.broker == BROKER,
                        BrokerInstrument.is_active.is_(True),
                        BrokerInstrument.segment.in_(["NSE", "INDICES", "BSE"]),
                    )
                )
            ).scalars()
            return [_record(r) for r in rows]

    async def find(
        self,
        *,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        token: int | None = None,
        name: str | None = None,
        instrument_type: str | None = None,
        expiry_from: date | None = None,
        expiry: date | None = None,
        strike: float | None = None,
        limit: int = 50,
    ) -> list[InstrumentRecord]:
        conds = [BrokerInstrument.broker == BROKER, BrokerInstrument.is_active.is_(True)]
        if exchange:
            conds.append(BrokerInstrument.exchange == exchange)
        if tradingsymbol:
            conds.append(BrokerInstrument.tradingsymbol == tradingsymbol)
        if token is not None:
            conds.append(BrokerInstrument.instrument_token == token)
        if name:
            conds.append(BrokerInstrument.name == name)
        if instrument_type:
            conds.append(BrokerInstrument.instrument_type == instrument_type)
        if expiry_from:
            conds.append(BrokerInstrument.expiry >= expiry_from)
        if expiry:
            conds.append(BrokerInstrument.expiry == expiry)
        if strike is not None:
            conds.append(BrokerInstrument.strike == strike)
        async with self._session_factory() as s:
            rows = (
                await s.execute(
                    select(BrokerInstrument)
                    .where(and_(*conds))
                    .order_by(BrokerInstrument.expiry.asc().nulls_first(), BrokerInstrument.strike)
                    .limit(limit)
                )
            ).scalars()
            return [_record(r) for r in rows]

    async def count(self) -> int:
        async with self._session_factory() as s:
            return int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(BrokerInstrument)
                        .where(
                            BrokerInstrument.broker == BROKER, BrokerInstrument.is_active.is_(True)
                        )
                    )
                ).scalar_one()
            )


# --- resolver -----------------------------------------------------------------------------


class InstrumentNotFound(KiteError):
    pass


@dataclass
class InstrumentResolver:
    """Fast lookups. NSE / BSE cash and indices in memory; F&O via the repository."""

    repository: InstrumentRepository | None = None
    _by_token: dict[int, InstrumentRecord] = field(default_factory=dict)
    _by_key: dict[tuple[str, str], InstrumentRecord] = field(default_factory=dict)
    loaded_at: datetime | None = None

    def load(self, records: list[InstrumentRecord], now: datetime | None = None) -> int:
        self._by_token = {}
        self._by_key = {}
        for r in records:
            if r.segment not in ("NSE", "INDICES", "BSE"):
                continue
            self._by_token[r.instrument_token] = r
            self._by_key[(r.exchange, r.tradingsymbol)] = r
        self.loaded_at = now or utcnow()
        return len(self._by_token)

    async def reload(self) -> int:
        if self.repository is None:
            return len(self._by_token)
        return self.load(await self.repository.load_cash_and_indices())

    @property
    def size(self) -> int:
        return len(self._by_token)

    def find_by_symbol(self, symbol: str, exchange: str = "NSE") -> InstrumentRecord | None:
        return self._by_key.get((exchange, symbol))

    def find_by_token(self, token: int) -> InstrumentRecord | None:
        return self._by_token.get(token)

    def nse_equities(self) -> list[InstrumentRecord]:
        """Every NSE cash-market record of type EQ (includes ETFs and the
        non-EQ series Kite lists as SYMBOL-BE, SYMBOL-SM, ...; callers filter)."""

        return sorted(
            (
                r
                for r in self._by_token.values()
                if r.segment == "NSE" and r.instrument_type == "EQ"
            ),
            key=lambda r: r.tradingsymbol,
        )

    def resolve_nse_equity(self, symbol: str) -> InstrumentRecord:
        r = self._by_key.get(("NSE", symbol))
        if r is None or r.segment != "NSE" or r.instrument_type != "EQ":
            raise InstrumentNotFound(f"NSE equity {symbol!r} is not in the instrument master")
        return r

    def resolve_index(self, symbol: str) -> InstrumentRecord:
        """App symbols (NIFTY50, BANKNIFTY) or Kite names (NIFTY 50)."""

        name = INDEX_ALIASES.get(symbol.upper(), symbol)
        r = self._by_key.get(("NSE", name))
        if r is None or not r.is_index:
            raise InstrumentNotFound(f"index {symbol!r} is not in the instrument master")
        return r

    def resolve_app_symbol(self, symbol: str) -> InstrumentRecord:
        """An app watchlist symbol: an index alias or an NSE equity."""

        if symbol.upper() in INDEX_ALIASES:
            return self.resolve_index(symbol)
        return self.resolve_nse_equity(symbol)

    async def resolve_nfo_instrument(self, tradingsymbol: str) -> InstrumentRecord:
        if self.repository is None:
            raise InstrumentNotFound("no instrument repository")
        rows = await self.repository.find(exchange="NFO", tradingsymbol=tradingsymbol, limit=1)
        if not rows:
            raise InstrumentNotFound(f"NFO instrument {tradingsymbol!r} not found")
        return rows[0]

    async def resolve_future(
        self, underlying: str, *, expiry: date | None = None, today: date | None = None
    ) -> InstrumentRecord:
        """The future on `underlying` (Kite `name`, e.g. NIFTY, RELIANCE) for
        `expiry`, or the nearest one not yet expired."""

        if self.repository is None:
            raise InstrumentNotFound("no instrument repository")
        rows = await self.repository.find(
            exchange="NFO",
            name=underlying,
            instrument_type="FUT",
            expiry=expiry,
            expiry_from=None if expiry else (today or utcnow().date()),
            limit=1,
        )
        if not rows:
            raise InstrumentNotFound(f"no {underlying} future for expiry {expiry or 'next'}")
        return rows[0]

    async def resolve_option(
        self,
        underlying: str,
        *,
        strike: float,
        option_type: str,
        expiry: date | None = None,
        today: date | None = None,
    ) -> InstrumentRecord:
        if option_type not in ("CE", "PE"):
            raise ValueError("option_type must be CE or PE")
        if self.repository is None:
            raise InstrumentNotFound("no instrument repository")
        rows = await self.repository.find(
            exchange="NFO",
            name=underlying,
            instrument_type=option_type,
            strike=strike,
            expiry=expiry,
            expiry_from=None if expiry else (today or utcnow().date()),
            limit=1,
        )
        if not rows:
            raise InstrumentNotFound(
                f"no {underlying} {strike} {option_type} for expiry {expiry or 'next'}"
            )
        return rows[0]


# --- sync service ------------------------------------------------------------------------------


@dataclass
class SyncResult:
    ok: bool
    run_id: uuid.UUID | None
    rows_fetched: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    inserted: int = 0
    updated: int = 0
    deactivated: int = 0
    error: str | None = None
    invalid_examples: list[str] = field(default_factory=list)


class InstrumentService:
    def __init__(
        self,
        client: KiteClient,
        repository: InstrumentRepository,
        resolver: InstrumentResolver,
        *,
        exchanges: list[str],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.client = client
        self.repository = repository
        self.resolver = resolver
        self.exchanges = exchanges
        self._clock = clock
        self.last_result: SyncResult | None = None
        self.syncing = False
        self._ensure_lock = asyncio.Lock()

    async def ensure_fresh(self, *, refresh_after: time = time(8, 30)) -> bool:
        """Load the master from the database, and download it again when it
        is missing or older than today's dump (Zerodha regenerates it every
        morning, before 08:30 IST). Serialised: concurrent callers share one
        sync. True when an instrument master is available."""

        async with self._ensure_lock:
            if self.resolver.size == 0:
                await self.resolver.reload()
            last = await self.repository.last_run(successful=True)
            now_ist = self._clock().astimezone(IST)
            cutoff = datetime.combine(now_ist.date(), refresh_after, tzinfo=IST)
            if now_ist < cutoff:
                cutoff -= timedelta(days=1)
            stale = last is None or last.finished_at is None or last.finished_at < cutoff
            if self.resolver.size == 0 or stale:
                result = await self.sync_instruments()
                if not result.ok and self.resolver.size == 0:
                    return False
            return self.resolver.size > 0

    async def sync_instruments(self) -> SyncResult:
        if self.syncing:
            return SyncResult(ok=False, run_id=None, error="a sync is already running")
        self.syncing = True
        now = self._clock()
        logger.info("INSTRUMENT_SYNC_STARTED exchanges=%s", ",".join(self.exchanges))
        run_id = await self.repository.start_run(self.exchanges, now)
        result = SyncResult(ok=False, run_id=run_id)
        try:
            records: list[InstrumentRecord] = []
            for exchange in self.exchanges:
                body = await self.client.instruments(exchange)
                parsed = parse_instrument_dump(body, exchanges={exchange})
                result.rows_fetched += parsed.total_rows
                result.rows_invalid += parsed.invalid_rows
                result.invalid_examples.extend(parsed.invalid_examples)
                records.extend(parsed.records)
            if not records:
                raise KiteError("the instrument dump was empty")
            result.rows_valid = len(records)
            result.inserted, result.updated = await self.repository.upsert(records, run_id, now)
            result.deactivated = await self.repository.deactivate_missing(self.exchanges, run_id)
            self.resolver.load(records, self._clock())
            result.ok = True
        except Exception as exc:  # noqa: BLE001 - recorded on the run, the app keeps going
            result.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.warning("INSTRUMENT_SYNC_FAILED %s", result.error)
        finally:
            self.syncing = False
        await self.repository.finish_run(
            run_id,
            status="SUCCESS" if result.ok else "FAILED",
            finished_at=self._clock(),
            rows_fetched=result.rows_fetched,
            rows_valid=result.rows_valid,
            rows_invalid=result.rows_invalid,
            inserted=result.inserted,
            updated=result.updated,
            deactivated=result.deactivated,
            error=result.error,
        )
        if result.ok:
            logger.info(
                "INSTRUMENT_SYNC_COMPLETED valid=%d invalid=%d inserted=%d updated=%d deactivated=%d",
                result.rows_valid,
                result.rows_invalid,
                result.inserted,
                result.updated,
                result.deactivated,
            )
        self.last_result = result
        return result

    async def status(self) -> dict[str, Any]:
        last = await self.repository.last_run()
        ok = await self.repository.last_run(successful=True)
        return {
            "status": (
                "SYNCED"
                if ok is not None and self.resolver.size
                else ("SYNCING" if self.syncing else "NOT_SYNCED")
            ),
            "in_memory": self.resolver.size,
            "last_run_status": last.status if last else None,
            "last_run_at": last.started_at if last else None,
            "last_success_at": ok.finished_at if ok else None,
            "last_error": last.error if last and last.status == "FAILED" else None,
            "rows_valid": ok.rows_valid if ok else 0,
        }
