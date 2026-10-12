from __future__ import annotations

import pandas as pd
import pytest

from app.core.config import CostModel
from app.core.models import AccountMode, Candidate, DataSource, Direction
from app.core.safety import RealTradingLocked
from app.execution.paper import OrderRejected, PaperBroker
from app.risk.engine import RiskDecision
from tests.conftest import OPEN, bar

T5 = OPEN + pd.Timedelta(minutes=5)
APPROVED = RiskDecision(approved=True, reasons=(), volume=2.0, risk_money=50.0, loss_at_stop_money=5.0)
DEADLINE = OPEN + pd.Timedelta(minutes=15)


def cand(direction=Direction.LONG, entry_ref=102.0, stop=99.5, target=107.0) -> Candidate:
    return Candidate(id="c1", session_day=OPEN.date(), direction=direction, signal_time=T5,
                     entry_ref=entry_ref, stop=stop, target=target, or_high=100, or_low=95,
                     breakout_time=T5, retest_time=T5, spread_at_signal=0.5, rationale="t",
                     strategy_version="orb_retest@1", config_hash="h", data_source=DataSource.TEST)


def short_cand() -> Candidate:
    return cand(Direction.SHORT, entry_ref=94.0, stop=96.0, target=90.0)


@pytest.fixture
def broker(spec, costs):
    return PaperBroker(spec, costs, 10_000.0, DataSource.TEST)


def open_long(broker, o=101.6, spread_points=50):
    return broker.open(cand(), APPROVED, bar(5, o, o + 0.2, o - 0.2, o, spread_points), DEADLINE, "TIME_EXIT")


# ---------------------------------------------------------------- fills
def test_long_fills_at_ask_plus_slippage(broker):
    pos = open_long(broker)
    assert pos.entry_price == pytest.approx(101.6 + 0.5 + 0.1)
    assert pos.spread_at_entry == pytest.approx(0.5)


def test_short_fills_at_bid_minus_slippage(broker):
    pos = broker.open(short_cand(), APPROVED, bar(5, 94.2, 94.4, 94.0, 94.2), DEADLINE, "TIME_EXIT")
    assert pos.entry_price == pytest.approx(94.2 - 0.1)


def test_spread_multiplier_and_floor(spec):
    b = PaperBroker(spec, CostModel(spread_multiplier=2.0, min_spread=0.0), 10_000, DataSource.TEST)
    assert b.open(cand(), APPROVED, bar(5, 101.6, 101.8, 101.4, 101.6, 50), DEADLINE, "T").entry_price == pytest.approx(102.6)
    b2 = PaperBroker(spec, CostModel(min_spread=0.8), 10_000, DataSource.TEST)
    assert b2.open(cand(), APPROVED, bar(5, 101.6, 101.8, 101.4, 101.6, 50), DEADLINE, "T").entry_price == pytest.approx(102.4)


# ---------------------------------------------------------------- exits
def test_stop_hit_long(broker):
    open_long(broker)
    [t] = broker.on_bar(bar(6, 101, 101.5, 99.0, 99.4))
    assert t.exit_reason == "STOP" and t.exit_price == pytest.approx(99.5 - 0.1)
    assert not t.ambiguous_bar


def test_target_hit_long_fills_exactly_at_target(broker):
    open_long(broker)
    [t] = broker.on_bar(bar(6, 103, 108, 102.5, 107.5))
    assert t.exit_reason == "TARGET" and t.exit_price == pytest.approx(107.0)


def test_both_touched_in_one_bar_assumes_stop(broker):
    open_long(broker)
    [t] = broker.on_bar(bar(6, 102, 108, 99, 104))
    assert t.exit_reason == "STOP" and t.ambiguous_bar


def test_gap_through_stop_fills_at_open(broker):
    open_long(broker)
    [t] = broker.on_bar(bar(6, 98.0, 98.5, 97.5, 98.2))
    assert t.exit_reason == "STOP" and t.exit_price == pytest.approx(98.0 - 0.1)


def test_gap_beyond_target_gets_no_bonus(broker):
    open_long(broker)
    [t] = broker.on_bar(bar(6, 110, 111, 109, 110))
    assert t.exit_reason == "TARGET" and t.exit_price == pytest.approx(107.0)


def test_stop_and_target_checked_on_entry_bar(broker, spec, costs):
    pos = broker.open(cand(), APPROVED, bar(5, 101.6, 101.8, 99.0, 100.0), DEADLINE, "TIME_EXIT")
    [t] = broker.on_bar(bar(5, 101.6, 101.8, 99.0, 100.0))
    assert t.exit_reason == "STOP"


def test_short_stop_triggers_on_ask(broker):
    broker.open(short_cand(), APPROVED, bar(5, 94.2, 94.4, 94.0, 94.2), DEADLINE, "TIME_EXIT")
    # bid high 95.6 < stop 96, but ask high = 95.6 + 0.5 = 96.1 >= 96 -> stopped.
    [t] = broker.on_bar(bar(6, 94.5, 95.6, 94.3, 95.0))
    assert t.exit_reason == "STOP" and t.exit_price == pytest.approx(96.0 + 0.1)


