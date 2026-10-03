"""Download Kite 5-minute history for a backtest (Phase 13).

Writes `<dir>/<SYMBOL>.csv` (timestamp,open,high,low,close,volume; timestamp
= candle start, ISO 8601 with offset) for the requested symbols plus the
NIFTY 50 benchmark, covering the run plus its warm-up days. The directory
is keyed by the date range and symbols, so a repeated run reuses it; the
current trading day is never cached as complete.

Rate limits are respected by the shared client (3 historical requests/s);
the 90-day limit per 5-minute request is handled by chunking.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.brokers.zerodha.historical import ZerodhaHistoricalDataProvider
from app.brokers.zerodha.models import KiteInterval
from app.core.logging import get_logger
from app.market_data.historical import HistoricalDataError
from app.market_data.universe import NIFTY_INDEX_SYMBOL

logger = get_logger(__name__)

ProgressFn = Callable[[float, str], None]
COVERAGE_FILE = "coverage.json"


def cache_dir_for(
    root: Path, symbols: list[str], start: date, end: date, today: date | None = None
) -> Path:
    key = ",".join(sorted({*symbols, NIFTY_INDEX_SYMBOL}))
    digest = hashlib.sha256(key.encode()).hexdigest()[:10]
    live = "-partial" if today is not None and end >= today else ""
    return root / "zerodha" / f"{start.isoformat()}_{end.isoformat()}_{digest}{live}"


async def download_history(
    provider: ZerodhaHistoricalDataProvider,
    symbols: list[str],
    start: date,
    end: date,
    root: Path,
    *,
    warmup_days: int = 15,
    progress: ProgressFn | None = None,
) -> Path:
    report = progress or (lambda _p, _m: None)
    zone = provider.session.zone
    fetch_start = start - timedelta(days=warmup_days * 7 // 5 + 7)
    today = datetime.now(zone).date()
    target = cache_dir_for(root, symbols, start, end, today)
    wanted = sorted({*symbols, NIFTY_INDEX_SYMBOL})
    if (
        target.is_dir()
        and (target / COVERAGE_FILE).exists()
        and all((target / f"{s}.csv").exists() for s in wanted)
        and not str(target).endswith("-partial")
    ):
        report(0.05, "Using cached Zerodha history")
        return target
    target.mkdir(parents=True, exist_ok=True)
    begin = datetime.combine(fetch_start, time(0, 0), tzinfo=zone)
    finish = datetime.combine(end, time(23, 59, 59), tzinfo=zone)
    for i, symbol in enumerate(wanted, start=1):
        report(
            0.05 * i / len(wanted), f"Downloading Zerodha 5m history {i}/{len(wanted)}: {symbol}"
        )
        try:
            rows = await provider.fetch_rows(symbol, KiteInterval.MINUTE_5, begin, finish)
        except HistoricalDataError as exc:
            raise HistoricalDataError(f"{symbol}: {exc}") from exc
        tmp = target / f".{symbol}.csv.tmp"
        with tmp.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for ts, o, h, lo, c, v in rows:
                w.writerow([ts.isoformat(), o, h, lo, c, v])
        tmp.replace(target / f"{symbol}.csv")
    # The range Kite was asked for. A weekday inside it without index candles
    # was an exchange holiday (e.g. 2 Oct, Gandhi Jayanti) - including at the
    # ends of the range - not missing data. Written last: a cache directory
    # without it is incomplete and gets downloaded again.
    (target / COVERAGE_FILE).write_text(
        json.dumps({"first": fetch_start.isoformat(), "last": end.isoformat()})
    )
    logger.info(
        "Zerodha history for %d symbols (%s..%s) written to %s",
        len(wanted),
        fetch_start,
        end,
        target,
    )
    return target


__all__ = ["COVERAGE_FILE", "cache_dir_for", "download_history"]
