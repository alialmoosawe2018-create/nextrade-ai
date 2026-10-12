from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.config import OrbConfig, StopMethod
from app.core.models import ONE_MINUTE, Bar, DataSource, Direction
from app.strategy.orb import OrbRetestDay, Phase
from tests.conftest import OPEN, bar, opening_range

SPREAD = 0.5


def run(cfg, session, bars, finish=True):
    day = OrbRetestDay(cfg, session, lambda b: SPREAD, "testhash0000", DataSource.TEST)
    events = []
    for b in bars:
        events += day.on_bar(b)
    if finish:
        events += day.finish()
    return day, events


def only(events):
    assert len(events) == 1, events
    return events[0]


# ---------------------------------------------------------------- valid signals
def test_upside_breakout_and_retest(cfg, session):
    bars = opening_range() + [
        bar(3, 99, 103, 99, 102),         # 1m close 102 > OR high 100 -> breakout
        bar(4, 102, 102.5, 100.5, 101.5),  # low 100.5 <= 101 touches; close 101.5 > 100 confirms
    ]
    day, events = run(cfg, session, bars)
    ev = only(events)
    assert ev.kind == "SIGNAL"
    c = ev.candidate
    assert c.direction is Direction.LONG
    assert c.or_high == 100 and c.or_low == 95
    assert c.breakout_time == OPEN + pd.Timedelta(minutes=4)
    assert c.signal_time == OPEN + pd.Timedelta(minutes=5)
    assert c.entry_ref == pytest.approx(101.5 + SPREAD)       # long enters on ask
    assert c.stop == pytest.approx(100.5 - 1.0)               # retest swing low - buffer
    assert c.target == pytest.approx(102.0 + 2.0 * 2.5)       # 2R
    assert c.data_source is DataSource.TEST
    assert day.phase is Phase.DONE


def test_downside_breakout_and_retest(cfg, session):
    bars = opening_range() + [
        bar(3, 96, 96, 92, 93),           # close 93 < OR low 95 -> short breakout
        bar(4, 93, 94.5, 92.8, 94),       # high 94.5 >= 94 touches; close 94 < 95 confirms
    ]
    _, events = run(cfg, session, bars)
    c = only(events).candidate
    assert c.direction is Direction.SHORT
    assert c.entry_ref == pytest.approx(94.0)                 # short enters on bid
    assert c.stop == pytest.approx(94.5 + SPREAD + 1.0)       # swing high on ask side + buffer
    assert c.target == pytest.approx(94.0 - 2.0 * 2.0)


def test_range_opposite_stop_method(cfg, session):
    cfg = cfg.model_copy(update={"stop_method": StopMethod.RANGE_OPPOSITE, "max_stop_distance": 50})
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    c = only(run(cfg, session, bars)[1]).candidate
    assert c.stop == pytest.approx(95 - 1.0)


# ---------------------------------------------------------------- no-signal paths
def test_false_breakout(cfg, session):
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102, 94, 94.9)]  # 94.9 < 100-5
    ev = only(run(cfg, session, bars)[1])
    assert (ev.kind, ev.reason) == ("REJECT", "FALSE_BREAKOUT")


def test_no_retest_within_window(cfg, session):
    bars = opening_range() + [bar(3, 99, 103, 99, 102)]
    bars += [bar(m, 103, 104, 101.5, 103) for m in range(4, 14)]  # lows stay above 101
    ev = only(run(cfg, session, bars, finish=False)[1])
    assert ev.reason == "NO_RETEST"
    assert ev.time == OPEN + pd.Timedelta(minutes=4 + 10)  # breakout close + window


def test_retest_touched_but_expired(cfg, session):
    bars = opening_range() + [bar(3, 99, 103, 99, 102)]
    bars += [bar(m, 100, 100.5, 98, 99) for m in range(4, 14)]  # touches, never closes back above
    ev = only(run(cfg, session, bars, finish=False)[1])
    assert ev.reason == "RETEST_EXPIRED"


def test_no_breakout_before_deadline(cfg, session):
    bars = opening_range() + [bar(m, 97, 99, 96, 98) for m in range(3, 30)]
    ev = only(run(cfg, session, bars, finish=False)[1])
    assert ev.reason == "NO_BREAKOUT"
    assert ev.time == OPEN + pd.Timedelta(minutes=30)


