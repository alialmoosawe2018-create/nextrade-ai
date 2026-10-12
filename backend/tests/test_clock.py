from datetime import date

import pandas as pd
import pytest

from app.core.clock import BAGHDAD, MarketCalendar, to_local


@pytest.fixture(scope="module")
def cal():
    return MarketCalendar()


def test_dst_transition_moves_open_in_utc(cal):
    # US DST ends Sunday 2026-11-01.
    assert cal.session(date(2026, 10, 30)).open_utc == pd.Timestamp("2026-10-30 13:30", tz="UTC")
    assert cal.session(date(2026, 11, 2)).open_utc == pd.Timestamp("2026-11-02 14:30", tz="UTC")


def test_baghdad_display_shifts_with_us_dst(cal):
    before = to_local(cal.session(date(2026, 10, 12)).open_utc)
    after = to_local(cal.session(date(2026, 11, 2)).open_utc)
    assert (before.hour, before.minute) == (16, 30)
    assert (after.hour, after.minute) == (17, 30)
    assert before.tz == BAGHDAD


def test_holiday_and_weekend_are_closed(cal):
    assert cal.session(date(2026, 11, 26)) is None  # Thanksgiving
    assert cal.session(date(2026, 7, 3)) is None    # Independence Day observed
    assert cal.session(date(2026, 10, 10)) is None  # Saturday


def test_early_close(cal):
    s = cal.session(date(2026, 11, 27))
    assert s.early_close and s.close_utc == pd.Timestamp("2026-11-27 18:00", tz="UTC")
    assert not cal.session(date(2026, 10, 12)).early_close


def test_outside_calendar_window_refuses_to_guess(cal):
    with pytest.raises(ValueError):
        cal.session(date(2040, 1, 2))


def test_sessions_between(cal):
    days = [s.day for s in cal.sessions_between(date(2026, 11, 23), date(2026, 11, 29))]
    assert days == [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)]
