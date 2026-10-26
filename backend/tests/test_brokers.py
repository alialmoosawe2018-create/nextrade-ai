from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import SecretStr

from app.brokers.fake_mt5 import MOCK_SERVER_TZ, FakeMt5
from app.brokers.mt5_gateway import READ_ONLY_CALLS, GatewayError, Mt5Gateway, to_utc_bars
from app.brokers.server_time import (CANDIDATES, ServerTz, candidates_matching, choose, measure_offset,
                                     score_candidates)
from app.core.clock import MarketCalendar

PASS = "mock-investor-pass"
SERVER = "NexTrade-MOCK"
FUTURE = datetime(2026, 12, 1, tzinfo=timezone.utc)  # replay clock after the test ranges


def gw(**kw) -> Mt5Gateway:
    return Mt5Gateway(FakeMt5(replay_start_utc=FUTURE, **kw), label_mock=True)


def connected(**kw) -> Mt5Gateway:
    g = gw(**kw)
    g.connect("credentials", login=1234567, password=SecretStr(PASS), server=SERVER)
    return g


# ---------------------------------------------------------------- server time
def test_ny_shift_rule_tracks_us_dst():
    tz = ServerTz("ny_shift", 7.0)
    summer = pd.Timestamp("2026-10-12 13:30", tz="UTC")   # 09:30 EDT
    winter = pd.Timestamp("2026-11-02 14:30", tz="UTC")   # 09:30 EST
    assert pd.Timestamp(tz.utc_to_server_epoch(summer), unit="s") == pd.Timestamp("2026-10-12 16:30")
    assert pd.Timestamp(tz.utc_to_server_epoch(winter), unit="s") == pd.Timestamp("2026-11-02 16:30")


def test_round_trip_and_rules_that_differ_in_the_gap_week():
    utc = pd.Series(pd.date_range("2026-10-20", "2026-11-05", freq="37min", tz="UTC"))
    for tz in CANDIDATES:
        epochs = pd.Series([tz.utc_to_server_epoch(t) for t in utc])
        back = tz.to_utc(epochs)
        ok = back.notna()
        assert (back[ok].values == utc[ok].values).all(), tz.name
    gap_day = datetime(2026, 10, 27, 14, tzinfo=timezone.utc)  # EU already on winter time, US not yet
    nicosia = ServerTz("zone", zone="Europe/Nicosia").offset_at(gap_day)
    ny7 = ServerTz("ny_shift", 7.0).offset_at(gap_day)
    assert nicosia.total_seconds() / 3600 == 2 and ny7.total_seconds() / 3600 == 3


def test_measure_offset_needs_a_fresh_tick():
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    fresh = int(now.timestamp()) + 3 * 3600 - 5
    assert measure_offset(fresh, now) == 3.0
    stale = int(now.timestamp()) + 3 * 3600 - 600
    assert measure_offset(stale, now) is None
    names = {c.name for c in candidates_matching(3.0, now)}
    assert "NY+7" in names and "UTC+3" in names and "UTC+2" not in names


