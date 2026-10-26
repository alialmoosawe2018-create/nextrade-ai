from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.brokers.fake_mt5 import FakeMt5
from app.core.clock import MarketCalendar
from app.core.models import Direction
from app.research.features import MacroContext, signal_features
from tests.test_chat_and_ideas import FakeRunner

CAL = MarketCalendar()
S = CAL.session(date(2026, 10, 27))       # Tue, open 13:30Z
P1 = CAL.session(date(2026, 10, 26))      # Mon
P2 = CAL.session(date(2026, 10, 23))      # Fri


def minute_bars(start: str, end: str, price_fn) -> pd.DataFrame:
    t = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="1min", inclusive="left")
    px = [price_fn(x) for x in t]
    return pd.DataFrame({"time": t, "open": px, "high": px, "low": px, "close": px, "spread": 10})


def spx(x):
    if x < P1.close_utc:
        return 100.0          # Friday and Monday until the close
    if x < S.open_utc:
        return 101.0          # overnight +1%
    return 50.0               # after the open: must never be seen


def test_macro_uses_only_bars_closed_before_the_open():
    bars = {"spx": minute_bars("2026-10-23 12:00", "2026-10-27 16:00", spx),
            "eurusd": minute_bars("2026-10-23 12:00", "2026-10-27 16:00", lambda x: 1.10 if x < P1.close_utc else 1.089),
            "gold": minute_bars("2026-10-23 12:00", "2026-10-27 16:00", lambda x: 4000.0 if x < P1.close_utc else 4040.0)}
    ctx = MacroContext(bars).for_session(S, P1, P2)
    assert ctx["spx_overnight"] == pytest.approx(1.0)
    assert ctx["usd_overnight"] == pytest.approx(1.0)        # EURUSD -1% = dollar +1%
    assert ctx["gold_overnight"] == pytest.approx(1.0)
    # Wreck everything at/after the open: nothing changes.
    wrecked = {k: v.assign(close=v["close"].where(v["time"] < S.open_utc, 1e9)) for k, v in bars.items()}
    assert MacroContext(wrecked).for_session(S, P1, P2) == ctx


def test_missing_or_stale_macro_is_none():
    assert MacroContext({}).for_session(S, P1, P2)["spx_overnight"] is None
    old = {"spx": minute_bars("2026-10-20 12:00", "2026-10-21 12:00", lambda x: 100.0)}
    assert MacroContext(old).for_session(S, P1, P2)["spx_overnight"] is None


def test_alignment_features():
    from app.core.models import Candidate, DataSource
    c = Candidate(id="x", session_day=S.day, direction=Direction.SHORT, signal_time=S.open_utc + pd.Timedelta(minutes=30),
                  entry_ref=100, stop=101, target=98.5, or_high=100.5, or_low=99.5,
                  breakout_time=S.open_utc + pd.Timedelta(minutes=20), retest_time=S.open_utc + pd.Timedelta(minutes=25),
                  spread_at_signal=0.1, rationale="", strategy_version="v", config_hash="h", data_source=DataSource.TEST)
    f = signal_features(c, {"spx_overnight": 0.8, "claude_bias": 1}, S, S.open_utc + pd.Timedelta(minutes=15))
    assert f["macro_align"] == -1 and f["bias_align"] == -1          # short against a rising S&P and a bullish bias
    f2 = signal_features(c, {"spx_overnight": 0.01}, S, S.open_utc + pd.Timedelta(minutes=15))
    assert f2["macro_align"] == 0 and f2["bias_align"] is None


class BiasRunner(FakeRunner):
    def run_json(self, system, prompt, schema):
        return {"data": {"bias": "BULLISH", "confidence": 140, "reasons": ["a", "b"]}, "cost_usd": 0, "duration_ms": 1}


def test_brief_records_a_shadow_bias(tmp_path):
    fk = lambda: FakeMt5(replay_start_utc=datetime(2026, 12, 1, tzinfo=timezone.utc))  # noqa: E731
    c = TestClient(create_app(tmp_path, mt5_factory=fk, mock_factory=fk, allow_non_loopback=True))
    pid = c.post("/api/accounts", json={"label": "m", "kind": "MOCK"}).json()["id"]
    c.post("/api/connection/connect", json={"profile_id": pid})
    c.app.state.st.chat.runner = BiasRunner()
    out = c.post("/api/brief").json()
    assert out["status"] == "DONE" and out["bias"]["bias"] == "BULLISH" and out["bias"]["confidence"] == 100
    rec = c.get("/api/learning/biases").json()
    assert len(rec["biases"]) == 1 and rec["stats"]["scored"] == 0   # not scored before the session has closed


def test_resolve_never_picks_a_stock_over_the_commodity_or_index():
    from app.brokers.assets import BY_KEY, resolve
    rows = [{"name": "GOLD", "path": r"Nasdaq\Stock\GOLD"},          # Barrick Gold, the company
            {"name": "XAUUSD", "path": r"Metals\XAUUSD"},
            {"name": "ESTC", "path": r"NYSE\Stock\ESTC"},
            {"name": "ES", "path": r"NYSE\Stock\ES"},
            {"name": "US500", "path": "Indices/US500"},
            {"name": "USTEC", "path": r"Indices\USTEC"},
            {"name": "SPY", "path": r"ETF\SPY"}]
    assert resolve(BY_KEY["XAUUSD"], rows) == "XAUUSD"
    assert resolve(BY_KEY["US500"], rows) == "US500"
    assert resolve(BY_KEY["US100"], rows) == "USTEC"
    # Order of the broker list must not matter.
    assert resolve(BY_KEY["XAUUSD"], list(reversed(rows))) == "XAUUSD"
    # GOLD as a metal is fine; a stock alone is "not offered", never a stand-in (MetaQuotes: WTI = W&T Offshore).
    assert resolve(BY_KEY["XAUUSD"], [{"name": "GOLD", "path": "Metals/GOLD"}]) == "GOLD"
    assert resolve(BY_KEY["XAUUSD"], [{"name": "GOLD", "path": r"Nasdaq\Stock\GOLD"}]) is None
    assert resolve(BY_KEY["OIL"], [{"name": "WTI", "path": r"Nasdaq\Stock\WTI"}]) is None
    assert resolve(BY_KEY["US500"], [{"name": "ESTC", "path": r"NYSE\Stock\ESTC"}]) is None
    assert resolve(BY_KEY["OIL"], [{"name": "USOIL", "path": r"Nasdaq\Other\USOIL"}]) is None   # exchange-listed note
    assert resolve(BY_KEY["OIL"], [{"name": "USOIL", "path": r"Energies\USOIL"}]) == "USOIL"


def test_resolve_accepts_suffixes_and_prefers_exact_names():
    from app.brokers.assets import BY_KEY, resolve
    assert resolve(BY_KEY["US100"], ["NAS100.m", "US100.mock"]) == "US100.mock"   # base priority first
    assert resolve(BY_KEY["EURUSD"], ["EURUSD.pro", "EURUSD"]) == "EURUSD"
    assert resolve(BY_KEY["EURUSD"], ["EURUSDX_LONGNAME"]) is None
    assert resolve(BY_KEY["OIL"], ["UKOIL", "USOIL-ECN"]) == "USOIL-ECN"          # WTI before Brent
