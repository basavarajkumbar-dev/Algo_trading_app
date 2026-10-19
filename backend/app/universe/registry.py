"""Stock universes: which symbols a scan or backtest covers.

Index membership is never invented here. Each NSE index universe names its
official constituent file on niftyindices.com (NSE Indices Ltd, the index
provider). The files are downloaded by the backend at run time, cached on
disk and refreshed on a schedule (see `loader.py`). The URLs were checked
to exist on 2026-10-19.

Index definitions (NSE Indices methodology):

* NIFTY 100 = NIFTY 50 + NIFTY Next 50.
* There is no index called "NIFTY 150" or "NIFTY 250". The nearest official
  indices are NIFTY Midcap 150 (ranks 101-250 by full market cap),
  NIFTY Smallcap 250 (ranks 251-500) and NIFTY LargeMidcap 250
  (NIFTY 100 + NIFTY Midcap 150). They are offered under their real names.
* Constituents change at the semi-annual rebalances (effective end of March
  and end of September) and on corporate events in between, so lists are
  re-downloaded weekly by default.

`NSE_ALL_EQ` is not an index: it is every active NSE cash-market equity in
the broker's instrument master (Zerodha), refreshed daily with it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

NIFTY_INDICES_BASE = "https://www.niftyindices.com/IndexConstituent"


class UniverseKind(str, Enum):
    INDEX = "INDEX"  # official index constituent file
    BROKER_EQUITIES = "BROKER_EQUITIES"  # every NSE equity in the instrument master


class UniverseDefinition(BaseModel):
    id: str
    name: str
    kind: UniverseKind
    source_url: str | None = None
    expected_size: int | None = None  # as defined by the index methodology
    group: str  # "Broad market", "Size", "Sector", "All"
    notes: str = ""


def _index(
    id_: str, name: str, file: str, size: int | None, group: str, notes: str = ""
) -> UniverseDefinition:
    return UniverseDefinition(
        id=id_,
        name=name,
        kind=UniverseKind.INDEX,
        source_url=f"{NIFTY_INDICES_BASE}/{file}",
        expected_size=size,
        group=group,
        notes=notes,
    )


UNIVERSES: dict[str, UniverseDefinition] = {
    u.id: u
    for u in (
        _index("NIFTY50", "NIFTY 50", "ind_nifty50list.csv", 50, "Broad market"),
        _index("NIFTY_NEXT50", "NIFTY Next 50", "ind_niftynext50list.csv", 50, "Broad market"),
        _index(
            "NIFTY100",
            "NIFTY 100",
            "ind_nifty100list.csv",
            100,
            "Broad market",
            "NIFTY 50 + NIFTY Next 50",
        ),
        _index("NIFTY200", "NIFTY 200", "ind_nifty200list.csv", 200, "Broad market"),
        _index("NIFTY500", "NIFTY 500", "ind_nifty500list.csv", 500, "Broad market"),
        _index("NIFTY_MIDCAP100", "NIFTY Midcap 100", "ind_niftymidcap100list.csv", 100, "Size"),
        _index(
            "NIFTY_MIDCAP150",
            "NIFTY Midcap 150",
            "ind_niftymidcap150list.csv",
            150,
            "Size",
            "Offered for the requested 'NIFTY 150': no index of that name exists",
        ),
        _index(
            "NIFTY_SMALLCAP100",
            "NIFTY Smallcap 100",
            "ind_niftysmallcap100list.csv",
            100,
            "Size",
        ),
        _index(
            "NIFTY_SMALLCAP250",
            "NIFTY Smallcap 250",
            "ind_niftysmallcap250list.csv",
            250,
            "Size",
            "Offered for the requested 'NIFTY 250': no index of that name exists",
        ),
        _index(
            "NIFTY_LARGEMIDCAP250",
            "NIFTY LargeMidcap 250",
            "ind_niftylargemidcap250list.csv",
            250,
            "Size",
            "NIFTY 100 + NIFTY Midcap 150",
        ),
        _index(
            "NIFTY_MICROCAP250",
            "NIFTY Microcap 250",
            "ind_niftymicrocap250_list.csv",
            250,
            "Size",
        ),
        _index("NIFTY_BANK", "NIFTY Bank", "ind_niftybanklist.csv", None, "Sector"),
        _index(
            "NIFTY_PRIVATE_BANK",
            "NIFTY Private Bank",
            "ind_nifty_privatebanklist.csv",
            None,
            "Sector",
        ),
        UniverseDefinition(
            id="NSE_ALL_EQ",
            name="All NSE equities (broker instrument master)",
            kind=UniverseKind.BROKER_EQUITIES,
            group="All",
            notes=(
                "Every active NSE EQ-series instrument in the Zerodha instrument master. "
                "Needs a completed instrument sync (a Kite login)."
            ),
        ),
    )
}


def get_definition(universe_id: str) -> UniverseDefinition:
    try:
        return UNIVERSES[universe_id.upper()]
    except KeyError as exc:
        raise KeyError(
            f"Unknown universe {universe_id!r}; known: {', '.join(sorted(UNIVERSES))}"
        ) from exc


__all__ = [
    "NIFTY_INDICES_BASE",
    "UNIVERSES",
    "UniverseDefinition",
    "UniverseKind",
    "get_definition",
]
