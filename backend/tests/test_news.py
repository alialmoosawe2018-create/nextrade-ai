from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.brokers.fake_mt5 import FakeMt5
from app.core.config import RiskConfig
from app.market.news import NewsCalendar, news_block
from app.risk.engine import MarketSnapshot, evaluate
from tests.test_execution_paper import T5, cand
from tests.test_risk_engine import ACCOUNT

FEED = [
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-27T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
    {"title": "Federal Funds Rate", "country": "USD", "date": "2026-10-28T14:00:00-04:00", "impact": "High", "forecast": "3.75%", "previous": "4.00%"},
    {"title": "German ifo", "country": "EUR", "date": "2026-10-27T10:00:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Retail Sales", "country": "USD", "date": "2026-10-27T10:30:00-04:00", "impact": "Medium", "forecast": "", "previous": ""},
]
RISK = RiskConfig(max_spread=5.0)


def cal(tmp_path, feed=FEED):
    return NewsCalendar(tmp_path, fetcher=lambda: feed)


def at(s):
    return pd.Timestamp(s, tz="UTC")


def test_blackout_window_around_high_usd_news(tmp_path):
    ev = cal(tmp_path).events()
    assert news_block(ev, at("2026-10-27 12:05"), RISK) and "CPI" in news_block(ev, at("2026-10-27 12:05"), RISK)  # 08:05 ET
    assert news_block(ev, at("2026-10-27 12:59"), RISK)            # 08:59 ET, inside +30
    assert news_block(ev, at("2026-10-27 13:31"), RISK) is None    # 09:31 ET: CPI is over
    assert news_block(ev, at("2026-10-27 14:00"), RISK) is None    # EUR high and USD medium don't block


def test_rate_decision_skips_the_whole_day(tmp_path):
    ev = cal(tmp_path).events()
    reason = news_block(ev, at("2026-10-28 13:40"), RISK)          # 09:40 ET, hours before the decision
    assert reason.startswith("SKIP_DAY") and "Federal Funds Rate" in reason


def test_unknown_calendar_never_pretends_all_clear(tmp_path):
    def broken():
        raise OSError("offline")
    c = NewsCalendar(tmp_path, fetcher=broken)
    assert c.events() is None and c.status()["available"] is False and "offline" in c.status()["last_error"]
    assert news_block(None, at("2026-10-27 12:30"), RISK) is None


def test_failed_refresh_keeps_last_good_cache(tmp_path):
    c = cal(tmp_path)
    assert len(c.events()) == 4
    c._fetch = lambda: (_ for _ in ()).throw(OSError("down"))
    c.refresh(force=True)
    assert len(c.events()) == 4 and "down" in c.last_error


def test_risk_engine_rejects_during_news(spec, costs):
    m = MarketSnapshot(now=T5, last_bar_close=T5, spread=0.5, news_block="CPI m/m 12:30Z")
    d = evaluate(cand(), ACCOUNT, m, spec, RISK, costs)
    assert not d.approved and d.reasons == ("NEWS_BLACKOUT",) and "CPI" in d.details["news"]


def test_news_endpoint_and_chat_context(tmp_path):
    fk = lambda: FakeMt5(replay_start_utc=datetime(2026, 12, 1, tzinfo=timezone.utc))  # noqa: E731
    c = TestClient(create_app(tmp_path, mt5_factory=fk, mock_factory=fk, allow_non_loopback=True))
    c.app.state.st.news = cal(tmp_path / "news")
    c.app.state.st.clock = lambda: datetime(2026, 10, 27, 11, 0, tzinfo=timezone.utc)
    n = c.get("/api/news").json()
    assert n["available"] and [e["title"] for e in n["upcoming"]][:2] == ["CPI m/m", "German ifo"]
    assert n["upcoming"][0]["baghdad"].endswith("15:30")             # 08:30 ET = 15:30 Baghdad
    pid = c.post("/api/accounts", json={"label": "m", "kind": "MOCK"}).json()["id"]
    c.post("/api/connection/connect", json={"profile_id": pid})
    c.app.state.st.clock = lambda: datetime(2026, 10, 27, 11, 0, tzinfo=timezone.utc)  # connect resets the mock clock
    from tests.test_chat_and_ideas import FakeRunner, chat
    fake = FakeRunner()
    c.app.state.st.chat.runner = fake
    chat(c, "شنو الأخبار اليوم؟")
    assert '"economic_calendar"' in fake.calls[0][1] and "CPI m/m" in fake.calls[0][1]


def test_failures_back_off_instead_of_hammering(tmp_path):
    calls = []
    def broken():
        calls.append(1)
        raise OSError("429")
    c = NewsCalendar(tmp_path, fetcher=broken)
    for _ in range(20):
        c.events()
    assert len(calls) == 1


def test_owner_can_switch_the_news_filter_off(tmp_path):
    ev = cal(tmp_path).events()
    off = RiskConfig(max_spread=5.0, news_filter=False)
    assert news_block(ev, at("2026-10-27 12:05"), RISK)            # on: CPI blocks
    assert news_block(ev, at("2026-10-27 12:05"), off) is None     # off: nothing blocks
