"""Constituent lists: download, cache, refresh, resolve.

Lists come from the official index files (registry.py). The backend fetches
them with a bounded number of retries and exponential backoff, validates
them, and caches them on disk (`UNIVERSE_CACHE_DIR`). A cached list is
reused until it is older than `max_age` (7 days by default); if a refresh
fails the last good cache is used and flagged stale. With no cache at all:

* NIFTY 50 falls back to the bundled September 2026 snapshot
  (`app.market_data.universe`), flagged ``source="snapshot"``;
* every other index is reported as unavailable. No list is ever guessed.

`resolve_universe` turns requested universes (plus explicit symbols) into
the exact symbols a scan or backtest will process, and reports every count
and every exclusion with its reason: nothing is truncated silently.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_SYMBOL
from app.universe.registry import UNIVERSES, UniverseDefinition, UniverseKind, get_definition

logger = get_logger(__name__)

Fetcher = Callable[[str], Awaitable[bytes]]
#: Returns why a symbol cannot be processed (no instrument, no data, ...)
#: or None when it is eligible.
Eligibility = Callable[[str], str | None]

DEFAULT_MAX_AGE = timedelta(days=7)
SNAPSHOT_DATE = "2026-09"


class UniverseUnavailable(RuntimeError):
    """No official list, no cache and no snapshot for a universe."""


class Constituent(BaseModel):
    symbol: str
    name: str | None = None
    industry: str | None = None
    series: str | None = None
    isin: str | None = None


class ConstituentList(BaseModel):
    universe_id: str
    name: str
    constituents: list[Constituent]
    source: str  # "official" (downloaded now) | "cache" | "snapshot" | "instrument_master"
    source_url: str | None = None
    fetched_at: datetime | None = None
    sha256: str | None = None
    stale: bool = False
    error: str | None = None
    expected_size: int | None = None

    @property
    def symbols(self) -> list[str]:
        return [c.symbol for c in self.constituents]


class UniverseSource(BaseModel):
    universe_id: str
    name: str
    source: str
    fetched_at: datetime | None
    stale: bool
    error: str | None
    size: int
    expected_size: int | None


class UniverseReport(BaseModel):
    """Exactly what a scan or backtest was asked for and what it processed."""

    requested_universes: list[str] = Field(default_factory=list)
    requested_symbols: list[str] = Field(default_factory=list)  # given explicitly
    requested_count: int  # unique symbols requested (universes + explicit)
    duplicates_removed: int
    eligible_count: int
    excluded: dict[str, str] = Field(default_factory=dict)  # symbol -> reason
    excluded_count: int
    processed: list[str] = Field(default_factory=list)
    processed_count: int
    limit: int | None = None
    truncated: bool = False  # True when the limit excluded eligible symbols
    sources: list[UniverseSource] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    resolved_at: datetime


def parse_constituent_csv(data: bytes) -> list[Constituent]:
    """Parse an NSE Indices constituent file (columns Company Name,
    Industry, Symbol, Series, ISIN Code). Column order is not assumed."""

    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("empty file")
    fields = {f.strip().lower(): f for f in reader.fieldnames}
    if "symbol" not in fields:
        raise ValueError(f"no Symbol column (columns: {', '.join(reader.fieldnames)})")

    def col(row: dict[str, str], key: str) -> str | None:
        name = fields.get(key)
        value = (row.get(name) or "").strip() if name else ""
        return value or None

    out: list[Constituent] = []
    seen: set[str] = set()
    for row in reader:
        symbol = (col(row, "symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(
            Constituent(
                symbol=symbol,
                name=col(row, "company name"),
                industry=col(row, "industry"),
                series=col(row, "series"),
                isin=col(row, "isin code"),
            )
        )
    if not out:
        raise ValueError("no constituents in the file")
    return out


async def http_fetch(url: str) -> bytes:  # pragma: no cover - network
    import httpx

    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (GainzAlgo universe loader)", "Accept": "text/csv"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


class UniverseStore:
    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        fetch: Fetcher | None = http_fetch,
        max_age: timedelta = DEFAULT_MAX_AGE,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        broker_equities: Callable[[], list[Constituent]] | None = None,
    ) -> None:
        root = cache_dir or os.environ.get("UNIVERSE_CACHE_DIR") or "/tmp/gainzalgo-universes"
        self.cache_dir = Path(root)
        self._fetch = fetch
        self.max_age = max_age
        self.retries = max(retries, 1)
        self.backoff = backoff_seconds
        self._clock = clock
        self._sleep = sleep
        self._broker_equities = broker_equities
        self._memory: dict[str, ConstituentList] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def set_broker_equities(self, provider: Callable[[], list[Constituent]] | None) -> None:
        self._broker_equities = provider

    # --- cache -----------------------------------------------------------------------

    def _path(self, universe_id: str) -> Path:
        return self.cache_dir / f"{universe_id}.json"

    def _read_cache(self, universe_id: str) -> ConstituentList | None:
        path = self._path(universe_id)
        if not path.exists():
            return None
        try:
            return ConstituentList.model_validate_json(path.read_text())
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable universe cache %s", path)
            return None

    def _write_cache(self, data: ConstituentList) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(data.universe_id).with_suffix(".tmp")
        tmp.write_text(data.model_dump_json())
        tmp.replace(self._path(data.universe_id))

    def _fresh(self, data: ConstituentList) -> bool:
        return (
            data.fetched_at is not None
            and data.source in ("official", "cache")
            and self._clock() - data.fetched_at < self.max_age
        )

    # --- loading ---------------------------------------------------------------------

    async def get(
        self, universe_id: str, *, refresh: bool = False, prefer_cache: bool = False
    ) -> ConstituentList:
        """`prefer_cache` (startup): use any cached copy, even an old one
        (flagged stale), or the NIFTY 50 snapshot, before downloading, so a
        start never waits on the network for a list it already has."""

        definition = get_definition(universe_id)
        if definition.kind == UniverseKind.BROKER_EQUITIES:
            return self._broker_list(definition)
        lock = self._locks.setdefault(definition.id, asyncio.Lock())
        async with lock:  # one download per universe at a time
            cached = self._memory.get(definition.id) or self._read_cache(definition.id)
            if cached is not None and not refresh and self._fresh(cached):
                self._memory[definition.id] = cached
                return cached
            if prefer_cache and not refresh:
                if cached is not None:
                    return cached.model_copy(update={"stale": True})
                if definition.id == "NIFTY50":
                    return self._snapshot(definition, "no cached copy yet")
            try:
                fresh = await self._download(definition)
            except Exception as exc:  # noqa: BLE001 - fall back, never guess
                error = f"{type(exc).__name__}: {exc}"
                logger.warning("UNIVERSE_REFRESH_FAILED %s: %s", definition.id, error)
                if cached is not None:
                    stale = cached.model_copy(update={"stale": True, "error": error})
                    self._memory[definition.id] = stale
                    return stale
                if definition.id == "NIFTY50":
                    return self._snapshot(definition, error)
                raise UniverseUnavailable(
                    f"{definition.name}: the official list could not be downloaded "
                    f"({error}) and no cached copy exists"
                ) from exc
            self._write_cache(fresh)
            self._memory[definition.id] = fresh
            return fresh

    async def _download(self, definition: UniverseDefinition) -> ConstituentList:
        if self._fetch is None or definition.source_url is None:
            raise UniverseUnavailable("downloads are disabled")
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                data = await self._fetch(definition.source_url)
                constituents = parse_constituent_csv(data)
                if definition.expected_size and abs(
                    len(constituents) - definition.expected_size
                ) > max(2, definition.expected_size // 10):
                    raise ValueError(
                        f"{len(constituents)} constituents, expected about "
                        f"{definition.expected_size}"
                    )
                logger.info(
                    "UNIVERSE_REFRESHED %s constituents=%d", definition.id, len(constituents)
                )
                return ConstituentList(
                    universe_id=definition.id,
                    name=definition.name,
                    constituents=constituents,
                    source="official",
                    source_url=definition.source_url,
                    fetched_at=self._clock(),
                    sha256=hashlib.sha256(data).hexdigest(),
                    expected_size=definition.expected_size,
                )
            except Exception as exc:  # noqa: BLE001 - retried below
                last = exc
                if attempt + 1 < self.retries:
                    await self._sleep(self.backoff * (2**attempt))
        assert last is not None
        raise last

    def _snapshot(self, definition: UniverseDefinition, error: str) -> ConstituentList:
        return ConstituentList(
            universe_id=definition.id,
            name=definition.name,
            constituents=[
                Constituent(symbol=i.symbol, name=i.name, industry=i.sector, series="EQ")
                for i in NIFTY_50_UNIVERSE
            ],
            source="snapshot",
            source_url=definition.source_url,
            fetched_at=None,
            stale=True,
            error=f"using the bundled {SNAPSHOT_DATE} snapshot: {error}",
            expected_size=50,
        )

    def _broker_list(self, definition: UniverseDefinition) -> ConstituentList:
        rows = self._broker_equities() if self._broker_equities is not None else []
        if not rows:
            raise UniverseUnavailable(
                f"{definition.name}: no instrument master loaded (log in to Kite once so the "
                "instrument sync can run)"
            )
        return ConstituentList(
            universe_id=definition.id,
            name=definition.name,
            constituents=rows,
            source="instrument_master",
            fetched_at=self._clock(),
        )

    async def status(self) -> list[dict[str, Any]]:
        out = []
        for definition in UNIVERSES.values():
            cached = self._memory.get(definition.id) or (
                self._read_cache(definition.id) if definition.kind == UniverseKind.INDEX else None
            )
            out.append(
                {
                    **definition.model_dump(mode="json"),
                    "cached": cached is not None,
                    "size": len(cached.constituents) if cached else None,
                    "source": cached.source if cached else None,
                    "fetched_at": (
                        cached.fetched_at.isoformat() if cached and cached.fetched_at else None
                    ),
                    "stale": (not self._fresh(cached)) if cached else None,
                }
            )
        return out


async def resolve_universe(
    store: UniverseStore,
    universe_ids: Iterable[str] = (),
    *,
    symbols: Iterable[str] = (),
    eligibility: Eligibility | None = None,
    limit: int | None = None,
    now: datetime | None = None,
    prefer_cache: bool = False,
    notes: Iterable[str] = (),
) -> UniverseReport:
    """Union of the requested universes and explicit symbols, deduplicated
    in request order, filtered by `eligibility`, then capped at `limit`
    (every symbol the cap leaves out is listed with that reason)."""

    ids = [get_definition(u).id for u in universe_ids]
    ordered: list[str] = []
    seen: set[str] = set()
    total = 0
    excluded: dict[str, str] = {}
    sources: list[UniverseSource] = []
    for universe_id in ids:
        data = await store.get(universe_id, prefer_cache=prefer_cache)
        sources.append(
            UniverseSource(
                universe_id=data.universe_id,
                name=data.name,
                source=data.source,
                fetched_at=data.fetched_at,
                stale=data.stale,
                error=data.error,
                size=len(data.constituents),
                expected_size=data.expected_size,
            )
        )
        for c in data.constituents:
            total += 1
            if c.symbol in seen:
                continue
            seen.add(c.symbol)
            ordered.append(c.symbol)
            if c.series and c.series.upper() not in ("EQ", "BE", "BZ"):
                excluded[c.symbol] = f"series {c.series} is not a cash equity series"
    explicit = [s.strip().upper() for s in symbols if s and s.strip()]
    for s in explicit:
        total += 1
        if s not in seen:
            seen.add(s)
            ordered.append(s)

    eligible: list[str] = []
    for s in ordered:
        if s in excluded:
            continue
        if s == NIFTY_INDEX_SYMBOL:
            excluded[s] = "the NIFTY 50 index is market context, not a tradable symbol"
            continue
        reason = eligibility(s) if eligibility is not None else None
        if reason:
            excluded[s] = reason
            continue
        eligible.append(s)

    processed = eligible
    truncated = False
    if limit is not None and len(eligible) > limit:
        processed = eligible[:limit]
        truncated = True
        for s in eligible[limit:]:
            excluded[s] = f"over the configured limit of {limit} symbols"
    return UniverseReport(
        requested_universes=ids,
        requested_symbols=explicit,
        requested_count=len(ordered),
        duplicates_removed=total - len(ordered),
        eligible_count=len(eligible),
        excluded=excluded,
        excluded_count=len(excluded),
        processed=processed,
        processed_count=len(processed),
        limit=limit,
        truncated=truncated,
        sources=sources,
        notes=list(notes),
        resolved_at=now or datetime.now(UTC),
    )


def report_summary(report: UniverseReport) -> str:
    text = (
        f"requested {report.requested_count}, eligible {report.eligible_count}, "
        f"excluded {report.excluded_count}, processed {report.processed_count}"
    )
    if report.truncated:
        text += f" (capped at {report.limit})"
    return text


def dump_report(report: UniverseReport) -> dict[str, Any]:
    return json.loads(report.model_dump_json())


__all__ = [
    "Constituent",
    "broker_equities_from",
    "ConstituentList",
    "UniverseReport",
    "UniverseSource",
    "UniverseStore",
    "UniverseUnavailable",
    "dump_report",
    "parse_constituent_csv",
    "report_summary",
    "resolve_universe",
]


#: Kite lists non-regular series as SYMBOL-<SERIES>; intraday (MIS) equity
#: trading is in the EQ series, so these are not eligible equities.
NON_EQ_SUFFIXES = frozenset(
    {"BE", "BL", "BT", "BZ", "GB", "GS", "IL", "IQ", "IT", "MF", "RR", "SG", "SM", "ST", "TB"}
)


def broker_equities_from(records: Iterable[Any]) -> list[Constituent]:
    """NSE equities from instrument-master records (InstrumentRecord-like:
    tradingsymbol, name). Excludes non-EQ series and exchange-traded funds."""

    out: list[Constituent] = []
    for r in records:
        symbol = str(r.tradingsymbol).upper()
        suffix = symbol.rsplit("-", 1)[-1] if "-" in symbol else ""
        if suffix in NON_EQ_SUFFIXES:
            continue
        name = (r.name or "").upper()
        if symbol.endswith(("ETF", "BEES")) or " ETF" in name or name.endswith("ETF"):
            continue
        out.append(Constituent(symbol=symbol, name=r.name, series="EQ"))
    return out
