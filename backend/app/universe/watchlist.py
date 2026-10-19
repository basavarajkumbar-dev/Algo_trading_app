"""The live watchlist from the configured universes (SCANNER_UNIVERSES)."""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_SYMBOL
from app.universe.loader import (
    Eligibility,
    UniverseReport,
    UniverseStore,
    UniverseUnavailable,
    report_summary,
    resolve_universe,
)

logger = get_logger(__name__)

_SIMULATED = {i.symbol for i in NIFTY_50_UNIVERSE}


def simulated_eligibility(symbol: str) -> str | None:
    if symbol in _SIMULATED:
        return None
    return "no simulated data: the mock feed covers the NIFTY 50 snapshot only"


def instrument_eligibility(resolver: object) -> tuple[Eligibility | None, list[str]]:
    """Zerodha: a symbol must be an NSE equity in the instrument master.
    Without a synced master nothing can be verified yet: every symbol is
    kept and the broker feed reports any it cannot subscribe."""

    size = int(getattr(resolver, "size", 0) or 0)
    if size == 0:
        return None, [
            "Instrument master not loaded yet: symbols are not verified until the first "
            "Kite login syncs it (unresolved symbols are then reported by the feed)."
        ]

    def check(symbol: str) -> str | None:
        r = resolver.find_by_symbol(symbol)  # type: ignore[attr-defined]
        if r is None:
            return "not in the broker's instrument master (renamed, suspended or delisted?)"
        if r.instrument_type != "EQ" or r.segment != "NSE":
            return f"not an NSE equity ({r.segment} {r.instrument_type})"
        return None

    return check, []


async def build_watchlist(
    store: UniverseStore,
    universe_ids: list[str],
    *,
    eligibility: Eligibility | None,
    limit: int,
    notes: list[str] | None = None,
    timeout_seconds: float = 90.0,
) -> tuple[list[str], UniverseReport]:
    """[NIFTY 50 index, *processed symbols] and the full report. If the
    requested universes cannot be loaded, the NIFTY 50 (cache or bundled
    snapshot) is used instead and the report says why."""

    notes = list(notes or [])
    try:
        report = await asyncio.wait_for(
            resolve_universe(
                store,
                universe_ids,
                eligibility=eligibility,
                limit=limit,
                prefer_cache=True,
                notes=notes,
            ),
            timeout=timeout_seconds,
        )
    except (UniverseUnavailable, KeyError, TimeoutError) as exc:
        logger.error("UNIVERSE_UNAVAILABLE %s: %s (falling back to NIFTY 50)", universe_ids, exc)
        notes.append(
            f"Requested universes {', '.join(universe_ids)} unavailable ({exc}); "
            "scanning the NIFTY 50 instead"
        )
        report = await resolve_universe(
            store, ["NIFTY50"], eligibility=eligibility, limit=limit, prefer_cache=True, notes=notes
        )
    logger.info("Scanner universe %s: %s", report.requested_universes, report_summary(report))
    return [NIFTY_INDEX_SYMBOL, *report.processed], report


__all__ = ["build_watchlist", "instrument_eligibility", "simulated_eligibility"]
