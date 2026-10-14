from __future__ import annotations

import time
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.brokers.fake_mt5 import FakeMt5
from app.core.clock import MarketCalendar
from app.core.config import EntryFilter, OrbConfig, config_hash
from app.core.models import DataSource
from app.research.features import daily_rth, day_context, signal_features
from app.research.learning import MIN_TRADES, discover, idea_stats, resolve_idea
from app.services.backtest import run_backtest
from tests.conftest import bar, bars_df, opening_range
from tests.test_chat_and_ideas import FakeRunner


# ---------------------------------------------------------------- features: no look-ahead
def _daily(n=30, start=date(2026, 8, 3)):
    sessions = MarketCalendar().sessions_between(start, date(2026, 10, 30))[:n]
    rng = np.random.default_rng(1)
    rows, price = [], 100.0
    for s in sessions:
        o = price
        h, l = o + abs(rng.normal(2, 1)), o - abs(rng.normal(2, 1))
        c = rng.uniform(l, h)
        rows.append({"day": s.day, "open": o, "high": h, "low": l, "close": c})
        price = c
    return sessions, pd.DataFrame(rows)


def test_day_context_ignores_today_and_the_future():
    sessions, daily = _daily()
    s = sessions[20]
    only_prior = day_context(daily[daily["day"] < s.day], s, 101.0)
    with_future = day_context(daily, s, 101.0)        # today's and later rows present
    assert only_prior == with_future
    wrecked = daily.copy()
    wrecked.loc[wrecked["day"] >= s.day, ["high", "low", "close"]] = [1e9, -1e9, 5e8]
    assert day_context(wrecked, s, 101.0) == only_prior


def test_context_needs_enough_history():
    sessions, daily = _daily()
    ctx = day_context(daily[daily["day"] < sessions[5].day], sessions[5], 100.0)
    assert ctx["atr14"] is None and ctx["gap_atr"] is None and ctx["weekday"] == sessions[5].day.weekday()


# ---------------------------------------------------------------- filters in the engine
def _long_day(session_bars_extra=True):
    bars = opening_range() + [bar(3, 99, 103, 99, 102), bar(4, 102, 102.5, 100.5, 101.5),
                              bar(5, 101.6, 102.0, 101.2, 101.8), bar(6, 102, 108, 101.5, 107.5)]
    bars += [bar(m, 102.5, 103, 102, 102.5) for m in range(7, 20)]
    return bars


def test_adopted_filter_blocks_the_signal(session, spec, cfg, risk_cfg, costs):
    base = run_backtest(bars_df(_long_day()), [session], spec, cfg, risk_cfg, costs, 10_000, DataSource.TEST)
    assert len(base.trades) == 1
    risk_entry = next(j for j in base.journal if j["stage"] == "RISK")
    feats = risk_entry["features"]
    assert feats["minutes_after_open"] == pytest.approx(5.0) and feats["breakout_delay_min"] == pytest.approx(1.0)
    f = EntryFilter(id="L1", feature="minutes_after_open", op="lt", value=10)
    filtered_cfg = cfg.model_copy(update={"entry_filters": (f,)})
    res = run_backtest(bars_df(_long_day()), [session], spec, filtered_cfg, risk_cfg, costs, 10_000, DataSource.TEST)
    assert res.trades == []
    fil = [j for j in res.journal if j["stage"] == "FILTER"]
    assert fil and fil[0]["reasons"] == ["LESSON:L1"]
    assert config_hash(filtered_cfg) != config_hash(cfg)   # a lesson is a new strategy version


def test_filter_never_acts_on_missing_values():
    f = EntryFilter(id="L2", feature="gap_atr", op="gt", value=0.1)
    assert not f.matches({"gap_atr": None}) and f.matches({"gap_atr": 0.5})
    with pytest.raises(ValueError):
        EntryFilter(id="x", feature="tomorrow_price", op="gt", value=1)


# ---------------------------------------------------------------- evidence engine
def _trades(n, bad_feature_both_halves=True, seed=3):
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2025-01-02 14:00", tz="UTC")
    rows = []
    for i in range(n):
        x = rng.uniform(0, 1)                   # e.g. spread_to_stop
        noise = rng.uniform(0, 1)               # irrelevant feature
        in_second_half = i >= n * 0.6
        bad = x > 0.75 and (bad_feature_both_halves or not in_second_half)
        r = -1.0 if bad else (2.0 if rng.uniform() < 0.45 else -1.0)
        rows.append({"entry_time": str(t0 + pd.Timedelta(days=i)), "r_multiple": r, "spread_to_stop": x,
                     "gap_atr": noise, "trend_align": float(rng.choice([-1, 0, 1]))})
    return pd.DataFrame(rows)


