"""Opening range (ORB) from closed candles.

The opening range is the high and low of the first N minutes after the
session opens (09:15 IST for the NSE cash market), for the day of the
latest candle. N is configurable (the brief names 5, 15 and 30).

The simulated feed trades around the clock, so 09:15 IST is just a
point in its continuous stream; with real data it is the true open.
Before the window has fully closed there is no opening range yet.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.market_data.schemas import Candle


class OpeningRange(BaseModel):
    available: bool
    minutes: int
    session_date: date | None = None
    start: datetime | None = None
    end: datetime | None = None
    high: float | None = None
    low: float | None = None
    candles: int = 0
    reason: str


def compute_opening_range(
    candles: list[Candle],
    *,
    minutes: int,
    session_start: time = time(9, 15),
    timezone: str = "Asia/Kolkata",
) -> OpeningRange:
    if not candles:
        return OpeningRange(available=False, minutes=minutes, reason="No candles")
    tz = ZoneInfo(timezone)
    step = candles[-1].timeframe.minutes
    if minutes % step != 0:
        return OpeningRange(
            available=False,
            minutes=minutes,
            reason=f"{minutes}m opening range cannot be built from {step}m candles",
        )

    day = candles[-1].timestamp.astimezone(tz).date()
    start = datetime.combine(day, session_start, tzinfo=tz)
    end = start + timedelta(minutes=minutes)
    window = [c for c in candles if start <= c.timestamp.astimezone(tz) < end]
    needed = minutes // step
    base = OpeningRange(
        available=False,
        minutes=minutes,
        session_date=day,
        start=start,
        end=end,
        candles=len(window),
        reason="",
    )
    if candles[-1].timestamp.astimezone(tz) + timedelta(minutes=step) <= end:
        base.reason = f"The {minutes}m opening range from {session_start:%H:%M} has not closed yet"
        return base
    if len(window) < needed:
        base.reason = f"Only {len(window)} of {needed} opening-range candles available"
        return base
    base.available = True
    base.high = max(c.high for c in window)
    base.low = min(c.low for c in window)
    base.reason = f"{minutes}m opening range {base.low:g}-{base.high:g}"
    return base
