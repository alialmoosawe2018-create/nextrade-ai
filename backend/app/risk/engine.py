"""Independent risk engine. Strategy and AI output can never override it.

Every check runs and every failing reason is recorded (audit), but a single
reason is enough to reject. Position size is computed from the broker's
contract specification only; missing specification = rejection, never a guess.
Volume is always rounded DOWN to the volume step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

import pandas as pd

from app.core.config import CostModel, InstrumentSpec, RiskConfig
from app.core.models import AccountMode, Candidate, Direction
from app.core.safety import REAL_TRADING_ENABLED


@dataclass(frozen=True)
class AccountState:
    mode: AccountMode
    equity: float
    day_start_equity: float
    realized_pnl_today: float
    trades_today: int
    open_positions: int


@dataclass(frozen=True)
class MarketSnapshot:
    now: pd.Timestamp
    last_bar_close: pd.Timestamp | None
    spread: float | None  # price units


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    volume: float = 0.0
    risk_money: float = 0.0          # equity x risk %
    loss_at_stop_money: float = 0.0  # what `volume` loses at the stop incl. cost estimates
    details: dict = field(default_factory=dict)


def floor_to_step(value: float, step: float) -> float:
    v, s = Decimal(str(value)), Decimal(str(step))
    if s <= 0:
        raise ValueError("step must be > 0")
    return float((v / s).to_integral_value(rounding=ROUND_FLOOR) * s)


def evaluate(
    candidate: Candidate,
    account: AccountState,
    market: MarketSnapshot,
    spec: InstrumentSpec,
    cfg: RiskConfig,
    costs: CostModel,
) -> RiskDecision:
    reasons: list[str] = []
    details: dict = {}

    # --- permissions -------------------------------------------------------
    if not cfg.trading_enabled:
        reasons.append("TRADING_DISABLED")
    if account.mode == AccountMode.REAL and not REAL_TRADING_ENABLED:
        reasons.append("REAL_TRADING_LOCKED")
    elif account.mode not in cfg.allowed_modes:
        reasons.append("MODE_NOT_ALLOWED")

    # --- data --------------------------------------------------------------
    if market.last_bar_close is None:
        reasons.append("NO_MARKET_DATA")
    else:
        age = (market.now - market.last_bar_close).total_seconds()
        details["data_age_seconds"] = age
        if age > cfg.max_data_age_seconds or age < 0:
            reasons.append("STALE_DATA")
    if cfg.max_spread is None:
        reasons.append("SPREAD_LIMIT_NOT_CONFIGURED")
    elif market.spread is None:
        reasons.append("SPREAD_UNKNOWN")
    elif market.spread > cfg.max_spread:
        reasons.append("SPREAD_TOO_WIDE")

    # --- account limits ----------------------------------------------------
    daily_limit = account.day_start_equity * cfg.max_daily_loss_pct / 100.0
    if account.realized_pnl_today <= -daily_limit:
        reasons.append("DAILY_LOSS_LIMIT")
    if account.trades_today >= cfg.max_trades_per_day:
        reasons.append("TRADE_LIMIT")
    if account.open_positions >= cfg.max_concurrent_positions:
        reasons.append("POSITION_LIMIT")

    # --- stop geometry -----------------------------------------------------
    c = candidate
    if c.direction is Direction.LONG:
        geometry_ok = c.stop < c.entry_ref < c.target
    else:
        geometry_ok = c.target < c.entry_ref < c.stop
    if not geometry_ok or c.risk_distance <= 0:
        reasons.append("INVALID_STOP")

    # --- sizing ------------------------------------------------------------
    missing = spec.missing_fields()
    if missing:
        reasons.append("SPEC_MISSING")
        details["missing_spec_fields"] = missing
    volume = 0.0
    risk_money = account.equity * cfg.risk_per_trade_pct / 100.0
    loss_at_stop = 0.0
    if not missing and geometry_ok and account.equity > 0:
        per_unit = spec.tick_value / spec.tick_size  # money per 1.0 price move per lot
        # Loss per lot at the stop: distance + entry AND stop slippage, plus round-trip commission.
        loss_per_lot = (c.risk_distance + 2 * costs.slippage) * per_unit + 2 * costs.commission_per_lot_per_side
        details["loss_per_lot"] = loss_per_lot
        if loss_per_lot <= 0:
            reasons.append("SIZING_FAILED")
        else:
            raw = risk_money / loss_per_lot
            cap = spec.volume_max if cfg.max_volume is None else min(spec.volume_max, cfg.max_volume)
            volume = floor_to_step(min(raw, cap), spec.volume_step)
            details["raw_volume"] = raw
            if volume < spec.volume_min:
                reasons.append("MIN_LOT_EXCEEDS_RISK")
                details["min_lot_loss"] = spec.volume_min * loss_per_lot
                volume = 0.0
            else:
                loss_at_stop = volume * loss_per_lot
                remaining = daily_limit + min(account.realized_pnl_today, 0.0)
                if loss_at_stop > remaining:
                    reasons.append("DAILY_LOSS_BUDGET_INSUFFICIENT")
    elif account.equity <= 0:
        reasons.append("NO_EQUITY")

    approved = not reasons
    return RiskDecision(
        approved=approved,
        reasons=tuple(reasons),
        volume=volume if approved else 0.0,
        risk_money=risk_money,
        loss_at_stop_money=loss_at_stop if approved else 0.0,
        details=details,
    )