def test_real_pattern_is_validated():
    out = discover(_trades(400), "US100")
    lessons = {l["feature"]: l for l in out["lessons"]}
    assert "spread_to_stop" in lessons and lessons["spread_to_stop"]["status"] == "VALIDATED"
    assert lessons["spread_to_stop"]["op"] == "gt"
    assert "gap_atr" not in lessons or lessons["gap_atr"]["status"] != "VALIDATED"


def test_pattern_that_vanishes_later_is_not_validated():
    out = discover(_trades(400, bad_feature_both_halves=False), "US100")
    lesson = next((l for l in out["lessons"] if l["feature"] == "spread_to_stop"), None)
    assert lesson is not None and lesson["status"] == "UNVALIDATED"


def test_pure_noise_produces_no_validated_lesson():
    rng = np.random.default_rng(11)
    t0 = pd.Timestamp("2025-01-02 14:00", tz="UTC")
    df = pd.DataFrame([{"entry_time": str(t0 + pd.Timedelta(days=i)), "r_multiple": 2.0 if rng.uniform() < 0.34 else -1.0,
                        "spread_to_stop": rng.uniform(), "gap_atr": rng.uniform(), "stop_atr": rng.uniform(),
                        "minutes_after_open": rng.uniform(16, 120), "trend_align": float(rng.choice([-1, 0, 1])),
                        "weekday": float(rng.integers(0, 5))} for i in range(500)])
    out = discover(df, "US100")
    assert out["status"] == "OK" and out["rules_tested"] >= 15
    assert [l for l in out["lessons"] if l["status"] == "VALIDATED"] == []


def test_too_few_trades_is_insufficient_data():
    out = discover(_trades(MIN_TRADES - 1), "US100")
    assert out["status"] == "INSUFFICIENT DATA" and out["lessons"] == []


# ---------------------------------------------------------------- idea tracking
def _bars(rows):
    t0 = pd.Timestamp("2026-10-12 14:00", tz="UTC")
    return pd.DataFrame([{"time": t0 + pd.Timedelta(minutes=i), "open": o, "high": h, "low": l, "close": c}
                         for i, (o, h, l, c) in enumerate(rows)])


IDEA = {"side": "BUY", "entry_ref": 100.0, "sl": 98.0, "tp": 104.0, "created_at": "2026-10-12T14:00:00+00:00"}


def test_idea_outcomes():
    assert resolve_idea(IDEA, _bars([(100, 101, 99.5, 100.5), (100.5, 104.2, 100, 104)]), 0.1) == ("TARGET", 2.0)
    assert resolve_idea(IDEA, _bars([(100, 100.5, 97.9, 98.2)]), 0.1) == ("STOP", -1.0)
    assert resolve_idea(IDEA, _bars([(100, 104.5, 97.5, 101)]), 0.1) == ("STOP", -1.0)   # both in one bar
    sell = {**IDEA, "side": "SELL", "sl": 102.0, "tp": 96.0}
    # bid high 101.95 < 102 but ask high = 101.95 + 0.1 >= 102 -> stopped.
    assert resolve_idea(sell, _bars([(100, 101.95, 99.5, 100)]), 0.1) == ("STOP", -1.0)
    open_ = resolve_idea(IDEA, _bars([(100, 101, 99.5, 100.5)]), 0.1)
    assert open_ == ("OPEN", None)
    stats = idea_stats([{"symbol": "X", "outcome": "TARGET", "outcome_r": 2.0}, {"symbol": "X", "outcome": "STOP", "outcome_r": -1.0}])
    assert stats["resolved"] == 2 and stats["mean_r"] == 0.5 and stats["warning"]


# ---------------------------------------------------------------- API: ingest, losses, discover, approve, review
class ReviewRunner(FakeRunner):
    def __init__(self, verdict="PROCESS"):
        super().__init__()
        self.verdict = verdict

    def run_json(self, system, prompt, schema):
        import json
        data = json.loads(prompt)
        uids = [t["uid"] for t in data["trades"]]
        reviews = [{"uid": u, "verdict": self.verdict, "factors": ["spread_to_stop", "made_up_factor"],
                    "explanation": "سبريد عالي"} for u in uids]
        reviews.append({"uid": "not-a-trade", "verdict": "PROCESS", "factors": [], "explanation": "x"})
        return {"data": {"reviews": reviews}, "cost_usd": 0, "duration_ms": 1}


