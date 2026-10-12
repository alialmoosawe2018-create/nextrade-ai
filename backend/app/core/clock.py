"""Market clock. All US market logic runs on the NYSE calendar (America/New_York).

Never hardcode the Baghdad <-> New York offset: it changes twice a year.
Holidays and early closes come from exchange_calendars (XNYS). Its session
window is bounded (roughly 20 years back, 1 year ahead of install/run date);
dates outside it raise instead of guessing.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.core.models import Session

NEW_YORK = ZoneInfo("America/New_York")
BAGHDAD = ZoneInfo("Asia/Baghdad")
REGULAR_SESSION_MINUTES = 390  # 09:30-16:00 ET


@lru_cache(maxsize=4)
def _calendar(name: str):
    return xcals.get_calendar(name)


class MarketCalendar:
    def __init__(self, name: str = "XNYS") -> None:
        self._cal = _calendar(name)

    @property
    def first_day(self) -> date:
        return self._cal.first_session.date()

    @property
    def last_day(self) -> date:
        return self._cal.last_session.date()

    def _check_bounds(self, d: date) -> None:
        if not (self.first_day <= d <= self.last_day):
            raise ValueError(
                f"{d} is outside the calendar window {self.first_day}..{self.last_day}; refusing to guess."
            )

    def session(self, d: date) -> Session | None:
        """The regular session for `d`, or None if the exchange is closed."""
        self._check_bounds(d)
        ts = pd.Timestamp(d)
        if not self._cal.is_session(ts):
            return None
        open_utc = self._cal.session_open(ts)
        close_utc = self._cal.session_close(ts)
        minutes = int((close_utc - open_utc) / pd.Timedelta(minutes=1))
        return Session(day=d, open_utc=open_utc, close_utc=close_utc,
                       early_close=minutes < REGULAR_SESSION_MINUTES)

    def sessions_between(self, start: date, end: date) -> list[Session]:
        self._check_bounds(start)
        self._check_bounds(end)
        days = self._cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
        return [s for s in (self.session(d.date()) for d in days) if s is not None]


def to_local(ts: pd.Timestamp, tz: ZoneInfo = BAGHDAD) -> pd.Timestamp:
    """For display only. Internal logic stays in UTC."""
    return ts.tz_convert(tz)
