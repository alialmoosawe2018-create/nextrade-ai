from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.brokers.fake_mt5 import FakeMt5

FUTURE = datetime(2026, 12, 1, tzinfo=timezone.utc)
PASS = "mock-investor-pass"


def run_job(client, body) -> tuple[int, dict]:
    """POST an analysis and wait for the background job; returns (http-like status, payload)."""
    import time
    r = client.post("/api/analysis/run", json=body)
    if r.status_code != 202:
        return r.status_code, r.json()
    jid = r.json()["job_id"]
    for _ in range(600):
        j = client.get(f"/api/analysis/jobs/{jid}").json()
        if j["status"] != "RUNNING":
            return (200, j["result"]) if j["status"] == "DONE" else (409, {"detail": j["error"]})
        time.sleep(0.05)
    raise AssertionError("analysis job did not finish")


def make_client(tmp_path, fake=None) -> TestClient:
    fake = fake or FakeMt5(replay_start_utc=FUTURE)
    app = create_app(tmp_path, mt5_factory=lambda: fake, mock_factory=lambda: FakeMt5(replay_start_utc=FUTURE),
                     allow_non_loopback=True)
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


def mock_connected(client) -> str:
    pid = client.post("/api/accounts", json={"label": "Mock", "kind": "MOCK"}).json()["id"]
    r = client.post("/api/connection/connect", json={"profile_id": pid})
    assert r.status_code == 200, r.text
    return pid


def test_health_reports_locked_real_trading(client):
    h = client.get("/api/health").json()
    assert h["real_trading"] == "LOCKED" and h["mode"].startswith("SIMULATION")
    assert h["connection"] == {"connected": False}


def test_non_loopback_clients_are_refused(tmp_path):
    c = TestClient(create_app(tmp_path))  # TestClient host is "testclient", not loopback
    assert c.get("/api/health").status_code == 403


def test_account_profiles_never_accept_a_password(client, tmp_path):
    r = client.post("/api/accounts", json={"label": "X", "server": "S", "login": 5, "password": "nope"})
    assert r.status_code == 422
    assert client.post("/api/accounts", json={"label": "X", "kind": "MT5"}).status_code == 422  # no server
    ok = client.post("/api/accounts", json={"label": "PrimeX demo", "server": "PrimeXCapitalLtd-Server", "login": 5})
    assert ok.status_code == 201 and "password" not in ok.json()
    assert "nope" not in (tmp_path / "accounts.json").read_text(encoding="utf-8")


def test_mt5_credentials_connect_and_no_password_anywhere(tmp_path):
    client = make_client(tmp_path)
    pid = client.post("/api/accounts", json={"label": "Demo", "server": "NexTrade-MOCK", "login": 1234567}).json()["id"]
    bad = client.post("/api/connection/connect", json={"profile_id": pid, "method": "credentials",
                                                       "password": "wrong-secret-42"})
    assert bad.status_code == 502 and "wrong-secret-42" not in bad.text
    good = client.post("/api/connection/connect", json={"profile_id": pid, "method": "credentials", "password": PASS})
    assert good.status_code == 200 and PASS not in good.text
    body = good.json()
    assert body["connected"] and body["source"] == "DEMO" and body["read_only"] is True
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert PASS not in f.read_text(encoding="utf-8", errors="ignore"), f
    assert PASS not in client.get("/api/health").text + client.get("/api/connection/status").text


def test_symbols_and_spec_flow(client):
    mock_connected(client)
    syms = client.get("/api/symbols").json()
    assert [s["name"] for s in syms] == ["US100.mock"]
    sel = client.post("/api/instrument/select", json={"symbol": "US100.mock"}).json()
    assert sel["missing"] == [] and sel["spec"]["source"] == "MT5_SYMBOL_INFO"
    assert client.post("/api/instrument/select", json={"symbol": "NOPE"}).status_code == 404
    q = client.get("/api/market/quote").json()
    assert q["available"] and q["source"] == "MOCK"


def test_endpoints_require_connection(client):
    assert client.get("/api/symbols").status_code == 409
    assert client.post("/api/live/start").status_code == 409
    assert client.post("/api/analysis/run", json={"start": "2026-09-01", "end": "2026-09-30"}).status_code == 409


def test_settings_validation_and_reset(client):
    r = client.put("/api/settings/risk", json={"risk_per_trade_pct": 5})
    assert r.status_code == 422 and r.json()["detail"][0]["field"] == "risk_per_trade_pct"
    ok = client.put("/api/settings/risk", json={"risk_per_trade_pct": 0.25, "max_spread": 5.0})
    assert ok.status_code == 200 and ok.json()["values"]["max_spread"] == 5.0
    assert client.get("/api/settings").json()["values"]["risk"]["risk_per_trade_pct"] == 0.25
    assert client.post("/api/settings/risk/reset").json()["values"]["max_spread"] is None
    assert client.put("/api/settings/nope", json={}).status_code == 404


def test_analysis_flow_on_mock(client):
    mock_connected(client)
    client.post("/api/instrument/select", json={"symbol": "US100.mock"})
    code, blocked = run_job(client, {"start": "2026-09-01", "end": "2026-11-20"})
    assert code == 409 and blocked["detail"]["code"] == "SPREAD_LIMIT_NOT_CONFIGURED"
    client.put("/api/settings/risk", json={"max_spread": 5.0})
    code, out = run_job(client, {"start": "2026-09-01", "end": "2026-11-20", "split": "dev"})
    assert code == 200, out
    assert out["data_source"] == "MOCK" and out["summary"]["WARNING"].startswith("MOCK DATA")
    assert out["server_tz"]["kind"] == "ny_shift" and out["server_tz"]["hours"] == 7.0
    assert client.get(out["report_url"]).status_code == 200
    runs = client.get("/api/analysis/runs").json()
    assert runs[0]["run_id"] == out["run_id"] and runs[0]["data_source"] == "MOCK"
    # The decided server-time rule is remembered on the account profile.
    assert client.get("/api/connection/status").json()["server_tz"]["hours"] == 7.0


def test_holdout_runs_once(client):
    mock_connected(client)
    client.post("/api/instrument/select", json={"symbol": "US100.mock"})
    client.put("/api/settings/risk", json={"max_spread": 5.0})
    body = {"start": "2026-09-01", "end": "2026-11-20", "split": "holdout"}
    assert run_job(client, body)[0] == 200
    code, again = run_job(client, body)
    assert code == 409 and again["detail"]["code"] == "HOLDOUT_ALREADY_USED"


def test_symbol_without_history_is_named_clearly(client):
    mock_connected(client)
    client.post("/api/instrument/select", json={"symbol": "EURUSD.mock"})
    client.put("/api/settings/risk", json={"max_spread": 5.0})
    code, out = run_job(client, {"start": "2026-09-01", "end": "2026-11-20"})
    assert code == 409 and out["detail"]["code"] == "NO_HISTORY"


def test_disconnect(client):
    mock_connected(client)
    assert client.post("/api/connection/disconnect").json() == {"connected": False}
    assert client.get("/api/connection/status").json() == {"connected": False}
