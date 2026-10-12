from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from app.core.config import CostModel, InstrumentSpec, RiskConfig
from app.core.models import AccountMode
from app.risk.engine import AccountState, MarketSnapshot, evaluate, floor_to_step
from tests.test_execution_paper import T5, cand

ACCOUNT = AccountState(AccountMode.SIMULATION, equity=10_000, day_start_equity=10_000,
                       realized_pnl_today=0.0, trades_today=0, open_positions=0)
FRESH = MarketSnapshot(now=T5, last_bar_close=T5, spread=0.5)


def ev(spec, risk_cfg, costs, c=None, account=ACCOUNT, market=FRESH):
    return evaluate(c or cand(), account, market, spec, risk_cfg, costs)


def acct(**kw):
    return AccountState(**{**ACCOUNT.__dict__, **kw})


def test_normal_sizing_rounds_down(spec, risk_cfg, costs):
    d = ev(spec, risk_cfg, costs)
    # risk 50; loss per lot = (2.5 stop distance + 2 x 0.1 slippage) * 1.0 = 2.7 -> 18.52 -> 18.5
    assert d.approved and d.reasons == ()
    assert d.volume == pytest.approx(18.5)
    assert d.loss_at_stop_money == pytest.approx(18.5 * 2.7)
    assert d.loss_at_stop_money <= d.risk_money


def test_floor_to_step_never_rounds_up():
    assert floor_to_step(19.29, 0.1) == pytest.approx(19.2)
    assert floor_to_step(0.3, 0.1) == pytest.approx(0.3)  # binary-float trap: 0.3/0.1 = 2.9999...
    assert floor_to_step(0.09, 0.1) == 0.0


def test_commission_reduces_size(spec, risk_cfg):
    d = ev(spec, risk_cfg, CostModel(slippage=0.1, commission_per_lot_per_side=0.5))
    assert d.volume == pytest.approx(13.5)  # 50 / (2.7 + 1.0) = 13.51


def test_volume_caps(spec, risk_cfg, costs):
    d = ev(spec, risk_cfg.model_copy(update={"max_volume": 1.0}), costs)
    assert d.volume == pytest.approx(1.0)


def test_invalid_stop(spec, risk_cfg, costs):
    d = ev(spec, risk_cfg, costs, c=cand(stop=103.0))
    assert not d.approved and "INVALID_STOP" in d.reasons and d.volume == 0


def test_missing_spec_rejects_instead_of_guessing(risk_cfg, costs):
    d = ev(InstrumentSpec(symbol="NAS100"), risk_cfg, costs)
    assert not d.approved and "SPEC_MISSING" in d.reasons
    assert "tick_value" in d.details["missing_spec_fields"]


def test_min_lot_exceeds_risk(spec, risk_cfg, costs):
    big_min = spec.model_copy(update={"volume_min": 25.0})
    d = ev(big_min, risk_cfg, costs)
    assert not d.approved and "MIN_LOT_EXCEEDS_RISK" in d.reasons
    assert d.details["min_lot_loss"] == pytest.approx(25 * 2.7)


def test_daily_loss_lockout(spec, risk_cfg, costs):
    d = ev(spec, risk_cfg, costs, account=acct(realized_pnl_today=-150.0))
    assert "DAILY_LOSS_LIMIT" in d.reasons


def test_daily_budget_too_small_for_this_trade(spec, risk_cfg, costs):
    d = ev(spec, risk_cfg, costs, account=acct(realized_pnl_today=-120.0))
    assert d.reasons == ("DAILY_LOSS_BUDGET_INSUFFICIENT",)


def test_trade_and_position_limits(spec, risk_cfg, costs):
    assert "TRADE_LIMIT" in ev(spec, risk_cfg, costs, account=acct(trades_today=2)).reasons
    assert "POSITION_LIMIT" in ev(spec, risk_cfg, costs, account=acct(open_positions=1)).reasons


def test_stale_and_missing_data(spec, risk_cfg, costs):
    stale = MarketSnapshot(now=T5 + pd.Timedelta(seconds=91), last_bar_close=T5, spread=0.5)
    assert "STALE_DATA" in ev(spec, risk_cfg, costs, market=stale).reasons
    future = MarketSnapshot(now=T5 - pd.Timedelta(seconds=5), last_bar_close=T5, spread=0.5)
    assert "STALE_DATA" in ev(spec, risk_cfg, costs, market=future).reasons
    none = MarketSnapshot(now=T5, last_bar_close=None, spread=0.5)
    assert "NO_MARKET_DATA" in ev(spec, risk_cfg, costs, market=none).reasons


def test_spread_limits(spec, risk_cfg, costs):
    wide = MarketSnapshot(now=T5, last_bar_close=T5, spread=2.5)
    assert ev(spec, risk_cfg, costs, market=wide).reasons == ("SPREAD_TOO_WIDE",)
    unset = risk_cfg.model_copy(update={"max_spread": None})
    assert "SPREAD_LIMIT_NOT_CONFIGURED" in ev(spec, unset, costs).reasons


def test_kill_switch(spec, risk_cfg, costs):
    off = risk_cfg.model_copy(update={"trading_enabled": False})
    assert "TRADING_DISABLED" in ev(spec, off, costs).reasons


def test_real_mode_locked_and_demo_not_allowed(spec, risk_cfg, costs):
    assert "REAL_TRADING_LOCKED" in ev(spec, risk_cfg, costs, account=acct(mode=AccountMode.REAL)).reasons
    assert "MODE_NOT_ALLOWED" in ev(spec, risk_cfg, costs, account=acct(mode=AccountMode.DEMO)).reasons
    # Even a config that lists REAL as allowed cannot open it.
    permissive = RiskConfig(max_spread=2.0, allowed_modes=(AccountMode.SIMULATION, AccountMode.REAL))
    assert "REAL_TRADING_LOCKED" in ev(spec, permissive, costs, account=acct(mode=AccountMode.REAL)).reasons


def test_config_bounds_reject_dangerous_values():
    with pytest.raises(ValidationError):
        RiskConfig(risk_per_trade_pct=5.0)
    with pytest.raises(ValidationError):
        RiskConfig(max_daily_loss_pct=0)
    with pytest.raises(ValidationError):
        RiskConfig(unknown_key=1)
