"""NexTrade AI local API (FastAPI). Run: uvicorn app.api.main:app --host 127.0.0.1 --port 8000

Local-only by design: requests from non-loopback clients are refused (defence in depth on
top of binding to 127.0.0.1). No endpoint can place an order; the MT5 gateway is read-only.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.brokers.fake_mt5 import FakeMt5
from app.brokers.mt5_gateway import GatewayError, Mt5Gateway, load_mt5_module, to_utc_bars
from app.core.clock import NEW_YORK, MarketCalendar, to_local
from app.core.config import InstrumentSpec, config_hash
from app.core.models import DataSource
from app.core.safety import REAL_TRADING_ENABLED
from app.services.analysis import AnalysisBlocked, run_analysis
from app.services.live import LiveMonitor
from app.services.splits import SPLITS
from app.api.assistant import register_assistant
from app.api.learning import register_learning
from app.market.news import NewsCalendar
from app.storage.db import Db
from app.storage.store import SETTINGS_MODELS, Profile, ProfileStore, SettingsStore

ROOT = Path(__file__).resolve().parents[3]
VERSION = "0.2.0-platform"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class AppState:
    def __init__(self, data_dir: Path, mt5_factory=None, mock_factory=None) -> None:
        self.data_dir = Path(data_dir)
        self.profiles = ProfileStore(self.data_dir)
        self.settings = SettingsStore(self.data_dir)
        self.mt5_factory = mt5_factory or load_mt5_module
        self.mock_factory = mock_factory or _default_mock
        self.gateway: Mt5Gateway | None = None
        self.profile: Profile | None = None
        self.spec: InstrumentSpec | None = None
        self.spec_raw: dict | None = None
        self.monitor: LiveMonitor | None = None
        self.clock = lambda: datetime.now(timezone.utc)
        self.lock = threading.RLock()
        self.errors: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.ideas: dict[str, dict] = {}
        self.chat = None
        self.db = Db(self.data_dir / "nextrade.db")
        self.news = NewsCalendar(self.data_dir / "news")

    def record_error(self, where: str, msg: str) -> None:
        self.errors.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "where": where, "error": msg})
        del self.errors[:-50]


def _default_mock() -> FakeMt5:
    """MOCK broker replaying the most recent past session at 10x, so the live page can be watched any time.
    (10x keeps mock time per 2-second poll at ~20s, well inside the 90s data-age limit.)"""
    cal = MarketCalendar()
    d = datetime.now(timezone.utc).date() - timedelta(days=1)
    s = None
    while s is None:
        s = cal.session(d)
        d -= timedelta(days=1)
    return FakeMt5(replay_start_utc=(s.open_utc - pd.Timedelta(minutes=5)).to_pydatetime(), speed=10.0)


# ------------------------------------------------------------------ schemas
class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a "password" key is rejected, never stored
    label: str = Field(min_length=1, max_length=60)
    kind: Literal["MT5", "MOCK"] = "MT5"
    server: str = Field(default="", max_length=120)
    login: int | None = Field(default=None, ge=1)
    terminal_path: str | None = Field(default=None, max_length=400)


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    method: Literal["credentials", "attach"] = "attach"
    password: SecretStr | None = None


class SymbolIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)


class AnalysisIn(BaseModel):
    start: date
    end: date
    split: Literal["dev", "validation", "holdout", "all"] = "dev"
    allow_holdout_reuse: bool = False


def create_app(data_dir: Path | None = None, mt5_factory=None, mock_factory=None,
               allow_non_loopback: bool = False) -> FastAPI:
    data_dir = Path(data_dir or ROOT / "data")
    st = AppState(data_dir, mt5_factory, mock_factory)
    app = FastAPI(title="NexTrade AI", version=VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.st = st
    (data_dir / "runs").mkdir(parents=True, exist_ok=True)
    app.mount("/reports", StaticFiles(directory=data_dir / "runs", html=True), name="reports")

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host = request.client.host if request.client else ""
        if not allow_non_loopback and host not in LOOPBACK:
            return JSONResponse({"detail": "local access only"}, status_code=403)
        return await call_next(request)

    def need_gateway() -> Mt5Gateway:
        if st.gateway is None or not st.gateway.connected:
            raise HTTPException(409, detail={"code": "NOT_CONNECTED", "message": "Connect an MT5 account first"})
        return st.gateway

    def need_symbol() -> tuple[Mt5Gateway, InstrumentSpec]:
        gw = need_gateway()
        if st.spec is None:
            raise HTTPException(409, detail={"code": "NO_SYMBOL", "message": "Select the Nasdaq symbol first"})
        return gw, st.spec

    def conn_payload() -> dict:
        gw = st.gateway
        if gw is None or gw.info is None:
            return {"connected": False}
        return {"connected": True, "profile_id": st.profile.id if st.profile else None,
                "profile_label": st.profile.label if st.profile else None, **asdict(gw.info),
                "symbol": st.spec.symbol if st.spec else None,
                "server_tz": st.profile.server_tz.model_dump() if st.profile and st.profile.server_tz else None}

    # ------------------------------------------------------------- health
    @app.get("/api/health")
    def health():
        mod = None
        try:
            mod = st.mt5_factory()
        except Exception:  # noqa: BLE001
            mod = None
        return {"status": "ok", "version": VERSION, "mode": "SIMULATION / PAPER",
                "real_trading": "UNLOCKED" if REAL_TRADING_ENABLED else "LOCKED",
                "mt5_package": "installed" if mod is not None else "missing",
                "connection": conn_payload(),
                "live_monitor": st.monitor.status if st.monitor else "OFF",
                "recent_errors": st.errors[-10:]}

    # ------------------------------------------------------------ accounts
    @app.get("/api/accounts")
    def list_accounts():
        return [p.model_dump(mode="json") for p in st.profiles.all()]

    @app.post("/api/accounts", status_code=201)
    def add_account(body: ProfileIn):
        if body.kind == "MT5" and not body.server:
            raise HTTPException(422, detail="server is required for an MT5 account")
        p = Profile(**body.model_dump())
        if p.kind == "MOCK":
            p.server, p.login = "NexTrade-MOCK", 1234567
        return st.profiles.save(p).model_dump(mode="json")

    @app.delete("/api/accounts/{pid}")
    def delete_account(pid: str):
        if st.profile and st.profile.id == pid and st.gateway and st.gateway.connected:
            raise HTTPException(409, detail="Disconnect this account before deleting it")
        if not st.profiles.delete(pid):
            raise HTTPException(404, detail="no such account")
        return {"deleted": pid}

    # ---------------------------------------------------------- connection
    @app.post("/api/connection/connect")
    def connect(body: ConnectIn):
        with st.lock:
            profile = st.profiles.get(body.profile_id)
            if profile is None:
                raise HTTPException(404, detail="no such account")
            _stop_monitor(st)
            if st.gateway is not None:
                st.gateway.disconnect()
            if profile.kind == "MOCK":
                fake = st.mock_factory()
                gw = Mt5Gateway(fake, label_mock=True)
                st.clock = fake.now_utc
                method, login, server = "attach", None, None
            else:
                module = st.mt5_factory()
                if module is None:
                    raise HTTPException(503, detail={"code": "MT5_PACKAGE_MISSING",
                                                     "message": "The MetaTrader5 Python package is not installed"})
                gw = Mt5Gateway(module)
                st.clock = lambda: datetime.now(timezone.utc)
                method, login, server = body.method, profile.login, profile.server
            try:
                gw.connect(method, login=login, password=body.password, server=server,
                           terminal_path=profile.terminal_path)
            except GatewayError as e:
                st.record_error("connect", str(e))
                raise HTTPException(502, detail={"code": "CONNECT_FAILED", "message": str(e)}) from None
            st.gateway, st.profile, st.spec, st.spec_raw = gw, profile, None, None
            if profile.symbol:
                try:
                    st.spec, st.spec_raw = gw.instrument_spec(profile.symbol)
                except GatewayError as e:
                    st.record_error("symbol", str(e))
            return conn_payload()

    @app.post("/api/connection/disconnect")
    def disconnect():
        with st.lock:
            _stop_monitor(st)
            if st.gateway is not None:
                st.gateway.disconnect()
            st.gateway, st.profile, st.spec, st.spec_raw = None, None, None, None
            return {"connected": False}

    @app.get("/api/connection/status")
    def status():
        if st.gateway is not None and st.gateway.connected:
            try:
                st.gateway.refresh_account()
            except GatewayError as e:
                st.record_error("status", str(e))
        return conn_payload()

    # ------------------------------------------------------------- symbols
    @app.get("/api/symbols")
    def symbols(q: str | None = None, nasdaq_only: bool = True):
        try:
            return need_gateway().find_symbols(q, nasdaq_only=nasdaq_only)
        except GatewayError as e:
            raise HTTPException(502, detail=str(e)) from None

    @app.post("/api/instrument/select")
    def select_symbol(body: SymbolIn):
        gw = need_gateway()
        try:
            spec, raw = gw.instrument_spec(body.symbol)
        except GatewayError as e:
            raise HTTPException(404, detail=str(e)) from None
        with st.lock:
            _stop_monitor(st)
            st.spec, st.spec_raw = spec, raw
            st.profile.symbol = body.symbol
            st.profiles.save(st.profile)
        return {"spec": spec.model_dump(mode="json"), "raw": raw, "missing": spec.missing_fields()}

    @app.get("/api/instrument")
    def instrument():
        if st.spec is None:
            return {"spec": None}
        return {"spec": st.spec.model_dump(mode="json"), "raw": st.spec_raw, "missing": st.spec.missing_fields()}

    # -------------------------------------------------------------- market
    @app.get("/api/market/quote")
    def quote():
        gw, spec = need_symbol()
        q = gw.quote(spec.symbol)
        if q is None:
            return {"available": False}
        offset = gw.measured_offset_hours(spec.symbol, st.clock())
        return {"available": True, **q, "measured_server_offset_hours": offset,
                "source": gw.info.source}

    @app.get("/api/market/candles")
    def candles(day: date | None = None):
        gw, spec = need_symbol()
        if st.profile.server_tz is None:
            raise HTTPException(409, detail={"code": "SERVER_TIME_UNDETERMINED",
                                             "message": "Run an analysis once so the server time rule is determined"})
        cal = MarketCalendar()
        now = pd.Timestamp(st.clock())
        d = day or now.tz_convert(NEW_YORK).date()
        s = cal.session(d)
        if s is None:
            return {"day": str(d), "session": None, "candles": []}
        raw = gw.rates_m1_server(spec.symbol, s.open_utc, min(s.close_utc, now))
        bars = to_utc_bars(raw, st.profile.server_tz.to_tz())
        bars = bars[(bars["time"] >= s.open_utc) & (bars["time"] < s.close_utc) &
                    (bars["time"] + pd.Timedelta(minutes=1) <= now)]
        return {"day": str(d), "source": gw.info.source,
                "session": {"open_utc": str(s.open_utc), "close_utc": str(s.close_utc),
                            "open_baghdad": str(to_local(s.open_utc)), "early_close": s.early_close},
                "candles": [{"time": int(t.timestamp()), "open": o, "high": h, "low": l, "close": c}
                            for t, o, h, l, c in zip(bars["time"], bars["open"], bars["high"],
                                                     bars["low"], bars["close"])]}

    # ------------------------------------------------------------- analysis
    def start_analysis(gw: Mt5Gateway, spec: InstrumentSpec, start: date, end: date, split: str,
                       allow_holdout_reuse: bool = False) -> dict:
        """Starts a background job (history download can take minutes); poll /api/analysis/jobs/{id}."""
        body = AnalysisIn(start=start, end=end, split=split, allow_holdout_reuse=allow_holdout_reuse)
        if body.end < body.start:
            raise HTTPException(422, detail="end must be after start")
        with st.lock:
            if any(j["status"] == "RUNNING" for j in st.jobs.values()):
                raise HTTPException(409, detail={"code": "ANALYSIS_BUSY", "message": "Another analysis is running"})
            job_id = uuid.uuid4().hex[:12]
            job = {"job_id": job_id, "status": "RUNNING", "params": {**body.model_dump(mode="json"), "symbol": spec.symbol},
                   "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "finished_at": None, "result": None, "error": None}
            st.jobs[job_id] = job
            profile = st.profile
            args = (gw, st.profiles, profile, spec.symbol, spec, st.settings.get("strategy"), st.settings.get("risk"),
                    st.settings.get("costs"), st.settings.get("paper").starting_balance, body.start, body.end,
                    body.split, st.data_dir, body.allow_holdout_reuse, st.db)

        def work():
            try:
                job["result"] = run_analysis(*args)
                job["status"] = "DONE"
            except AnalysisBlocked as e:
                job["status"], job["error"] = "FAILED", {"code": e.reason, **e.details}
            except PermissionError as e:
                job["status"], job["error"] = "FAILED", {"code": "HOLDOUT_ALREADY_USED", "message": str(e)}
            except GatewayError as e:
                st.record_error("analysis", str(e))
                job["status"], job["error"] = "FAILED", {"code": "GATEWAY_ERROR", "message": str(e)}
            except Exception as e:  # noqa: BLE001 — report, never crash the API
                st.record_error("analysis", f"{type(e).__name__}: {e}")
                job["status"], job["error"] = "FAILED", {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}"}
            finally:
                job["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        threading.Thread(target=work, name=f"analysis-{job_id}", daemon=True).start()
        return {"job_id": job_id, "status": "RUNNING"}

    @app.post("/api/analysis/run", status_code=202)
    def analysis(body: AnalysisIn):
        gw, spec = need_symbol()
        return start_analysis(gw, spec, body.start, body.end, body.split, body.allow_holdout_reuse)

    @app.get("/api/analysis/jobs/{job_id}")
    def analysis_job(job_id: str):
        job = st.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, detail="no such job")
        return job

    @app.get("/api/analysis/runs")
    def runs():
        out = []
        for d in sorted((st.data_dir / "runs").glob("*"), reverse=True)[:50]:
            if (d / "summary.json").exists():
                import json
                s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
                r = json.loads((d / "run.json").read_text(encoding="utf-8"))
                out.append({"run_id": d.name, "report_url": f"/reports/{d.name}/report.html",
                            "data_source": r.get("data_source"), "split": r.get("split"),
                            "symbol": r.get("symbol"), "created_utc": r.get("created_utc"),
                            "status": s.get("status"), "trades": s.get("trades"),
                            "expectancy_r": (s.get("r") or {}).get("expectancy_r")})
        return out

    # ----------------------------------------------------------------- live
    def start_live() -> dict:
        gw, spec = need_symbol()
        if not st.settings.get("risk").trading_enabled:
            raise HTTPException(409, detail={"code": "TRADING_DISABLED",
                                             "message": "Kill switch is on — release it first"})
        if st.profile.server_tz is None:
            raise HTTPException(409, detail={"code": "SERVER_TIME_UNDETERMINED",
                                             "message": "Run an analysis once so the server time rule is determined"})
        risk = st.settings.get("risk")
        if risk.max_spread is None:
            raise HTTPException(409, detail={"code": "SPREAD_LIMIT_NOT_CONFIGURED",
                                             "message": "Set the maximum spread in Risk settings first"})
        with st.lock:
            _stop_monitor(st)
            source = DataSource.MOCK if gw.info.source == "MOCK" else (
                DataSource.DEMO if gw.info.source == "DEMO" else DataSource.LIVE)
            st.monitor = LiveMonitor(gw, spec.symbol, spec, st.profile.server_tz.to_tz(),
                                     st.settings.get("strategy"), risk, st.settings.get("costs"),
                                     st.settings.get("paper").starting_balance, source,
                                     st.data_dir / "live", st.clock, poll_seconds=2.0, db=st.db, news=st.news)
            st.monitor.start()
        return st.monitor.snapshot()

    @app.post("/api/live/start")
    def live_start():
        return start_live()

    @app.post("/api/live/stop")
    def live_stop():
        _stop_monitor(st)
        return {"status": "STOPPED"}

    @app.get("/api/live/status")
    def live_status():
        if st.monitor is None:
            return {"status": "OFF"}
        return st.monitor.snapshot()

    # ------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        vals = {n: st.settings.get(n).model_dump(mode="json") for n in SETTINGS_MODELS}
        defaults = {n: st.settings.default(n).model_dump(mode="json") for n in SETTINGS_MODELS}
        h = config_hash(st.settings.get("strategy"), st.settings.get("risk"), st.settings.get("costs"))
        return {"values": vals, "defaults": defaults, "config_hash": h}

    @app.put("/api/settings/{name}")
    def put_settings(name: str, body: dict):
        if name not in SETTINGS_MODELS:
            raise HTTPException(404, detail="unknown settings section")
        try:
            model = st.settings.put(name, body)
        except ValidationError as e:
            raise HTTPException(422, detail=[{"field": ".".join(str(x) for x in err["loc"]), "error": err["msg"]}
                                             for err in e.errors()]) from None
        restarted = st.monitor is not None
        _stop_monitor(st)
        return {"values": model.model_dump(mode="json"), "live_monitor_stopped": restarted}

    @app.post("/api/settings/{name}/reset")
    def reset_settings(name: str):
        if name not in SETTINGS_MODELS:
            raise HTTPException(404, detail="unknown settings section")
        _stop_monitor(st)
        return {"values": st.settings.reset(name).model_dump(mode="json")}

    @app.get("/api/splits")
    def splits():
        return {"splits": list(SPLITS) + ["all"]}

    register_assistant(app, st, need_gateway=need_gateway, need_symbol=need_symbol, start_live=start_live,
                       start_analysis=start_analysis, stop_monitor=lambda: _stop_monitor(st))
    register_learning(app, st, need_gateway=need_gateway, stop_monitor=lambda: _stop_monitor(st))
    return app


def _stop_monitor(st: AppState) -> None:
    if st.monitor is not None:
        st.monitor.stop()
        st.monitor = None


app = create_app()