@pytest.fixture
def client(tmp_path):
    fk = lambda: FakeMt5(replay_start_utc=datetime(2026, 12, 1, tzinfo=timezone.utc))  # noqa: E731
    c = TestClient(create_app(tmp_path, mt5_factory=fk, mock_factory=fk, allow_non_loopback=True))
    pid = c.post("/api/accounts", json={"label": "m", "kind": "MOCK"}).json()["id"]
    c.post("/api/connection/connect", json={"profile_id": pid})
    c.post("/api/instrument/select", json={"symbol": "US100.mock"})
    c.put("/api/settings/risk", json={"max_spread": 5.0})
    c.put("/api/settings/strategy", json={**c.get("/api/settings").json()["values"]["strategy"],
                                          "retest_tolerance": 3, "retest_max_penetration": 15, "stop_buffer": 2,
                                          "min_stop_distance": 3, "max_stop_distance": 150})
    return c


def _analysis(client, start="2026-05-04", end="2026-11-20"):
    from tests.test_api import run_job
    code, out = run_job(client, {"start": start, "end": end, "split": "all"})
    assert code == 200, out
    return out


def test_analysis_trades_land_in_the_db_with_features(client):
    out = _analysis(client)
    st = client.app.state.st
    df = st.db.trades_df(run_id=out["run_id"])
    assert len(df) == out["summary"]["trades"] > 0
    assert "spread_to_stop" in df.columns and df["minutes_after_open"].notna().all()
    later = df.iloc[-1]
    assert later["atr_ratio"] is not None and not pd.isna(later["atr_ratio"])  # day context available after warm-up
    losses = client.get("/api/learning/losses").json()
    assert losses["run_id"] == out["run_id"] and losses["losing"] > 0 and losses["losses"][0]["features"]


def test_discover_approve_retire_cycle(client):
    _analysis(client)
    st = client.app.state.st
    res = client.post("/api/learning/discover").json()
    assert res["status"] in ("OK", "INSUFFICIENT DATA")
    # Force a known validated lesson to exercise the owner-approval path deterministically.
    st.db.replace_open_lessons(res["run_id"], "US100.mock", [{"id": "Ltest01", "feature": "spread_to_stop", "op": "gt",
                                                              "value": 0.2, "status": "VALIDATED", "evidence": {}}])
    st.db.replace_open_lessons(res["run_id"], "US100.mock", [{"id": "Ltest01", "feature": "spread_to_stop", "op": "gt",
                                                              "value": 0.2, "status": "VALIDATED", "evidence": {}},
                                                             {"id": "Lweak", "feature": "gap_atr", "op": "gt",
                                                              "value": 1.0, "status": "UNVALIDATED", "evidence": {}}])
    h0 = client.get("/api/settings").json()["config_hash"]
    assert client.post("/api/learning/lessons/Lweak/approve").status_code == 409   # unvalidated cannot be adopted
    ok = client.post("/api/learning/lessons/Ltest01/approve").json()
    assert [f["id"] for f in ok["entry_filters"]] == ["Ltest01"]
    assert client.get("/api/settings").json()["config_hash"] != h0
    assert st.db.lesson("Ltest01")["status"] == "ADOPTED"
    client.post("/api/learning/lessons/Ltest01/retire")
    assert client.get("/api/settings").json()["values"]["strategy"]["entry_filters"] == []
    assert client.get("/api/settings").json()["config_hash"] == h0


def test_claude_reviews_are_stored_as_hypotheses(client):
    _analysis(client)
    client.app.state.st.chat.runner = ReviewRunner()
    r = client.post("/api/learning/review").json()
    for _ in range(300):
        job = client.get(f"/api/learning/review/{r['job_id']}").json()
        if job["status"] != "RUNNING":
            break
        time.sleep(0.02)
    assert job["status"] == "DONE" and job["reviewed"] == r["count"]
    reviews = client.app.state.st.db.reviews()
    assert "not-a-trade" not in reviews
    assert all(v["factors"] == ["spread_to_stop"] for v in reviews.values())   # invented factors dropped


def test_chat_context_carries_the_learning_memory(client):
    fake = FakeRunner()
    client.app.state.st.chat.runner = fake
    from tests.test_chat_and_ideas import chat
    chat(client, "شنو تعلمت؟")
    assert '"learning_memory"' in fake.calls[0][1] and "your_trade_ideas_track_record" in fake.calls[0][1]


def test_vectorized_context_equals_the_reference():
    from app.research.features import context_table, with_gap
    sessions, daily = _daily(60)
    table = context_table(daily)
    for s in sessions:
        ref = day_context(daily[daily["day"] < s.day], s, 101.3)
        fast = with_gap(table[s.day], 101.3)
        assert ref.keys() == fast.keys()
        for k in ref:
            if ref[k] is None or fast[k] is None:
                assert ref[k] == fast[k], (s.day, k)
            else:
                assert fast[k] == pytest.approx(ref[k], rel=1e-9), (s.day, k)
