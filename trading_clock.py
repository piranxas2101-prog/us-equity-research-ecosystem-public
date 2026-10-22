"""The New York Stock Exchange's clock: sessions, holidays, early closes, and time measured in trading minutes.

Owner 2026-10-23: the agents decide how long each position stays open - five hours, three days, a week - and the
monitors close it whenever its rules say so. A position's horizon is therefore counted in regular-session minutes
from its fill (one session = 390 minutes): a one-session position filled at 11:00 ends at 11:00 of the next
session, skipping nights, weekends and holidays, and an early close (13:00, e.g. the day after Thanksgiving) is a
short session, not a 15:55 flat after the market has closed.

The calendar is ``exchange_calendars`` (XNYS, Apache-2.0, added to the lock 2026-10-23). If it cannot be imported
the clock falls back to weekdays 09:30-16:00 and says so (``CALENDAR``): a holiday then counts as a session, so a
horizon can end early, never late.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")
SESSION_MINUTES = 390

try:
    import exchange_calendars as _xc

    CALENDAR = "exchange_calendars XNYS"
except ImportError:  # pragma: no cover - the locked runtime has it
    _xc = None
    CALENDAR = "weekdays 09:30-16:00 (exchange_calendars not installed)"


@lru_cache(maxsize=1)
def _calendar():
    return _xc.get_calendar("XNYS") if _xc is not None else None


def _eastern(moment: datetime) -> pd.Timestamp:
    stamp = pd.Timestamp(moment)
    return stamp.tz_localize(EASTERN) if stamp.tzinfo is None else stamp.tz_convert(EASTERN)


def session(day: date) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(open, close) of the regular session on ``day`` in New York time, or None when the exchange is closed."""
    calendar = _calendar()
    if calendar is None:
        if day.weekday() >= 5:
            return None
        start = pd.Timestamp(datetime.combine(day, time(9, 30)), tz=EASTERN)
        return start, start + pd.Timedelta(minutes=SESSION_MINUTES)
    label = pd.Timestamp(day)
    if not calendar.is_session(label):
        return None
    return calendar.session_open(label).tz_convert(EASTERN), calendar.session_close(label).tz_convert(EASTERN)


def minutes_to_close(moment: datetime) -> int | None:
    """Trading minutes left in today's session at ``moment`` (0 after the close), None on a closed day."""
    now = _eastern(moment)
    hours = session(now.date())
    if hours is None:
        return None
    return int(max(0.0, (hours[1] - max(now, hours[0])).total_seconds() // 60))


def trading_minutes_between(start: datetime, end: datetime) -> int:
    """Regular-session minutes between two moments."""
    begin, finish = _eastern(start), _eastern(end)
    total, day = 0, begin.date()
    while day <= finish.date():
        hours = session(day)
        if hours is not None:
            low, high = max(begin, hours[0]), min(finish, hours[1])
            if high > low:
                total += int((high - low).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def add_trading_minutes(start: datetime, minutes: int) -> pd.Timestamp:
    """The moment ``minutes`` regular-session minutes after ``start`` (a fill outside the session starts counting
    at the next open)."""
    moment, left, day = _eastern(start), int(minutes), _eastern(start).date()
    for _ in range(400):  # about a year and a half of calendar days: far beyond the longest horizon
        hours = session(day)
        if hours is not None:
            begin = max(moment, hours[0])
            if begin < hours[1]:
                available = int((hours[1] - begin).total_seconds() // 60)
                if left <= available:
                    return begin + pd.Timedelta(minutes=left)
                left -= available
        day += timedelta(days=1)
    raise ValueError("horizon beyond the calendar")
