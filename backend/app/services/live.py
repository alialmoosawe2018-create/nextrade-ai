"""Live monitor: polls completed M1 bars from the (read-only) gateway and runs the exact same
SessionRunner as the backtest — strategy -> risk -> PAPER broker. No real orders exist.

The risk check uses the real clock, so a signal computed from bars that arrived late
(e.g. when the monitor starts mid-session and backfills) is rejected as STALE_DATA
instead of being traded.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from app.brokers.mt5_gateway import GatewayError, Mt5Gateway, to_utc_bars
from app.brokers.server_time import ServerTz
from app.core.clock import NEW_YORK, MarketCalendar
from app.core.config import CostModel, InstrumentSpec, OrbConfig, RiskConfig, config_hash
from app.core.models import ONE_MINUTE, Bar, DataSource, Session
from app.execution.paper import ClosedTrade, PaperBroker
from app.reporting.metrics import json_safe
from app.research.features import daily_rth
from app.services.backtest import SessionRunner, iter_bars

STALE_AFTER_SECONDS = 150


class LiveMonitor:
    def __init__(self, gateway: Mt5Gateway, symbol: str, spec: InstrumentSpec, tz: ServerTz,
                 orb_cfg: OrbConfig, risk_cfg: RiskConfig, costs: CostModel, starting_balance: float,
                 data_source: DataSource, journal_dir: Path, clock: Callable[[], datetime],
                 poll_seconds: float = 5.0, db=None, news=None) -> None:
        self.gw = gateway
        self.db = db
        self.news = news
        self.symbol = symbol
        self.spec = spec
        self.tz = tz
        self.orb_cfg, self.risk_cfg, self.costs = orb_cfg, risk_cfg, costs
        self.source = data_source
        self.clock = clock
        self.poll = poll_seconds
        self.calendar = MarketCalendar()
        self.broker = PaperBroker(spec, costs, starting_balance, data_source)
        self.chash = config_hash(orb_cfg, risk_cfg, costs, spec)
        self.journal_dir = Path(journal_dir)
        self.events: deque[dict] = deque(maxlen=300)
        self.trades: list[ClosedTrade] = []
        self.session: Session | None = None
        self.runner: SessionRunner | None = None
        self.last_bar_time: pd.Timestamp | None = None
        self.status = "STARTING"
        self.message = ""
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- journal
    def _log(self, entry: dict) -> None:
        self.events.append(entry)
        day = str(entry.get("session_day") or "unknown")
        path = self.journal_dir / day / "journal.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")

    def _trade(self, t: ClosedTrade) -> None:
        self.trades.append(t)
        if self.db is not None:
            feats = next((e.get("features") for e in reversed(self.events)
                          if e.get("stage") == "RISK" and e.get("candidate_id") == t.candidate_id), None)
            self.db.add_trades("paper", None, self.symbol, [t], {t.candidate_id: feats or {}})

    # -------------------------------------------------------------------- loop
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="live-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        with self._lock:
            if self.runner is not None and not self.runner.finished:
                self.runner.finish()
            self.status = "STOPPED"

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
                self.last_error = None
            except GatewayError as e:
                self.status, self.last_error = "CONNECTION_ERROR", str(e)
            except Exception as e:  # noqa: BLE001 — keep the monitor alive, surface the error
                self.status, self.last_error = "ERROR", f"{type(e).__name__}: {e}"
            self._stop.wait(self.poll)

    def step(self) -> None:
        with self._lock:
            now = pd.Timestamp(self.clock())
            session = self.calendar.session(now.tz_convert(NEW_YORK).date())
            if self.session is not None and (session is None or session.day != self.session.day):
                if self.runner is not None and not self.runner.finished:
                    self.runner.finish()
                self.session, self.runner, self.last_bar_time = None, None, None
            if session is None:
                self.status, self.message = "MARKET_CLOSED", "No regular US session today"
                return
            if self.session is None:
                self.session = session
                self.runner = SessionRunner(session, self.broker, self.spec, self.orb_cfg, self.risk_cfg,
                                            self.costs, self.chash, self.source, log=self._log,
                                            on_trade=self._trade, now_fn=lambda _b: pd.Timestamp(self.clock()),
                                            prior_daily=self._prior_daily(session), news_fn=self._news_block)
            if now < session.open_utc:
                self.status, self.message = "WAITING_OPEN", f"Session opens {session.open_utc.isoformat()}"
                return
            if self.runner.finished:
                self.status, self.message = "SESSION_CLOSED", "Session finished"
                return
            for bar in self._new_bars(session, now):
                self.runner.on_bar(bar)
                self.last_bar_time = bar.time
            if now >= session.close_utc:
                self.runner.finish()
                self.status, self.message = "SESSION_CLOSED", "Session finished"
                return
            age = self.data_age_seconds(now)
            if age is not None and age > STALE_AFTER_SECONDS:
                self.status, self.message = "STALE_DATA", f"No new completed bar for {int(age)}s"
            else:
                self.status, self.message = "RUNNING", ""

    def _news_block(self, at: pd.Timestamp) -> str | None:
        if self.news is None:
            return None
        from app.market.news import news_block
        return news_block(self.news.events(), at, self.risk_cfg)

    def _prior_daily(self, session: Session) -> pd.DataFrame:
        """Previous ~40 sessions, aggregated per regular session — day context for the features."""
        start = session.day - pd.Timedelta(days=62)
        prior = [s for s in self.calendar.sessions_between(start, session.day) if s.day < session.day][-40:]
        if not prior:
            return pd.DataFrame(columns=["day", "open", "high", "low", "close"])
        raw = self.gw.rates_m1_server(self.symbol, prior[0].open_utc, prior[-1].close_utc)
        return daily_rth(to_utc_bars(raw, self.tz), prior)

    def _new_bars(self, session: Session, now: pd.Timestamp) -> list[Bar]:
        start = session.open_utc if self.last_bar_time is None else self.last_bar_time + ONE_MINUTE
        raw = self.gw.rates_m1_server(self.symbol, start, min(now, session.close_utc))
        bars = to_utc_bars(raw, self.tz)
        if bars.empty:
            return []
        t = bars["time"]
        keep = (t >= start) & (t < session.close_utc) & (t + ONE_MINUTE <= now)
        return list(iter_bars(bars[keep].sort_values("time")))

    def data_age_seconds(self, now: pd.Timestamp) -> float | None:
        if self.last_bar_time is None:
            return None
        return (now - (self.last_bar_time + ONE_MINUTE)).total_seconds()

    # ------------------------------------------------------------------ status
    def snapshot(self) -> dict:
        with self._lock:
            now = pd.Timestamp(self.clock())
            strat = self.runner.strategy if self.runner else None
            positions = [{"direction": p.direction.name, "entry_price": p.entry_price, "stop": p.stop,
                          "target": p.target, "volume": p.volume, "entry_time": str(p.entry_time),
                          "exit_deadline": str(p.exit_deadline)} for p in self.broker.positions]
            return json_safe({
                "status": self.status, "message": self.message, "error": self.last_error,
                "simulated": True, "data_source": self.source.value, "symbol": self.symbol,
                "server_tz": self.tz.name, "config_hash": self.chash, "now_utc": str(now),
                "session": None if self.session is None else {
                    "day": str(self.session.day), "open_utc": str(self.session.open_utc),
                    "close_utc": str(self.session.close_utc), "early_close": self.session.early_close},
                "phase": strat.phase.value if strat else None,
                "or_high": strat.or_high if strat else None, "or_low": strat.or_low if strat else None,
                "direction": strat.direction.name if strat and strat.direction else None,
                "last_bar_time": str(self.last_bar_time) if self.last_bar_time is not None else None,
                "data_age_seconds": self.data_age_seconds(now),
                "paper_balance": self.broker.balance, "open_positions": positions,
                "events": list(self.events)[-50:],
                "trades": [{"candidate_id": t.candidate_id, "direction": t.direction, "entry_time": str(t.entry_time),
                            "exit_time": str(t.exit_time), "exit_reason": t.exit_reason,
                            "net_pnl": t.net_pnl, "r_multiple": t.r_multiple} for t in self.trades],
            })
