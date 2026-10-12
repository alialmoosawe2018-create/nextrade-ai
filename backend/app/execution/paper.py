"""Paper (simulated) broker. SIMULATION mode only — it cannot be pointed at a real account.

Fill model (documented in docs/TRADING_RULES.md, section "Execution"):
- Market entry at the OPEN of the bar after the signal: long pays ask (+slippage),
  short receives bid (-slippage).
- Long exits on bid, short exits on ask (= bid + bar spread).
- Stop = market order: filled at stop -/+ slippage, or at the bar open if the
  bar gapped through it. Target = limit order: filled at the target, never better.
- Stop and target both inside one bar with unknown order -> STOP (conservative),
  flagged `ambiguous_bar`.
- Time / session exit at the open of the first bar at or after the deadline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.core.config import CostModel, InstrumentSpec
from app.core.models import AccountMode, Bar, Candidate, DataSource, Direction
from app.core.safety import assert_mode_allowed
from app.execution.costs import money_per_price_unit, spread_price
from app.risk.engine import RiskDecision


class OrderRejected(Exception):
    def __init__(self, reason: str, **details) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


@dataclass
class Position:
    candidate: Candidate
    direction: Direction
    volume: float
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    exit_deadline: pd.Timestamp
    exit_deadline_reason: str
    planned_risk_money: float
    spread_at_entry: float
    best: float = field(default=float("nan"))   # best exit-side price seen
    worst: float = field(default=float("nan"))  # worst exit-side price seen


@dataclass(frozen=True)
class ClosedTrade:
    candidate_id: str
    session_day: object
    direction: str
    strategy_version: str
    config_hash: str
    data_source: str
    volume: float
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    ambiguous_bar: bool
    spread_at_entry: float
    gross_pnl: float
    commission: float
    net_pnl: float
    planned_risk_money: float
    r_multiple: float
    mfe: float  # price units, exit-side
    mae: float
    simulated: bool = True


def compute_fill(candidate: Candidate, bar: Bar, spec: InstrumentSpec, costs: CostModel) -> tuple[float, float]:
    """Entry price and spread for a market entry at `bar.open`. Raises OrderRejected."""
    if bar.time != candidate.signal_time:
        raise OrderRejected("ENTRY_BAR_MISSING", expected=str(candidate.signal_time), got=str(bar.time))
    sp = spread_price(bar, spec.point, costs)
    if candidate.direction is Direction.LONG:
        fill = bar.open + sp + costs.slippage
        exit_side_open = bar.open
        through = exit_side_open <= candidate.stop or exit_side_open >= candidate.target
    else:
        fill = bar.open - costs.slippage
        exit_side_open = bar.open + sp
        through = exit_side_open >= candidate.stop or exit_side_open <= candidate.target
    if through:
        raise OrderRejected("ENTRY_INVALID_AT_FILL", open=bar.open)
    if costs.max_entry_deviation is not None and abs(fill - candidate.entry_ref) > costs.max_entry_deviation:
        raise OrderRejected("ENTRY_SLIPPED_TOO_FAR", fill=fill, entry_ref=candidate.entry_ref)
    return fill, sp


def evaluate_exit(pos: Position, bar: Bar, point: float, costs: CostModel) -> tuple[float, str, bool] | None:
    """(exit_price, reason, ambiguous) if the position closes on this bar, else None.

    Also updates the position's best/worst exit-side prices (MFE/MAE).
    """
    sp = spread_price(bar, point, costs)
    slip = costs.slippage
    long = pos.direction is Direction.LONG
    # Exit-side OHLC: bid for longs, ask for shorts.
    shift = 0.0 if long else sp
    o, h, l = bar.open + shift, bar.high + shift, bar.low + shift

    def seen(*prices: float) -> None:
        for p in prices:
            if long:
                pos.best = p if pos.best != pos.best else max(pos.best, p)
                pos.worst = p if pos.worst != pos.worst else min(pos.worst, p)
            else:
                pos.best = p if pos.best != pos.best else min(pos.best, p)
                pos.worst = p if pos.worst != pos.worst else max(pos.worst, p)

    if bar.time >= pos.exit_deadline:
        seen(o)
        return (o - slip if long else o + slip), pos.exit_deadline_reason, False

    if long:
        if o <= pos.stop:
            seen(o)
            return o - slip, "STOP", False
        if o >= pos.target:
            seen(o)
            return pos.target, "TARGET", False
        hit_stop, hit_target = l <= pos.stop, h >= pos.target
        seen(h, l)
        if hit_stop:
            return pos.stop - slip, "STOP", hit_target
        if hit_target:
            return pos.target, "TARGET", False
    else:
        if o >= pos.stop:
            seen(o)
            return o + slip, "STOP", False
        if o <= pos.target:
            seen(o)
            return pos.target, "TARGET", False
        hit_stop, hit_target = h >= pos.stop, l <= pos.target
        seen(h, l)
        if hit_stop:
            return pos.stop + slip, "STOP", hit_target
        if hit_target:
            return pos.target, "TARGET", False
    return None


class PaperBroker:
    """Simulated account. Balance is SIMULATED and labelled so everywhere."""

    def __init__(self, spec: InstrumentSpec, costs: CostModel, starting_balance: float,
                 data_source: DataSource, mode: AccountMode = AccountMode.SIMULATION) -> None:
        assert_mode_allowed(mode)
        if mode is not AccountMode.SIMULATION:
            raise ValueError("PaperBroker only runs in SIMULATION mode")
        if starting_balance <= 0:
            raise ValueError("starting_balance must be > 0")
        missing = spec.missing_fields()
        if missing:
            raise ValueError(f"instrument spec incomplete: {missing}")
        self.spec = spec
        self.costs = costs
        self.mode = mode
        self.data_source = data_source
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.positions: list[Position] = []
        self.closed: list[ClosedTrade] = []
        self.kill_switch = False

    def open(self, candidate: Candidate, decision: RiskDecision, bar: Bar,
             exit_deadline: pd.Timestamp, exit_deadline_reason: str) -> Position:
        if self.kill_switch:
            raise OrderRejected("KILL_SWITCH")
        if not decision.approved or decision.volume <= 0:
            raise OrderRejected("RISK_REJECTED", reasons=list(decision.reasons))
        fill, sp = compute_fill(candidate, bar, self.spec, self.costs)
        pos = Position(
            candidate=candidate, direction=candidate.direction, volume=decision.volume,
            entry_time=bar.time, entry_price=fill, stop=candidate.stop, target=candidate.target,
            exit_deadline=exit_deadline, exit_deadline_reason=exit_deadline_reason,
            planned_risk_money=decision.loss_at_stop_money, spread_at_entry=sp,
        )
        self.positions.append(pos)
        return pos

    def on_bar(self, bar: Bar) -> list[ClosedTrade]:
        out: list[ClosedTrade] = []
        for pos in list(self.positions):
            if bar.time < pos.entry_time:
                continue
            hit = evaluate_exit(pos, bar, self.spec.point, self.costs)
            if hit is not None:
                out.append(self._close(pos, bar.time, *hit))
        return out

    def close_all(self, bar: Bar, reason: str) -> list[ClosedTrade]:
        """Force-close at this bar's close (e.g. data ended)."""
        out = []
        sp = spread_price(bar, self.spec.point, self.costs)
        for pos in list(self.positions):
            long = pos.direction is Direction.LONG
            price = bar.close - self.costs.slippage if long else bar.close + sp + self.costs.slippage
            out.append(self._close(pos, bar.close_time, price, reason, False))
        return out

    def _close(self, pos: Position, time: pd.Timestamp, price: float, reason: str, ambiguous: bool) -> ClosedTrade:
        self.positions.remove(pos)
        d = int(pos.direction.value)
        gross = (price - pos.entry_price) * d * money_per_price_unit(self.spec, pos.volume)
        commission = 2 * self.costs.commission_per_lot_per_side * pos.volume
        net = gross - commission
        self.balance += net
        if pos.direction is Direction.LONG:
            mfe, mae = pos.best - pos.entry_price, pos.entry_price - pos.worst
        else:
            mfe, mae = pos.entry_price - pos.best, pos.worst - pos.entry_price
        trade = ClosedTrade(
            candidate_id=pos.candidate.id, session_day=pos.candidate.session_day,
            direction=pos.direction.name, strategy_version=pos.candidate.strategy_version,
            config_hash=pos.candidate.config_hash, data_source=self.data_source.value,
            volume=pos.volume, entry_time=pos.entry_time, entry_price=pos.entry_price,
            stop=pos.stop, target=pos.target, exit_time=time, exit_price=price,
            exit_reason=reason, ambiguous_bar=ambiguous, spread_at_entry=pos.spread_at_entry,
            gross_pnl=gross, commission=commission, net_pnl=net,
            planned_risk_money=pos.planned_risk_money,
            r_multiple=net / pos.planned_risk_money if pos.planned_risk_money > 0 else float("nan"),
            mfe=mfe, mae=mae,
        )
        self.closed.append(trade)
        return trade
