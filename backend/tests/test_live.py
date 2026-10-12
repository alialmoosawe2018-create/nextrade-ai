from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import SecretStr

from app.brokers.fake_mt5 import MOCK_SERVER_TZ, FakeMt5
from app.brokers.mt5_gateway import Mt5Gateway, to_utc_bars
from app.core.clock import MarketCalendar
from app.core.config import CostModel, OrbConfig, RiskConfig
from app.core.models import DataSource
from app.services.backtest import run_backtest
from app.services.live import LiveMonitor

ORB = OrbConfig(retest_tolerance=3, retest_max_penetration=15, stop_buffer=2, min_stop_distance=3,
                max_stop_distance=150)
RISK = RiskConfig(max_spread=5.0)
COSTS = CostModel(slippage=0.5)


def setup(day: date):
    fake = FakeMt5(replay_start_utc=datetime(2026, 12, 1, tzinfo=timezone.utc))
    gw = Mt5Gateway(fake, label_mock=True)
    gw.connect("credentials", login=1234567, password=SecretStr("mock-investor-pass"), server="NexTrade-MOCK")
    spec, _ = gw.instrument_spec("US100.mock")
    return fake, gw, spec


def monitor(fake, gw, spec, tmp_path, clock):
    return LiveMonitor(gw, "US100.mock", spec, MOCK_SERVER_TZ, ORB, RISK, COSTS, 10_000, DataSource.MOCK,
                       tmp_path, clock)


def _find_trade_day() -> tuple[date, object]:
    fake, gw, spec = setup(date(2026, 10, 1))
    cal = MarketCalendar()
    for s in cal.sessions_between(date(2026, 9, 1), date(2026, 10, 30)):
        raw = gw.rates_m1_server("US100.mock", s.open_utc, s.close_utc)
        bars = to_utc_bars(raw, MOCK_SERVER_TZ)
        res = run_backtest(bars, [s], spec, ORB, RISK, COSTS, 10_000, DataSource.MOCK)
        if res.trades:
            return s.day, res
    pytest.skip("no mock day with a trade in range")


def test_waiting_then_running(tmp_path):
    day = date(2026, 10, 12)
    fake, gw, spec = setup(day)
    now = {"t": datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)}
    fake.set_now(now["t"])
    m = monitor(fake, gw, spec, tmp_path, lambda: now["t"])
    m.step()
    assert m.status == "WAITING_OPEN"
    now["t"] = datetime(2026, 10, 12, 13, 50, tzinfo=timezone.utc)
    fake.set_now(now["t"])
    m.step()
    snap = m.snapshot()
    assert snap["status"] == "RUNNING" and snap["simulated"] and snap["data_source"] == "MOCK"
    assert snap["last_bar_time"] == str(pd.Timestamp("2026-10-12 13:49", tz="UTC"))
    assert snap["or_high"] is not None  # 15-minute range is complete
    m.step()  # nothing new: no bar processed twice
    assert m.last_bar_time == pd.Timestamp("2026-10-12 13:49", tz="UTC")


def test_stale_data_is_flagged(tmp_path):
    fake, gw, spec = setup(date(2026, 10, 12))
    data_now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    fake.set_now(data_now)
    m = monitor(fake, gw, spec, tmp_path, lambda: data_now + timedelta(minutes=10))
    m.step()
    assert m.status == "STALE_DATA"


def test_weekend_is_market_closed(tmp_path):
    fake, gw, spec = setup(date(2026, 10, 10))
    t = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
    fake.set_now(t)
    m = monitor(fake, gw, spec, tmp_path, lambda: t)
    m.step()
    assert m.status == "MARKET_CLOSED"


def test_live_matches_backtest_minute_by_minute(tmp_path):
    day, bt = _find_trade_day()
    fake, gw, spec = setup(day)
    s = MarketCalendar().session(day)
    t = {"now": (s.open_utc - pd.Timedelta(minutes=2)).to_pydatetime()}
    fake.set_now(t["now"])
    m = monitor(fake, gw, spec, tmp_path, lambda: t["now"])
    end = bt.trades[-1].exit_time + pd.Timedelta(minutes=3)
    while pd.Timestamp(t["now"]) <= end:
        m.step()
        t["now"] += timedelta(minutes=1)
        fake.set_now(t["now"])
    assert [(x.direction, x.entry_time, x.exit_reason, round(x.net_pnl, 6)) for x in m.trades] == \
           [(x.direction, x.entry_time, x.exit_reason, round(x.net_pnl, 6)) for x in bt.trades]
    journal_file = tmp_path / str(day) / "journal.jsonl"
    assert journal_file.exists() and "RISK" in journal_file.read_text(encoding="utf-8")


def test_late_start_does_not_trade_stale_signals(tmp_path):
    day, bt = _find_trade_day()
    fake, gw, spec = setup(day)
    s = MarketCalendar().session(day)
    late = (bt.trades[-1].exit_time + pd.Timedelta(minutes=5)).to_pydatetime()
    fake.set_now(late)
    m = monitor(fake, gw, spec, tmp_path, lambda: late)
    m.step()  # backfills the whole morning in one go
    assert m.trades == [] and not m.broker.positions
    risk = [e for e in m.events if e["stage"] == "RISK"]
    assert risk and all("STALE_DATA" in e["reasons"] for e in risk)
