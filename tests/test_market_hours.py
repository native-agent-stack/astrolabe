"""Fill mode's window: a regular XNYS session from its open to 5 minutes before its close, as
the installed exchange_calendars 4.13.2 states it. The dates below were read from that
calendar on 2026-11-17 before they were asserted here."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from astrolabe import market_hours

REPO = Path(__file__).resolve().parents[1]


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_the_installed_calendar_is_the_pinned_one():
    import exchange_calendars

    assert exchange_calendars.__version__ == "4.13.2"


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        ("2026-11-17T14:30:00", True),  # Tuesday 09:30 New York (EST, UTC-5): the open
        ("2026-11-17T14:29:59", False),  # a second before the open
        ("2026-11-17T20:54:59", True),  # 15:54:59
        ("2026-11-17T20:55:00", False),  # 15:55: five minutes before the 16:00 close
        ("2026-11-17T21:30:00", False),  # after the close
        ("2026-11-14T15:00:00", False),  # Saturday
        ("2026-11-16T03:00:00", False),  # Sunday night in New York
        ("2026-07-13T13:30:00", True),  # Monday 09:30 in summer time (EDT, UTC-4)
        ("2026-07-13T13:29:00", False),
        ("2026-07-13T19:54:59", True),
        ("2026-07-13T19:55:00", False),
    ],
)
def test_regular_sessions(moment, inside):
    assert (market_hours.fill_session(at(moment)) is not None) is inside


def test_thanksgiving_2026_is_no_session():
    for hour in range(24):
        assert market_hours.fill_session(at(f"2026-11-26T{hour:02d}:00:00")) is None


@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24"])
def test_early_closes_at_18_utc(day):
    session = market_hours.fill_session(at(f"{day}T15:00:00"))
    assert session is not None
    assert session.open_utc == at(f"{day}T14:30:00")
    assert session.close_utc == at(f"{day}T18:00:00")
    assert session.fill_window_end_utc == at(f"{day}T17:55:00")
    # A moment that a regular session would admit is outside the early-close window.
    assert market_hours.fill_session(at(f"{day}T17:55:00")) is None
    assert market_hours.fill_session(at(f"{day}T17:54:59")) is not None
    assert market_hours.fill_session(at(f"{day}T19:00:00")) is None


def test_the_session_is_recorded_with_its_open_and_close():
    session = market_hours.fill_session(at("2026-11-17T15:00:00"))
    assert session is not None
    assert session.as_record() == {
        "calendar": "XNYS",
        "session_open_utc": "2026-11-17T14:30:00Z",
        "session_close_utc": "2026-11-17T21:00:00Z",
        "fill_window_end_utc": "2026-11-17T20:55:00Z",
    }


def test_another_time_zone_names_the_same_moment():
    new_york_winter = timezone(timedelta(hours=-5))
    moment = datetime(2026, 11, 17, 9, 30, tzinfo=new_york_winter)
    assert market_hours.fill_session(moment) == market_hours.fill_session(at("2026-11-17T14:30"))


def test_the_new_york_date_decides_the_session():
    # 2026-11-18T02:00Z is still 2026-11-17 in New York, after that day's close.
    assert market_hours.fill_session(at("2026-11-18T02:00:00")) is None


@pytest.mark.parametrize("moment", ["1990-01-02T15:00:00", "2200-01-02T15:00:00"])
def test_a_date_the_calendar_does_not_cover_refuses(moment):
    with pytest.raises(market_hours.MarketHoursError, match="calendar covers"):
        market_hours.fill_session(at(moment))


def test_a_moment_without_a_time_zone_refuses():
    with pytest.raises(market_hours.MarketHoursError, match="time zone"):
        market_hours.fill_session(datetime(2026, 11, 17, 15, 0))


def test_importing_the_module_loads_neither_the_calendar_nor_pandas():
    code = (
        "import sys, astrolabe.market_hours\n"
        "print([m for m in ('exchange_calendars', 'pandas') if m in sys.modules])\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert done.stdout.strip() == "[]"
