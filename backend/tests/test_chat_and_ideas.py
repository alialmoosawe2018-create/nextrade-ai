from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.brokers.assets import BY_KEY, mentioned, resolve
from app.brokers.fake_mt5 import FakeMt5
from app.services import chat as chat_mod
from app.services.chat import STOP_PATTERN, ChatUnavailable, ClaudeCliRunner, validate_actions

FUTURE = datetime(2026, 12, 1, tzinfo=timezone.utc)
APP_DIR = Path(__file__).resolve().parents[1] / "app"


class FakeRunner:
    def __init__(self, reply="تحليل تجريبي", actions=None, error: ChatUnavailable | None = None):
        self.reply, self.actions, self.error = reply, actions or [], error
        self.calls: list[tuple[str, str]] = []
        self.model = "fake"

    def run(self, system, prompt, schema):
        self.calls.append((system, prompt))
        if self.error:
            raise self.error
        return {"reply": self.reply, "actions": self.actions, "cost_usd": 0.0, "duration_ms": 1}


@pytest.fixture
def client(tmp_path):
    fk = lambda: FakeMt5(replay_start_utc=datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc), speed=0.0)  # noqa: E731
    c = TestClient(create_app(tmp_path, mt5_factory=fk, mock_factory=fk, allow_non_loopback=True))
    pid = c.post("/api/accounts", json={"label": "m", "kind": "MOCK"}).json()["id"]
    assert c.post("/api/connection/connect", json={"profile_id": pid}).status_code == 200
    c.post("/api/instrument/select", json={"symbol": "US100.mock"})
    return c


def runner_of(client, runner):
    client.app.state.st.chat.runner = runner
    return runner


def chat(client, message) -> dict:
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200, r.text
    out = r.json()
    if out["status"] == "DONE":
        return out
    for _ in range(400):
        job = client.get(f"/api/chat/jobs/{out['job_id']}").json()
        if job["status"] != "RUNNING":
            return job
        time.sleep(0.02)
    raise AssertionError("chat job did not finish")


# ---------------------------------------------------------------- assets
def test_asset_words_respect_arabic_edges():
    assert [a.key for a in mentioned("حلل الذهب والنفط")] == ["XAUUSD", "OIL"]
    assert mentioned("شنو الفرق بين") == []          # «بين» must not read as yen
    assert [a.key for a in mentioned("شلون الين اليوم")] == ["USDJPY"]
    assert resolve(BY_KEY["OIL"], ["UKOIL", "USOIL.m"]) == "USOIL.m"
    assert resolve(BY_KEY["US100"], ["USTEC", "US100.pro"]) == "USTEC"
    assert resolve(BY_KEY["XAUUSD"], ["EURUSD"]) is None


# ---------------------------------------------------------------- stop is deterministic
@pytest.mark.parametrize("msg", ["وقف", "اوقف كل شي", "stop", "STOP all!", " توقف "])
def test_stop_words_match(msg):
    assert STOP_PATTERN.match(msg)


@pytest.mark.parametrize("msg", ["وقف المراقبة بعد الافتتاح", "شلون اوقف الخسارة؟", "stop loss"])
def test_longer_sentences_are_not_blind_kills(msg):
    assert not STOP_PATTERN.match(msg)


def test_stop_command_never_reaches_the_model(client):
    fake = runner_of(client, FakeRunner())
    client.put("/api/settings/risk", json={"max_spread": 5.0})
    out = chat(client, "وقف")
    assert fake.calls == []
    assert out["messages"][-1]["deterministic"] is True
    assert client.get("/api/settings").json()["values"]["risk"]["trading_enabled"] is False
    assert client.post("/api/live/start").json()["detail"]["code"] == "TRADING_DISABLED"
    assert client.post("/api/kill/release").json() == {"trading_enabled": True}


# ---------------------------------------------------------------- context
def test_context_has_facts_for_named_assets_and_no_secrets(client):
    fake = runner_of(client, FakeRunner())
    out = chat(client, "حلل الذهب")
    assert out["status"] == "DONE"
    prompt = fake.calls[0][1]
    ctx = json.loads(prompt.split("CONTEXT (facts from the platform, JSON):\n", 1)[1].split("\n\nRECENT CONVERSATION", 1)[0])
    assert ctx["connection"]["source"] == "MOCK"
    assert ctx["market"][0]["symbol"] == "XAUUSD.mock" and ctx["market"][0]["last"] is not None
    assert {w["asset"]: w["symbol"] for w in ctx["watchlist"]}["OIL"] == "USOIL.mock"
    assert "NO ORDERS ARE SENT" in ctx["execution"]
    assert "password" not in prompt.lower()


# ---------------------------------------------------------------- ideas via chat
def _gold_levels(client):
    q = client.get("/api/watchlist").json()
    g = next(w for w in q if w["asset"] == "XAUUSD")
    return g["bid"], g["ask"]


