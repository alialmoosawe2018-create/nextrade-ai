"""Backtest runner: replays M1 bars through strategy -> risk -> paper broker.

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

import numpy as np
import pandas as pd

from app.core.config import CostModel, InstrumentSpec, OrbConfig, RiskConfig, config_hash
from app.core.models import AccountMode, Bar, DataSource, Session
from app.execution.costs import spread_price
from app.execution.paper import ClosedTrade, OrderRejected, PaperBroker
from app.risk.engine import AccountState, MarketSnapshot, evaluate
from app.strategy.orb import OrbRetestDay

BAR_COLUMNS = ("time", "open", "high", "low", "close", "spread")


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


def _iter_bars(day: pd.DataFrame):
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
    sp_fn = lambda b: spread_price(b, spec.point, costs)  # noqa: E731

    def log(s: Session, time, stage: str, status: str, reasons, **extra) -> None:
        result.journal.append({
            "session_day": s.day, "time": time, "stage": stage, "status": status,
            "reasons": list(reasons), "config_hash": chash, "data_source": data_source.value,
            "simulated": True, **extra,
        })

    for s in sessions:
        day = _bars_for_session(df, times, s)
        strat = OrbRetestDay(orb_cfg, s, sp_fn, chash, data_source)
        day_start_equity = broker.balance
        realized_today = 0.0
        trades_today = 0
        pending = None
        last_bar: Bar | None = None
        session_exit = s.close_utc - pd.Timedelta(minutes=orb_cfg.flat_before_close_minutes)

        for bar in _iter_bars(day):
            last_bar = bar
            if pending is not None:
                cand, decision = pending
                pending = None
                time_exit = cand.signal_time + pd.Timedelta(minutes=orb_cfg.max_hold_minutes)
                deadline, why = (time_exit, "TIME_EXIT") if time_exit <= session_exit else (session_exit, "SESSION_END")
                try:
                    pos = broker.open(cand, decision, bar, deadline, why)
                    log(s, bar.time, "EXECUTION", "FILLED", [], candidate_id=cand.id,
                        entry_price=pos.entry_price, volume=pos.volume)
                except OrderRejected as e:
                    log(s, bar.time, "EXECUTION", "FILL_REJECTED", [e.reason], candidate_id=cand.id, **e.details)

            for t in broker.on_bar(bar):
                realized_today += t.net_pnl
                result.trades.append(t)
                log(s, t.exit_time, "EXECUTION", "CLOSED", [t.exit_reason], candidate_id=t.candidate_id,
                    net_pnl=t.net_pnl, r_multiple=t.r_multiple)

            for ev in strat.on_bar(bar):
                if ev.kind != "SIGNAL":
                    log(s, ev.time, "STRATEGY", "NO_SIGNAL", [ev.reason], **ev.details)
                    continue
                cand = ev.candidate
                account = AccountState(AccountMode.SIMULATION, broker.balance, day_start_equity,
                                       realized_today, trades_today, len(broker.positions))
                market = MarketSnapshot(now=ev.time, last_bar_close=bar.close_time, spread=cand.spread_at_signal)
                decision = evaluate(cand, account, market, spec, risk_cfg, costs)
                log(s, ev.time, "RISK", "APPROVED" if decision.approved else "REJECTED", decision.reasons,
                    candidate_id=cand.id, direction=cand.direction.name, entry_ref=cand.entry_ref,
                    stop=cand.stop, target=cand.target, volume=decision.volume, rationale=cand.rationale,
                    or_high=cand.or_high, or_low=cand.or_low)
                if decision.approved:
                    pending = (cand, decision)
                    trades_today += 1

        if pending is not None:
            log(s, None, "EXECUTION", "FILL_REJECTED", ["ENTRY_BAR_MISSING"], candidate_id=pending[0].id)
        for ev in strat.finish():
            log(s, ev.time, "STRATEGY", "NO_SIGNAL", [ev.reason], **ev.details)
        if broker.positions and last_bar is not None:
            for t in broker.close_all(last_bar, "DATA_END"):
                result.trades.append(t)
                log(s, t.exit_time, "EXECUTION", "CLOSED", ["DATA_END"], candidate_id=t.candidate_id,
                    net_pnl=t.net_pnl, r_multiple=t.r_multiple)

    result.ending_balance = broker.balance
    return result