def test_breakout_after_deadline_is_ignored(cfg, session):
    bars = opening_range() + [bar(m, 97, 99, 96, 98) for m in range(3, 29)]
    bars += [bar(29, 99, 104, 99, 103)]  # closes at the deadline exactly: still valid
    day, events = run(cfg, session, bars, finish=False)
    assert events == [] and day.phase is Phase.WAIT_RETEST
    bars2 = opening_range() + [bar(m, 97, 99, 96, 98) for m in range(3, 30)] + [bar(30, 99, 104, 99, 103)]
    _, events2 = run(cfg, session, bars2, finish=False)
    assert [e.reason for e in events2] == ["NO_BREAKOUT"]


def test_entry_window_closed(cfg, session):
    cfg = cfg.model_copy(update={"last_entry_minutes": 4})
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    ev = only(run(cfg, session, bars)[1])
    assert ev.reason == "ENTRY_WINDOW_CLOSED"


def test_stop_distance_invalid(cfg, session):
    cfg = cfg.model_copy(update={"min_stop_distance": 10.0})
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    ev = only(run(cfg, session, bars)[1])
    assert ev.reason == "STOP_DISTANCE_INVALID"


def test_range_filters(cfg, session):
    narrow = cfg.model_copy(update={"min_range": 6.0})
    assert only(run(narrow, session, opening_range())[1]).reason == "RANGE_TOO_NARROW"
    wide = cfg.model_copy(update={"max_range": 4.0})
    assert only(run(wide, session, opening_range())[1]).reason == "RANGE_TOO_WIDE"


# ---------------------------------------------------------------- duplicates
def test_only_one_signal_per_day(cfg, session):
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    bars += [bar(5, 101, 101, 93, 93), bar(6, 93, 94.5, 92.8, 94)]  # would qualify again
    day, events = run(cfg, session, bars)
    assert [e.kind for e in events] == ["SIGNAL"]


# ---------------------------------------------------------------- data problems
def test_missing_bar_in_opening_range(cfg, session):
    bars = [bar(0, 98, 100, 96, 98), bar(2, 97, 99.5, 96, 99), bar(3, 99, 103, 99, 102)]
    ev = only(run(cfg, session, bars)[1])
    assert ev.reason == "INCOMPLETE_OPENING_RANGE"
    assert ev.details["bars_in_range"] == 2


def test_missing_last_bar_of_opening_range(cfg, session):
    bars = [bar(0, 98, 100, 96, 98), bar(1, 98, 99, 95, 97), bar(3, 99, 103, 99, 102)]
    assert only(run(cfg, session, bars)[1]).reason == "INCOMPLETE_OPENING_RANGE"


def test_no_data(cfg, session):
    assert only(run(cfg, session, [])[1]).reason == "NO_DATA"


def test_data_gap_aborts_day(cfg, session):
    bars = opening_range() + [bar(3, 97, 99, 96, 98), bar(7, 97, 99, 96, 98)]  # 3 missing > 2
    ev = only(run(cfg, session, bars)[1])
    assert ev.reason == "DATA_GAP" and ev.details["missing_minutes"] == 3


def test_small_gap_tolerated(cfg, session):
    bars = opening_range() + [bar(3, 97, 99, 96, 98), bar(6, 99, 103, 99, 102)]  # 2 missing
    day, events = run(cfg, session, bars, finish=False)
    assert events == [] and day.phase is Phase.WAIT_RETEST


def test_incomplete_confirmation_candle_is_not_used(cfg, session):
    cfg = cfg.model_copy(update={"confirm_tf_minutes": 2})
    # bucket [3,4]: minute 4 missing -> the 102 close of minute 3 must NOT count.
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(5, 99, 99, 98, 99), bar(6, 99, 99, 98, 99)]
    day, events = run(cfg, session, bars, finish=False)
    assert events == [] and day.phase is Phase.WAIT_BREAKOUT
    assert day.incomplete_buckets == 1


def test_incomplete_candle_is_not_merged_into_the_next(cfg, session):
    cfg = cfg.model_copy(update={"confirm_tf_minutes": 2})
    # [3,4] incomplete (4 missing). Bar 5 alone must not complete the [5,6] candle.
    bars = opening_range() + [bar(3, 98, 99, 97, 99), bar(5, 99, 103, 99, 102)]
    day, _ = run(cfg, session, bars, finish=False)
    assert day.phase is Phase.WAIT_BREAKOUT
    day.on_bar(bar(6, 102, 103, 101, 102.5))
    assert day.phase is Phase.WAIT_RETEST
    assert day.breakout_time == OPEN + pd.Timedelta(minutes=7)


