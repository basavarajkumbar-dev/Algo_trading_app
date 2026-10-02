"""NSE cash-market session calendar for backtests (IST).

Sessions run 09:15-15:30 IST, Monday to Friday. A 5-minute bar is
identified by its start time, so a session has 75 bars, 09:15 ... 15:25.
Exchange holidays are not built in: pass them explicitly (or they come
from the data itself when a CSV source has no bars on a day).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
BAR_MINUTES = 5
BARS_PER_SESSION = 75  # (15:30 - 09:15) / 5


def trading_days(start: date, end: date, holidays: Iterable[date] = ()) -> list[date]:
    """Weekdays in [start, end] that are not holidays."""

    skip = set(holidays)
    days: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            days.append(d)
        d += timedelta(days=1)
    return days


def bar_starts(day: date, minutes: int = BAR_MINUTES) -> list[datetime]:
    """UTC start times of every bar in one session."""

    open_ist = datetime.combine(day, SESSION_OPEN, tzinfo=IST)
    count = (BARS_PER_SESSION * BAR_MINUTES) // minutes
    return [(open_ist + timedelta(minutes=minutes * i)).astimezone(UTC) for i in range(count)]


def session_day(ts: datetime) -> date:
    return ts.astimezone(IST).date()


def in_session(ts: datetime) -> bool:
    local = ts.astimezone(IST)
    return local.weekday() < 5 and SESSION_OPEN <= local.time() < SESSION_CLOSE
