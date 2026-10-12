"""Per-session runner shared by the backtest and the live monitor, plus the backtest loop.

Per bar t, the order is fixed and mirrors live operation:
  1. fill the order approved at the close of bar t-1 (at bar t's open),
  2. let the broker process exits on bar t,
  3. feed bar t to the strategy; a SIGNAL is risk-checked immediately
     (decision at bar t's close) and, if approved, filled on bar t+1.
Nothing ever reads a bar before it has closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from app.core.config import CostModel, InstrumentSpec, OrbConfig, RiskConfig, config_hash
from app.core.models import AccountMode, Bar, DataSource, Session
from app.execution.costs import spread_price
from app.execution.paper import ClosedTrade, OrderRejected, PaperBroker
from app.risk.engine import AccountState, MarketSnapshot, evaluate
from app.strategy.orb import OrbRetestDay

BAR_COLUMNS = ("time", "open", "high", "low", "close", "spread")

LogFn = Callable[[dict], None]


class SessionRunner:
    """Runs one session: strategy -> risk -> paper broker, journaling every decision."""

    def __init__(self, session: Session, broker: PaperBroker, spec: InstrumentSpec, orb_cfg: OrbConfig,
                 risk_cfg: RiskConfig, costs: CostModel, chash: str, data_source: DataSource,
                 log: LogFn, on_trade: Callable[[ClosedTrade], None],
                 now_fn: Callable[[Bar], pd.Timestamp] | None = None) -> None:
        self.s = session
        self.broker = broker
        self.spec = spec
        self.orb_cfg = orb_cfg
        self.risk_cfg = risk_cfg
        self.costs = costs
        self.chash = chash
        self.source = data_source
        self._log = log
        self._on_trade = on_trade
        # Backtest: "now" is the bar close. Live: the real clock (so stale data is caught).
        self._now = now_fn or (lambda b: b.close_time)
        self.strategy = OrbRetestDay(orb_cfg, session, lambda b: spread_price(b, spec.point, costs),
                                     chash, data_source)
        self.day_start_equity = broker.balance
        self.realized_today = 0.0
        self.trades_today = 0
        self.pending = None
        self.last_bar: Bar | None = None
        self.session_exit = session.close_utc - pd.Timedelta(minutes=orb_cfg.flat_before_close_minutes)
        self.finished = False

    def log(self, time, stage: str, status: str, reasons, **extra) -> None:
        self._log({"session_day": self.s.day, "time": time, "stage": stage, "status": status,
                   "reasons": list(reasons), "config_hash": self.chash, "data_source": self.source.value,
                   "simulated": True, **extra})

    def _closed(self, t: ClosedTrade) -> None:
        self.realized_today += t.net_pnl
        self._on_trade(t)
        self.log(t.exit_time, "EXECUTION", "CLOSED", [t.exit_reason], candidate_id=t.candidate_id,
                 net_pnl=t.net_pnl, r_multiple=t.r_multiple)

    def on_bar(self, bar: Bar) -> None:
        self.last_bar = bar
        if self.pending is not None:
            cand, decision = self.pending
            self.pending = None
            time_exit = cand.signal_time + pd.Timedelta(minutes=self.orb_cfg.max_hold_minutes)
            deadline, why = ((time_exit, "TIME_EXIT") if time_exit <= self.session_exit
                             else (self.session_exit, "SESSION_END"))
            try:
                pos = self.broker.open(cand, decision, bar, deadline, why)
                self.log(bar.time, "EXECUTION", "FILLED", [], candidate_id=cand.id,
                         entry_price=pos.entry_price, volume=pos.volume)
            except OrderRejected as e:
                self.log(bar.time, "EXECUTION", "FILL_REJECTED", [e.reason], candidate_id=cand.id, **e.details)

        for t in self.broker.on_bar(bar):
            self._closed(t)

        for ev in self.strategy.on_bar(bar):
            if ev.kind != "SIGNAL":
                self.log(ev.time, "STRATEGY", "NO_SIGNAL", [ev.reason], **ev.details)
                continue
            cand = ev.candidate
            account = AccountState(AccountMode.SIMULATION, self.broker.balance, self.day_start_equity,
                                   self.realized_today, self.trades_today, len(self.broker.positions))
            market = MarketSnapshot(now=self._now(bar), last_bar_close=bar.close_time,
                                    spread=cand.spread_at_signal)
            decision = evaluate(cand, account, market, self.spec, self.risk_cfg, self.costs)
            self.log(ev.time, "RISK", "APPROVED" if decision.approved else "REJECTED", decision.reasons,
                     candidate_id=cand.id, direction=cand.direction.name, entry_ref=cand.entry_ref,
                     stop=cand.stop, target=cand.target, volume=decision.volume, rationale=cand.rationale,
                     or_high=cand.or_high, or_low=cand.or_low)
            if decision.approved:
                self.pending = (cand, decision)
                self.trades_today += 1

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        if self.pending is not None:
            self.log(None, "EXECUTION", "FILL_REJECTED", ["ENTRY_BAR_MISSING"], candidate_id=self.pending[0].id)
            self.pending = None
        for ev in self.strategy.finish():
            self.log(ev.time, "STRATEGY", "NO_SIGNAL", [ev.reason], **ev.details)
        if self.broker.positions and self.last_bar is not None:
            for t in self.broker.close_all(self.last_bar, "DATA_END"):
                self._closed(t)


@dataclass
class BacktestResult:
    config_hash: str
    data_source: DataSource
    sessions: list[date]
    starting_balance: float
    ending_balance: float
    trades: list[ClosedTrade] = field(default_factory=list)
    journal: list[dict] = field(default_factory=list)


def _bars_for_session(df: pd.DataFrame, times: np.ndarray, s: Session) -> pd.DataFrame:
    lo = np.searchsorted(times, s.open_utc.to_datetime64(), side="left")
    hi = np.searchsorted(times, s.close_utc.to_datetime64(), side="left")
    return df.iloc[lo:hi]


def iter_bars(day: pd.DataFrame):
    for t, o, h, l, c, sp in zip(day["time"], day["open"], day["high"], day["low"],
                                 day["close"], day["spread"]):
        yield Bar(time=pd.Timestamp(t), open=float(o), high=float(h), low=float(l),
                  close=float(c), spread_points=int(sp))


def run_backtest(
    bars: pd.DataFrame,
    sessions: list[Session],
    spec: InstrumentSpec,
    orb_cfg: OrbConfig,
    risk_cfg: RiskConfig,
    costs: CostModel,
    starting_balance: float,
    data_source: DataSource,
) -> BacktestResult:
    missing = [c for c in BAR_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars missing columns {missing}")
    if spec.missing_fields():
        raise ValueError(f"instrument spec incomplete: {spec.missing_fields()} — refusing to backtest")
    df = bars.sort_values("time").reset_index(drop=True)
    if df["time"].duplicated().any():
        raise ValueError("duplicate bar timestamps — clean the data first")
    times = df["time"].to_numpy(dtype="datetime64[ns]")

    chash = config_hash(orb_cfg, risk_cfg, costs, spec)
    broker = PaperBroker(spec, costs, starting_balance, data_source)
    result = BacktestResult(chash, data_source, [s.day for s in sessions], starting_balance, starting_balance)

    for s in sessions:
        runner = SessionRunner(s, broker, spec, orb_cfg, risk_cfg, costs, chash, data_source,
                               log=result.journal.append, on_trade=result.trades.append)
        for bar in iter_bars(_bars_for_session(df, times, s)):
            runner.on_bar(bar)
        runner.finish()

    result.ending_balance = broker.balance
    return result