def test_propose_trade_creates_a_sized_manual_idea(client):
    bid, ask = _gold_levels(client)
    sl, tp = round(ask - 8.0, 2), round(ask + 16.0, 2)
    runner_of(client, FakeRunner(actions=[{"type": "propose_trade", "asset": "XAUUSD", "side": "BUY", "sl": sl, "tp": tp,
                                           "rationale": "test"}]))
    out = chat(client, "افتح صفقة شراء ذهب")
    act = out["messages"][-1]["actions"][0]
    assert act["ok"], act
    idea = act["detail"]
    assert idea["execution"].startswith("MANUAL") and idea["status"] == "PROPOSED"
    # 0.5% of 10,000 = 50; loss per lot = (dist + 2*0.5 slippage) * (1.0/0.01) — floored to 0.01 lots.
    dist = ask - sl
    expected = int(50 / ((dist + 1.0) * 100) * 100) / 100
    assert idea["volume"] == pytest.approx(expected) and idea["loss_at_sl"] <= 50
    assert client.get("/api/ideas").json()[0]["id"] == idea["id"]


def test_bad_ideas_are_refused_with_reasons(client):
    bid, ask = _gold_levels(client)
    runner_of(client, FakeRunner(actions=[
        {"type": "propose_trade", "asset": "XAUUSD", "side": "BUY", "sl": ask + 5},          # stop above entry
        {"type": "propose_trade", "asset": "XAUUSD", "side": "BUY", "sl": ask - 0.05},       # spread > 25% of stop
        {"type": "propose_trade", "asset": "GBPUSD", "side": "SELL", "sl": 2.0},             # not at this broker
    ]))
    acts = chat(client, "اقتراحات")["messages"][-1]["actions"]
    assert [a["code"] for a in acts] == ["STOP_WRONG_SIDE", "SPREAD_TOO_WIDE_FOR_STOP", "ASSET_NOT_AT_BROKER"]
    assert all(not a["ok"] for a in acts)


def test_malformed_model_actions_are_dropped():
    ok, dropped = validate_actions([{"type": "order_send", "symbol": "X"}, {"type": "propose_trade", "asset": "US100",
                                    "side": "BUY"}, "junk", {"type": "stop_live"},
                                    {"type": "propose_trade", "asset": "US100", "side": "BUY", "sl": 1, "risk_pct": 50}])
    assert [a["type"] for a in ok] == ["stop_live", "propose_trade"] and ok[1]["risk_pct"] is None
    assert len(dropped) == 3


def test_kill_switch_blocks_new_ideas(client):
    client.post("/api/kill")
    bid, ask = _gold_levels(client)
    runner_of(client, FakeRunner(actions=[{"type": "propose_trade", "asset": "XAUUSD", "side": "BUY", "sl": ask - 8}]))
    act = chat(client, "افتح ذهب")["messages"][-1]["actions"][0]
    assert not act["ok"] and act["code"] == "TRADING_DISABLED"


def test_model_unavailable_is_reported(client):
    runner_of(client, FakeRunner(error=ChatUnavailable("CLAUDE_NOT_LOGGED_IN", "run claude then /login")))
    job = chat(client, "حلل US100")
    assert job["status"] == "FAILED" and job["error"]["code"] == "CLAUDE_NOT_LOGGED_IN"


# ---------------------------------------------------------------- CLI runner parsing
def _fake_cli(monkeypatch, payload: dict):
    def run(cmd, **kw):
        assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == ""   # the model gets no tools at all
        assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(chat_mod.subprocess, "run", run)


def test_cli_runner_parses_structured_output(monkeypatch, tmp_path):
    _fake_cli(monkeypatch, {"is_error": False, "result": "", "structured_output": {"reply": "تمام", "actions": []},
                            "total_cost_usd": 0.01, "duration_ms": 900})
    out = ClaudeCliRunner(tmp_path, exe="claude.exe").run("s", "p", {})
    assert out["reply"] == "تمام" and out["actions"] == []


def test_cli_runner_maps_not_logged_in(monkeypatch, tmp_path):
    _fake_cli(monkeypatch, {"is_error": True, "result": "Not logged in · Please run /login"})
    with pytest.raises(ChatUnavailable) as e:
        ClaudeCliRunner(tmp_path, exe="claude.exe").run("s", "p", {})
    assert e.value.code == "CLAUDE_NOT_LOGGED_IN"


# ---------------------------------------------------------------- no order path anywhere
def test_no_order_sending_code_exists_in_the_app():
    offenders = [str(p) for p in APP_DIR.rglob("*.py") if "order_send(" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_finds_claude_inside_the_packaged_app_folder(tmp_path, monkeypatch):
    exe = tmp_path / "Local" / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / "claude-code" / "9.9.9" / "h" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))   # outside the app: no redirected view
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert chat_mod.find_claude_exe() == str(exe)


def test_finds_claude_in_the_claude_data_folder(tmp_path, monkeypatch):
    # App 3.6556+ keeps Claude Code under %LOCALAPPDATA%/Claude-Data/claude_<id>/claude-code.
    exe = tmp_path / "Local" / "Claude-Data" / "claude_abc123" / "claude-code" / "2.1.308" / "h" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(chat_mod.shutil, "which", lambda _n: None)
    assert chat_mod.find_claude_exe() == str(exe)
