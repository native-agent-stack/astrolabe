"""
When fill mode may run: inside a regular XNYS session, ending 5 minutes before its close.

Own design, on exchange_calendars 4.13.2 (pinned in pyproject.toml). The calendar knows the
exchange's holidays and early closes; fill mode's 90-second run then never meets a close.
The library is imported inside the function, so importing this module stays cheap and loads
neither it nor pandas.

The calendar covers a bounded range of dates: by default from 20 years before the day it is
built to one year after (exchange_calendars 4.13.2, exchange_calendar.py:59-62, read
2026-11-17). A moment outside that range refuses with ``MarketHoursError``; it never passes.

Facts read from the installed calendar on 2026-11-17 (and asserted in
tests/test_market_hours.py): 2026-11-26 (Thanksgiving) is not a session; 2026-11-27 and
2026-12-24 close at 18:00 UTC (13:00 New York); a regular session opens 14:30 UTC and closes
21:00 UTC in winter time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

CALENDAR = "XNYS"
MARKET_TZ = "America/New_York"
# Fill mode stops taking new runs this long before the session's close.
CLOSE_MARGIN = timedelta(minutes=5)


class MarketHoursError(RuntimeError):
    """The calendar cannot say whether this moment is inside a session."""


@dataclass(frozen=True)
class Session:
    """One XNYS session, in UTC, and the end of the window in which fill mode may start."""

    open_utc: datetime
    close_utc: datetime

    @property
    def fill_window_end_utc(self) -> datetime:
        return self.close_utc - CLOSE_MARGIN

    def as_record(self) -> dict[str, str]:
        def utc(moment: datetime) -> str:
            return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

        return {
            "calendar": CALENDAR,
            "session_open_utc": utc(self.open_utc),
            "session_close_utc": utc(self.close_utc),
            "fill_window_end_utc": utc(self.fill_window_end_utc),
        }


def fill_session(now: datetime) -> Session | None:
    """The XNYS session ``now`` falls in, when ``now`` is at or after its open and before its
    close less ``CLOSE_MARGIN``; None otherwise (no session that day, before the open, or too
    near or after the close). ``now`` must carry a time zone.

    Raises ``MarketHoursError`` when ``now`` is naive or its New York date lies outside the
    calendar's range.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise MarketHoursError("the moment to check has no time zone")
    import exchange_calendars
    import pandas as pd

    # get_calendar, first_session, last_session, is_session, session_open, session_close:
    # exchange_calendars 4.13.2, calendar_utils.py and exchange_calendar.py:945-1012, 1263,
    # read 2026-11-17. Session labels are dates without a time zone; opens and closes are UTC.
    calendar = exchange_calendars.get_calendar(CALENDAR)
    moment = pd.Timestamp(now).tz_convert("UTC")
    # The session label: the New York date of the moment, as an ISO date string.
    day = moment.tz_convert(MARKET_TZ).date().isoformat()
    first, last = (
        calendar.first_session.date().isoformat(),
        calendar.last_session.date().isoformat(),
    )
    if not first <= day <= last:
        raise MarketHoursError(f"the {CALENDAR} calendar covers {first} to {last}, not {day}")
    if not calendar.is_session(day):
        return None
    session = Session(
        open_utc=calendar.session_open(day).to_pydatetime(),
        close_utc=calendar.session_close(day).to_pydatetime(),
    )
    if session.open_utc <= moment.to_pydatetime() < session.fill_window_end_utc:
        return session
    return None
