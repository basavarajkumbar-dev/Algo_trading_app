"""Universes: official list parsing, cache and refresh, fallbacks, combined
universes without duplicates, exact counts, explicit limits, broker
equities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.universe.loader import (
    UniverseStore,
    UniverseUnavailable,
    broker_equities_from,
    parse_constituent_csv,
    resolve_universe,
)
from app.universe.registry import UNIVERSES

NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"


def _csv(symbols: list[str], series: str = "EQ") -> bytes:
    body = "".join(f"{s} Ltd.,Industry,{s},{series},INE{i:07d}\n" for i, s in enumerate(symbols))
    return (HEADER + body).encode()


class FakeFetch:
    def __init__(self, files: dict[str, bytes] | None = None, fail: bool = False) -> None:
        self.files = files or {}
        self.fail = fail
        self.calls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.fail:
            raise ConnectionError("blocked")
        name = url.rsplit("/", 1)[-1]
        return self.files[name]


def _store(tmp_path: Path, fetch: FakeFetch, now: datetime = NOW) -> UniverseStore:
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    store = UniverseStore(tmp_path, fetch=fetch, clock=lambda: now, sleep=sleep)
    store.sleeps = sleeps  # type: ignore[attr-defined]
    return store


def test_parse_official_csv_any_column_order_and_bom() -> None:
    data = ("﻿Symbol,ISIN Code,Company Name,Series,Industry\n" "TCS,INE1,TCS Ltd.,EQ,IT\n").encode()
    rows = parse_constituent_csv(data)
    assert rows[0].symbol == "TCS" and rows[0].isin == "INE1" and rows[0].industry == "IT"
    with pytest.raises(ValueError):
        parse_constituent_csv(b"Name,Code\nA,1\n")


def test_registry_names_the_real_indices() -> None:
    assert "NIFTY_MIDCAP150" in UNIVERSES and "NIFTY_SMALLCAP250" in UNIVERSES
    assert "NIFTY150" not in UNIVERSES and "NIFTY250" not in UNIVERSES  # no such indices
    assert all(
        u.source_url and u.source_url.startswith("https://www.niftyindices.com/IndexConstituent/")
        for u in UNIVERSES.values()
        if u.kind.value == "INDEX"
    )


async def test_download_cache_and_refresh(tmp_path: Path) -> None:
    fetch = FakeFetch({"ind_nifty100list.csv": _csv([f"S{i}" for i in range(100)])})
    store = _store(tmp_path, fetch)
    first = await store.get("NIFTY100")
    assert first.source == "official" and len(first.constituents) == 100 and first.sha256
    assert (tmp_path / "NIFTY100.json").exists()
    again = await store.get("NIFTY100")
    assert again.source == "official" and len(fetch.calls) == 1  # served from the cache
    later = _store(tmp_path, fetch, NOW + timedelta(days=8))  # older than 7 days
    await later.get("NIFTY100")
    assert len(fetch.calls) == 2


async def test_failed_refresh_falls_back_to_a_stale_cache(tmp_path: Path) -> None:
    ok = FakeFetch({"ind_nifty100list.csv": _csv([f"S{i}" for i in range(100)])})
    await _store(tmp_path, ok).get("NIFTY100")
    down = FakeFetch(fail=True)
    store = _store(tmp_path, down, NOW + timedelta(days=30))
    data = await store.get("NIFTY100")
    assert data.stale and "blocked" in (data.error or "") and len(data.constituents) == 100
    assert len(down.calls) == 3 and store.sleeps == [1.0, 2.0]  # type: ignore[attr-defined]


async def test_no_list_is_ever_guessed(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeFetch(fail=True))
    with pytest.raises(UniverseUnavailable):
        await store.get("NIFTY500")
    nifty = await store.get("NIFTY50")  # the bundled snapshot, clearly flagged
    assert nifty.source == "snapshot" and nifty.stale and len(nifty.constituents) == 50


async def test_a_file_far_from_the_index_size_is_rejected(tmp_path: Path) -> None:
    fetch = FakeFetch({"ind_nifty500list.csv": _csv(["A", "B", "C"])})
    with pytest.raises(UniverseUnavailable):
        await _store(tmp_path, fetch).get("NIFTY500")


async def test_combined_universe_counts_and_exclusions(tmp_path: Path) -> None:
    fetch = FakeFetch(
        {
            "ind_niftybanklist.csv": _csv(["HDFCBANK", "ICICIBANK", "SBIN", "XBANK"]),
            "ind_nifty_privatebanklist.csv": _csv(["HDFCBANK", "ICICIBANK", "YESBANK"]),
        }
    )
    store = _store(tmp_path, fetch)
    report = await resolve_universe(
        store,
        ["NIFTY_BANK", "NIFTY_PRIVATE_BANK"],
        symbols=["TCS", "SBIN"],
        eligibility=lambda s: "not in the instrument master" if s == "XBANK" else None,
        limit=4,
        now=NOW,
    )
    assert report.requested_count == 6  # HDFCBANK ICICIBANK SBIN XBANK YESBANK TCS
    assert report.duplicates_removed == 3  # HDFCBANK, ICICIBANK, SBIN again
    assert report.excluded["XBANK"] == "not in the instrument master"
    assert report.eligible_count == 5
    assert report.processed == ["HDFCBANK", "ICICIBANK", "SBIN", "YESBANK"]
    assert report.truncated and report.excluded["TCS"].startswith("over the configured limit")
    assert report.processed_count + report.excluded_count == report.requested_count


async def test_non_equity_series_are_excluded(tmp_path: Path) -> None:
    fetch = FakeFetch({"ind_niftybanklist.csv": _csv(["AAA"], series="N1")})
    report = await resolve_universe(_store(tmp_path, fetch), ["NIFTY_BANK"], now=NOW)
    assert report.processed == [] and "series N1" in report.excluded["AAA"]


def test_broker_equities_drop_non_eq_series_and_etfs() -> None:
    rows = [
        SimpleNamespace(tradingsymbol="BAJAJ-AUTO", name="BAJAJ AUTO"),
        SimpleNamespace(tradingsymbol="ABC-BE", name="ABC"),
        SimpleNamespace(tradingsymbol="NIFTYBEES", name="NIPPON ETF"),
        SimpleNamespace(tradingsymbol="M&M", name="MAHINDRA"),
    ]
    assert [c.symbol for c in broker_equities_from(rows)] == ["BAJAJ-AUTO", "M&M"]


async def test_broker_universe_needs_an_instrument_master(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeFetch())
    with pytest.raises(UniverseUnavailable):
        await store.get("NSE_ALL_EQ")
    store.set_broker_equities(
        lambda: broker_equities_from([SimpleNamespace(tradingsymbol="TCS", name="TCS")])
    )
    assert (await store.get("NSE_ALL_EQ")).symbols == ["TCS"]


async def test_prefer_cache_never_waits_for_the_network(tmp_path: Path) -> None:
    fetch = FakeFetch(fail=True)
    store = _store(tmp_path, fetch)
    data = await store.get("NIFTY50", prefer_cache=True)
    assert data.source == "snapshot" and fetch.calls == []