def _raw(g: Mt5Gateway, start: str, end: str) -> pd.DataFrame:
    return g.rates_m1_server("US100.mock", pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"))


def test_history_identifies_the_mock_server_rule():
    g = connected()
    sessions = MarketCalendar().sessions_between(date(2026, 9, 14), date(2026, 11, 20))
    raw = _raw(g, "2026-09-14", "2026-11-20")
    scores = score_candidates(raw, sessions)
    tz, why = choose(scores)
    assert tz == MOCK_SERVER_TZ, why
    by = {s["name"]: s for s in scores}
    ny, cy = by["NY+7"], by["Europe/Nicosia"]
    differ = {d for d in ny["wins"] if ny["center"][d] != cy["center"][d]}
    assert differ and all(ny["wins"][d] and not cy["wins"][d] for d in differ)  # the gap week decides
    assert all(cy["wins"][d] for d in cy["wins"] if d not in differ)


def test_choose_refuses_when_data_cannot_decide():
    g = connected()
    sessions = MarketCalendar().sessions_between(date(2026, 9, 14), date(2026, 9, 18))
    tz, why = choose(score_candidates(_raw(g, "2026-09-14", "2026-09-18"), sessions))
    assert tz is None and why.startswith("INSUFFICIENT DATA")
    # Summer-only data cannot separate NY+7 from Nicosia or UTC+3: they agree on every day.
    sessions = MarketCalendar().sessions_between(date(2026, 6, 1), date(2026, 7, 31))
    tz2, why2 = choose(score_candidates(_raw(g, "2026-06-01", "2026-07-31"), sessions))
    assert tz2 is not None and "identical on this data" in why2


def test_flat_volatility_is_uncertain():
    sessions = MarketCalendar().sessions_between(date(2026, 9, 14), date(2026, 10, 30))
    g = connected()
    raw = _raw(g, "2026-09-14", "2026-10-30")
    raw = raw.assign(high=raw["open"] + 1.0, low=raw["open"] - 1.0)  # no volatility jump anywhere
    tz, why = choose(score_candidates(raw, sessions))
    assert tz is None and why.startswith("UNCERTAIN")


def test_to_utc_bars_puts_the_open_at_1330z():
    g = connected()
    utc = to_utc_bars(_raw(g, "2026-10-12", "2026-10-12"), MOCK_SERVER_TZ)
    assert (utc["time"] == pd.Timestamp("2026-10-12 13:30", tz="UTC")).any()
    assert list(utc.columns) == ["time", "open", "high", "low", "close", "spread"]


# ---------------------------------------------------------------- gateway
def test_connect_with_credentials_is_read_only_mock():
    info = connected().info
    assert info.source == "MOCK" and info.method == "mock"
    assert info.read_only and info.account_mode == "DEMO" and info.warnings == []


def test_wrong_password_fails_without_echoing_it():
    g = gw()
    with pytest.raises(GatewayError) as e:
        g.connect("credentials", login=1234567, password=SecretStr("very-secret-xyz"), server=SERVER)
    assert "very-secret-xyz" not in str(e.value) and not g.connected


def test_password_never_kept_or_exposed():
    g = connected()
    blob = json.dumps(asdict(g.info)) + repr(g.info) + repr(vars(g))
    assert PASS not in blob
    assert repr(SecretStr(PASS)) == "SecretStr('**********')"


def test_attach_to_logged_in_terminal_and_failure():
    assert gw().connect("attach").login == 1234567
    with pytest.raises(GatewayError):
        gw(attach_logged_in=False).connect("attach")


def test_trading_capable_and_real_accounts_get_warnings():
    g = connected(investor_mode=False)
    assert not g.info.read_only and "investor" in g.info.warnings[0]
    fake = FakeMt5(replay_start_utc=FUTURE)
    real = Mt5Gateway(fake)
    fake.account_info = lambda: SimpleNamespace(login=9, trade_mode=2, trade_allowed=False, server="X",
                                                currency="USD", company="Broker", balance=1.0, equity=1.0)
    info = real.connect("credentials", login=1234567, password=SecretStr(PASS), server=SERVER)
    assert info.account_mode == "REAL" and info.source == "LIVE" and any("REAL" in w for w in info.warnings)


def test_no_trading_call_is_possible():
    assert not any(c.startswith(("order", "position", "history")) for c in READ_ONLY_CALLS)
    g = connected()
    with pytest.raises(GatewayError):
        g._call("order_send", {})
    assert "order_send" not in g._m.calls


def test_symbol_discovery_and_spec():
    g = connected()
    syms = g.find_symbols(nasdaq_only=True)
    assert [s["name"] for s in syms] == ["US100.mock"] and syms[0]["nasdaq_candidate"]
    assert {s["name"] for s in g.find_symbols()} == {"US100.mock", "US30.mock", "XAUUSD.mock", "USOIL.mock", "EURUSD.mock"}
    spec, raw = g.instrument_spec("US100.mock")
    assert spec.missing_fields() == [] and spec.source.value == "MT5_SYMBOL_INFO"
    assert (spec.tick_size, spec.tick_value, spec.volume_step) == (0.01, 0.01, 0.1)
    with pytest.raises(GatewayError):
        g.instrument_spec("NOPE")


def test_zero_tick_value_is_missing_not_guessed(monkeypatch):
    g = connected()
    orig = g._m._sym
    monkeypatch.setattr(g._m, "_sym", lambda n: SimpleNamespace(**{**vars(orig(n)), "trade_tick_value": 0.0}))
    spec, _ = g.instrument_spec("US100.mock")
    assert "tick_value" in spec.missing_fields()


def test_quote_and_measured_offset():
    g = connected()
    q = g.quote("US100.mock")
    assert q["ask"] > q["bid"]
    now = g._m.now_utc()
    assert g.measured_offset_hours("US100.mock", now) in (None, 2.0, 3.0)


def test_live_clock_breaks_ties_that_history_cannot(tmp_path, monkeypatch):
    """Summer-only history fits UTC+3, Nicosia and NY+7 equally; on 2026-10-26 they differ (+3/+2/+3)."""
    from app.services.analysis import AnalysisBlocked, ensure_server_tz
    from app.storage.store import Profile, ProfileStore
    g = connected()
    store = ProfileStore(tmp_path)
    now = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    # No live measurement -> refuse instead of picking the wrong twin.
    monkeypatch.setattr(g, "measured_offset_hours", lambda sym, n=None: None)
    p = store.save(Profile(label="x", kind="MOCK"))
    with pytest.raises(AnalysisBlocked) as e:
        ensure_server_tz(g, store, p, "US100.mock", date(2026, 6, 1), date(2026, 7, 31), now=now)
    assert e.value.reason == "SERVER_TIME_AMBIGUOUS"
    # Live clock says UTC+2 -> only Nicosia fits both history and today.
    monkeypatch.setattr(g, "measured_offset_hours", lambda sym, n=None: 2.0)
    rec = ensure_server_tz(g, store, p, "US100.mock", date(2026, 6, 1), date(2026, 7, 31), now=now)
    assert (rec.kind, rec.zone) == ("zone", "Europe/Nicosia") and "live clock UTC+2" in rec.evidence
