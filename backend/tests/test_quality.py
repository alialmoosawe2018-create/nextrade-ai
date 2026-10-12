from __future__ import annotations

from datetime import date

import pandas as pd

from app.core.clock import MarketCalendar
from app.market.providers import MockProvider
from app.market.quality import check, clean
from tests.conftest import bar, bars_df, opening_range


def test_full_mock_day_is_ok():
    sessions = MarketCalendar().sessions_between(date(2026, 10, 12), date(2026, 10, 13))
    r = check(MockProvider(seed=3).bars(sessions), sessions)
    assert r.verdict == "OK"
    assert r.sessions_with_full_data == 2 and r.missing_minutes_total == 0


def test_empty_is_insufficient_data(session):
    r = check(bars_df([]), [session])
    assert r.verdict == "INSUFFICIENT DATA" and r.sessions_empty == 1


def test_detects_problems(session):
    bars = opening_range() + [bar(3, 99, 98, 97, 99)]          # high < open -> invalid
    df = bars_df(bars + [bar(1, 98, 99, 95, 97)])               # duplicate timestamp
    df.loc[len(df)] = [pd.Timestamp("2026-10-12 13:40:30", tz="UTC"), 98, 99, 97, 98, 50]  # misaligned
    r = check(df, [session])
    assert r.duplicates == 1 and r.ohlc_invalid == 1 and r.misaligned == 1 and r.unsorted
    assert r.sessions_partial == 1 and r.verdict == "FAIL"


def test_partial_session_warns(session):
    sessions = [session]
    full = MockProvider(seed=5).bars(sessions)
    r = check(full.drop(index=[100, 101, 102]), sessions)
    assert r.verdict == "WARN" and r.missing_minutes_total == 3
    assert r.worst_sessions[0]["missing"] == 3


def test_clean_drops_only_bad_rows(session):
    df = bars_df(opening_range() + [bar(3, 99, 98, 97, 99), bar(1, 98, 99, 95, 97)])
    out, stats = clean(df)
    assert stats == {"dropped_duplicates": 1, "dropped_invalid": 1}
    assert out["time"].is_monotonic_increasing and len(out) == 3