def test_short_target_needs_ask_to_reach_it(broker):
    broker.open(short_cand(), APPROVED, bar(5, 94.2, 94.4, 94.0, 94.2), DEADLINE, "TIME_EXIT")
    # bid low 89.8 <= 90 but ask low 90.3 > 90 -> not filled.
    assert broker.on_bar(bar(6, 92, 92.5, 89.8, 91)) == []
    [t] = broker.on_bar(bar(7, 91, 91.2, 89.4, 90.5))  # ask low 89.9 -> target
    assert t.exit_reason == "TARGET"


def test_time_exit_at_open_of_deadline_bar(broker):
    open_long(broker)
    assert broker.on_bar(bar(6, 102, 103, 101, 102.5)) == []
    [t] = broker.on_bar(bar(15, 103, 104, 102, 103))
    assert t.exit_reason == "TIME_EXIT" and t.exit_price == pytest.approx(103 - 0.1)


def test_session_end_exit_reason(broker):
    broker.open(cand(), APPROVED, bar(5, 101.6, 101.8, 101.4, 101.6), DEADLINE, "SESSION_END")
    [t] = broker.on_bar(bar(20, 103, 104, 102, 103))  # data gap past the deadline
    assert t.exit_reason == "SESSION_END"


# ---------------------------------------------------------------- money
def test_pnl_commission_and_r(spec):
    b = PaperBroker(spec, CostModel(slippage=0.0, commission_per_lot_per_side=1.5), 10_000, DataSource.TEST)
    b.open(cand(), APPROVED, bar(5, 101.5, 101.8, 101.4, 101.6), DEADLINE, "TIME_EXIT")  # fill 102.0
    [t] = b.on_bar(bar(6, 103, 108, 102.5, 107.5))  # target 107
    assert t.gross_pnl == pytest.approx((107 - 102) * 2.0 * 1.0)
    assert t.commission == pytest.approx(2 * 1.5 * 2.0)
    assert t.net_pnl == pytest.approx(10.0 - 6.0)
    assert t.r_multiple == pytest.approx(4.0 / 5.0)
    assert b.balance == pytest.approx(10_004.0)
    assert t.simulated is True and t.data_source == "TEST"


def test_mfe_mae(broker):
    open_long(broker)  # entry 102.2
    broker.on_bar(bar(6, 102, 104, 101, 103))
    [t] = broker.on_bar(bar(7, 103, 103.5, 99.0, 99.2))
    assert t.mfe == pytest.approx(104 - 102.2)
    assert t.mae == pytest.approx(102.2 - 99.0)


# ---------------------------------------------------------------- rejections and gates
def test_rejected_decision_never_opens(broker):
    no = RiskDecision(approved=False, reasons=("SPREAD_TOO_WIDE",))
    with pytest.raises(OrderRejected) as e:
        broker.open(cand(), no, bar(5, 101.6, 101.8, 101.4, 101.6), DEADLINE, "T")
    assert e.value.reason == "RISK_REJECTED" and broker.positions == []


def test_kill_switch_blocks_orders(broker):
    broker.kill_switch = True
    with pytest.raises(OrderRejected) as e:
        open_long(broker)
    assert e.value.reason == "KILL_SWITCH"


def test_entry_bar_must_follow_signal(broker):
    with pytest.raises(OrderRejected) as e:
        broker.open(cand(), APPROVED, bar(6, 101.6, 101.8, 101.4, 101.6), DEADLINE, "T")
    assert e.value.reason == "ENTRY_BAR_MISSING"


def test_entry_invalid_if_open_already_through_stop(broker):
    with pytest.raises(OrderRejected) as e:
        broker.open(cand(), APPROVED, bar(5, 99.0, 99.2, 98.8, 99.0), DEADLINE, "T")
    assert e.value.reason == "ENTRY_INVALID_AT_FILL"


def test_entry_slipped_too_far(spec):
    b = PaperBroker(spec, CostModel(max_entry_deviation=0.5), 10_000, DataSource.TEST)
    with pytest.raises(OrderRejected) as e:
        b.open(cand(), APPROVED, bar(5, 103.0, 103.2, 102.8, 103.0), DEADLINE, "T")
    assert e.value.reason == "ENTRY_SLIPPED_TOO_FAR"


def test_real_mode_is_locked(spec, costs):
    with pytest.raises(RealTradingLocked):
        PaperBroker(spec, costs, 10_000, DataSource.TEST, mode=AccountMode.REAL)


def test_incomplete_spec_refused(costs):
    from app.core.config import InstrumentSpec
    with pytest.raises(ValueError):
        PaperBroker(InstrumentSpec(symbol="X"), costs, 10_000, DataSource.TEST)


def test_cost_scaling_never_below_one():
    with pytest.raises(ValueError):
        CostModel(slippage=0.1).scaled(0.5)
    s = CostModel(slippage=0.1, min_spread=0.2, commission_per_lot_per_side=1).scaled(2)
    assert (s.slippage, s.min_spread, s.commission_per_lot_per_side, s.spread_multiplier) == (0.2, 0.4, 2, 2.0)
