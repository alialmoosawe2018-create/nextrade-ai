from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.core.clock import MarketCalendar
from app.core.models import DataSource
from app.market.providers import MockProvider
from app.reporting.metrics import summarize
from app.services.backtest import run_backtest
from tests.conftest import bar, bars_df, opening_range


def long_day_bars(target_hit=True):
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    # entry at bar 5 open (101.6 -> ask 102.1 + slip 0.1 = 102.2); stop 99.5, target 107
    bars.append(bar(5, 101.6, 102.0, 101.2, 101.8))
    bars.append(bar(6, 102, 108, 101.5, 107.5) if target_hit else bar(6, 102, 103, 101.5, 102.5))
    bars += [bar(m, 102.5, 103, 102, 102.5) for m in range(7, 20)]
    return bars


def test_full_pipeline_long_trade(session, spec, cfg, risk_cfg, costs):
    res = run_backtest(bars_df(long_day_bars()), [session], spec, cfg, risk_cfg, costs, 10_000, DataSource.TEST)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "TARGET" and t.direction == "LONG" and t.simulated
    stages = [(j["stage"], j["status"]) for j in res.journal]
    assert stages == [("RISK", "APPROVED"), ("EXECUTION", "FILLED"), ("EXECUTION", "CLOSED")]
    assert all(j["config_hash"] == res.config_hash for j in res.journal)
    assert res.ending_balance == pytest.approx(10_000 + t.net_pnl)


def test_time_exit_through_runner(session, spec, cfg, risk_cfg, costs):
    res = run_backtest(bars_df(long_day_bars(target_hit=False)), [session], spec, cfg, risk_cfg, costs,
                       10_000, DataSource.TEST)
    [t] = res.trades
    # signal at +5m, max_hold 10 -> exit at the open of the +15m bar.
    assert t.exit_reason == "TIME_EXIT"
    assert t.exit_time == session.open_utc + pd.Timedelta(minutes=15)


def test_risk_rejection_is_journaled_not_traded(session, spec, cfg, risk_cfg, costs):
    tight = risk_cfg.model_copy(update={"max_spread": 0.1})
    res = run_backtest(bars_df(long_day_bars()), [session], spec, cfg, tight, costs, 10_000, DataSource.TEST)
    assert res.trades == []
    [j] = res.journal
    assert (j["stage"], j["status"], j["reasons"]) == ("RISK", "REJECTED", ["SPREAD_TOO_WIDE"])


def test_signal_on_last_bar_of_data_records_missing_entry(session, spec, cfg, risk_cfg, costs):
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5)]
    res = run_backtest(bars_df(bars), [session], spec, cfg, risk_cfg, costs, 10_000, DataSource.TEST)
    assert res.trades == []
    assert res.journal[-1]["reasons"] == ["ENTRY_BAR_MISSING"]


def test_no_data_produces_insufficient_data(session, spec, cfg, risk_cfg, costs):
    empty = bars_df([])
    res = run_backtest(empty, [session], spec, cfg, risk_cfg, costs, 10_000, DataSource.TEST)
    assert res.trades == [] and res.journal[0]["reasons"] == ["NO_DATA"]
    summary = summarize(res.trades, 10_000, 1)
    assert summary["status"] == "INSUFFICIENT DATA"
    assert "net_pnl" not in summary and "win_rate" not in summary


def test_refuses_incomplete_spec_and_duplicate_bars(session, cfg, risk_cfg, costs, spec):
    from app.core.config import InstrumentSpec
    with pytest.raises(ValueError):
        run_backtest(bars_df(long_day_bars()), [session], InstrumentSpec(), cfg, risk_cfg, costs, 10_000, DataSource.TEST)
    dup = bars_df(long_day_bars() + [bar(10, 1, 1, 1, 1)])
    with pytest.raises(ValueError):
        run_backtest(dup, [session], spec, cfg, risk_cfg, costs, 10_000, DataSource.TEST)


def test_mock_run_is_labelled(spec, risk_cfg, costs):
    from app.core.config import OrbConfig
    sessions = MarketCalendar().sessions_between(date(2026, 6, 1), date(2026, 9, 30))
    df = MockProvider(seed=11).bars(sessions)
    orb = OrbConfig(retest_tolerance=3, retest_max_penetration=15, stop_buffer=2,
                    min_stop_distance=3, max_stop_distance=150)
    risk = risk_cfg.model_copy(update={"max_spread": 5.0})
    res = run_backtest(df, sessions, spec, orb, risk, costs, 10_000, DataSource.MOCK)
    assert res.data_source is DataSource.MOCK and len(res.trades) > 0
    assert all(t.data_source == "MOCK" and t.simulated for t in res.trades)
    assert all(j["data_source"] == "MOCK" and j["simulated"] for j in res.journal)
    assert {j["session_day"] for j in res.journal if j["stage"] == "STRATEGY"} |            {t.session_day for t in res.trades} <= set(res.sessions)


def test_higher_costs_lower_the_same_trade(session, spec, cfg, risk_cfg, costs):
    rs = []
    for k in (1.0, 2.0):
        res = run_backtest(bars_df(long_day_bars()), [session], spec, cfg, risk_cfg, costs.scaled(k),
                           10_000, DataSource.TEST)
        [t] = res.trades
        rs.append((t.entry_price, t.r_multiple))
    assert rs[1][0] > rs[0][0]   # long pays more at 2x costs
    assert rs[1][1] < rs[0][1]   # and earns fewer R


def test_summary_statistics():
    from tests.test_metrics_helpers import fake_trades
    trades = fake_trades([2.0, -1.0, 2.0, -1.0])
    s = summarize(trades, 10_000, 4)
    assert s["status"] == "INSUFFICIENT_SAMPLE"
    assert s["r"]["expectancy_r"] == pytest.approx(0.5)
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["profit_factor"] == pytest.approx(4.0 / 2.0)
    assert s["r"]["ci95_r"][0] < 0.5 < s["r"]["ci95_r"][1]


def test_single_trade_summary_is_json_safe():
    import json
    from tests.test_metrics_helpers import fake_trades
    s = summarize(fake_trades([1.5]), 10_000, 1)
    json.dumps(s, allow_nan=False)  # raises if any NaN/inf slipped through
    assert s["r"]["sd_r"] is None and s["r"]["ci95_r"] is None