def test_candle_closing_after_deadline_never_breaks_out(cfg, session):
    # Bar 29 is missing (gap of 1 is tolerated); bar 30 closes at +31m, after the +30m deadline.
    bars = opening_range() + [bar(m, 97, 99, 96, 98) for m in range(3, 29)] + [bar(30, 99, 104, 99, 103)]
    day, events = run(cfg, session, bars, finish=False)
    assert [e.reason for e in events] == ["NO_BREAKOUT"]
    assert day.direction is None


def test_confirmation_uses_candle_close_not_intrabar(cfg, session):
    cfg = cfg.model_copy(update={"confirm_tf_minutes": 2})
    # minute 3 closes above, minute 4 closes back inside -> 2m candle close 99 -> no breakout.
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102, 98, 99)]
    day, _ = run(cfg, session, bars, finish=False)
    assert day.phase is Phase.WAIT_BREAKOUT
    # Same but minute 4 closes above -> breakout at the 2m candle close (open + 5m).
    bars2 = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 103, 101, 102.5)]
    day2, _ = run(cfg, session, bars2, finish=False)
    assert day2.phase is Phase.WAIT_RETEST
    assert day2.breakout_time == OPEN + pd.Timedelta(minutes=5)


# ---------------------------------------------------------------- session / input contract
def test_pre_market_bars_are_ignored(cfg, session):
    pre = Bar(OPEN - ONE_MINUTE, 50, 500, 1, 60, 50)  # absurd bar before the open
    bars = [pre] + opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    c = only(run(cfg, session, bars)[1]).candidate
    assert (c.or_high, c.or_low) == (100, 95)


def test_after_close_bars_are_ignored(cfg, session):
    late = Bar(session.close_utc, 98, 100, 96, 98, 50)
    day, events = run(cfg, session, [late])
    assert only(events).reason == "NO_DATA"


def test_rejects_out_of_order_and_misaligned_bars(cfg, session):
    day = OrbRetestDay(cfg, session, lambda b: SPREAD, "h", DataSource.TEST)
    day.on_bar(bar(1, 98, 99, 95, 97))
    with pytest.raises(ValueError):
        day.on_bar(bar(0, 98, 100, 96, 98))
    day2 = OrbRetestDay(cfg, session, lambda b: SPREAD, "h", DataSource.TEST)
    with pytest.raises(ValueError):
        day2.on_bar(Bar(OPEN + pd.Timedelta(seconds=30), 98, 100, 96, 98, 50))


def test_last_entry_respects_early_close():
    from datetime import date
    from app.core.clock import MarketCalendar
    s = MarketCalendar().session(date(2026, 11, 27))  # early close 18:00Z
    c = OrbConfig(opening_range_minutes=15, last_entry_minutes=390, flat_before_close_minutes=5,
                  breakout_deadline_minutes=90)
    day = OrbRetestDay(c, s, lambda b: 0.0, "h", DataSource.TEST)
    assert day.last_entry_at == pd.Timestamp("2026-11-27 17:54", tz="UTC")


# ---------------------------------------------------------------- look-ahead
def _random_day(seed: int) -> list[Bar]:
    rng = np.random.default_rng(seed)
    price, out = 20000.0, []
    for m in range(0, 120):
        c = price + rng.normal(0, 4)
        h, l = max(price, c) + abs(rng.normal(0, 2)), min(price, c) - abs(rng.normal(0, 2))
        out.append(bar(m, price, h, l, c))
        price = c
    return out


def test_future_bars_cannot_change_a_signal(session):
    cfg = OrbConfig(opening_range_minutes=15, confirm_tf_minutes=5, retest_tolerance=3,
                    retest_max_penetration=15, stop_buffer=2, min_stop_distance=1,
                    max_stop_distance=200, breakout_deadline_minutes=90, last_entry_minutes=110)
    checked = 0
    for seed in range(60):
        bars = _random_day(seed)
        _, events = run(cfg, session, bars, finish=False)
        sig = [e for e in events if e.kind == "SIGNAL"]
        if not sig:
            continue
        t = sig[0].candidate.signal_time
        # Wreck every bar that opens at or after the signal time.
        wrecked = [b if b.close_time <= t else Bar(b.time, b.open * 3, b.high * 3, b.low / 3, b.close / 3, 999)
                   for b in bars]
        _, events2 = run(cfg, session, wrecked, finish=False)
        sig2 = [e for e in events2 if e.kind == "SIGNAL"]
        assert sig2 and sig2[0].candidate == sig[0].candidate
        checked += 1
    assert checked >= 5, "too few random days produced a signal to make this test meaningful"
