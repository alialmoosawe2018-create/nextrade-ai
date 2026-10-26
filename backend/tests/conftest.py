from __future__ import annotations

import os

os.environ["NEXTRADE_NO_BACKGROUND"] = "1"  # never start the daily-brief thread (it could call the real Claude)

from datetime import date

import pandas as pd
import pytest

from app.core.clock import MarketCalendar
from app.core.config import CostModel, InstrumentSpec, OrbConfig, RiskConfig, SpecSource
from app.core.models import Bar, Session

# 2026-10-12 (Monday): regular session, open 13:30Z (EDT). Verified against XNYS.
DAY = date(2026, 10, 12)
OPEN = pd.Timestamp("2026-10-12 13:30", tz="UTC")


@pytest.fixture
def session() -> Session:
    s = MarketCalendar().session(DAY)
    assert s is not None and s.open_utc == OPEN
    return s


@pytest.fixture
def cfg() -> OrbConfig:
    return OrbConfig(
        opening_range_minutes=3, confirm_tf_minutes=1, breakout_buffer=0.0,
        breakout_deadline_minutes=30, retest_window_minutes=10, retest_tolerance=1.0,
        retest_max_penetration=5.0, stop_buffer=1.0, min_stop_distance=1.0,
        max_stop_distance=50.0, reward_risk=2.0, last_entry_minutes=60,
        max_hold_minutes=10, flat_before_close_minutes=5, max_gap_minutes=2,
    )


@pytest.fixture
def spec() -> InstrumentSpec:
    # TEST spec: 1 lot = 1.0 account currency per 1.0 price move.
    return InstrumentSpec(symbol="TEST100", source=SpecSource.TEST, point=0.01, digits=2,
                          tick_size=0.01, tick_value=0.01, contract_size=1.0,
                          volume_min=0.1, volume_max=50.0, volume_step=0.1,
                          currency_profit="USD", account_currency="USD")


@pytest.fixture
def costs() -> CostModel:
    return CostModel(slippage=0.1, commission_per_lot_per_side=0.0)


@pytest.fixture
def risk_cfg() -> RiskConfig:
    return RiskConfig(risk_per_trade_pct=0.5, max_daily_loss_pct=1.5, max_trades_per_day=2,
                      max_concurrent_positions=1, max_spread=2.0, max_data_age_seconds=90)


def bar(minute: int, o: float, h: float, l: float, c: float, spread_points: int = 50) -> Bar:
    """Bar opening `minute` minutes after the 13:30Z open."""
    return Bar(OPEN + pd.Timedelta(minutes=minute), o, h, l, c, spread_points)


def opening_range() -> list[Bar]:
    """OR high 100, low 95."""
    return [bar(0, 98, 100, 96, 98), bar(1, 98, 99, 95, 97), bar(2, 97, 99.5, 96, 99)]


def bars_df(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame({"time": [b.time for b in bars], "open": [b.open for b in bars],
                         "high": [b.high for b in bars], "low": [b.low for b in bars],
                         "close": [b.close for b in bars], "spread": [b.spread_points for b in bars]})
