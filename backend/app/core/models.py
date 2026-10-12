"""Domain types shared by strategy, risk, execution and reporting.

Price convention: MT5 candles for CFD instruments are built on the BID.
Every bar here therefore carries bid OHLC plus the bar's spread in MT5
points. Ask = bid + spread. (To be re-verified against the broker in A2.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

import pandas as pd

ONE_MINUTE = pd.Timedelta(minutes=1)


class Direction(int, Enum):
    LONG = 1
    SHORT = -1


class DataSource(str, Enum):
    MOCK = "MOCK"
    TEST = "TEST"
    HISTORICAL = "HISTORICAL"
    DEMO = "DEMO"
    LIVE = "LIVE"


class AccountMode(str, Enum):
    SIMULATION = "SIMULATION"
    DEMO = "DEMO"
    REAL = "REAL"


@dataclass(frozen=True)
class Bar:
    """One M1 candle. `time` is the bar OPEN time in UTC."""

    time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    spread_points: int = 0

    @property
    def close_time(self) -> pd.Timestamp:
        return self.time + ONE_MINUTE


@dataclass(frozen=True)
class Session:
    """A regular US equity session, both bounds in UTC."""

    day: date
    open_utc: pd.Timestamp
    close_utc: pd.Timestamp
    early_close: bool = False


@dataclass(frozen=True)
class Candidate:
    """A trade candidate produced by the deterministic strategy.

    stop/target are levels on the side the position EXITS on:
    long exits on bid, short exits on ask. entry_ref is the price
    the position would ENTER on at signal time (long: ask, short: bid).
    """

    id: str
    session_day: date
    direction: Direction
    signal_time: pd.Timestamp
    entry_ref: float
    stop: float
    target: float
    or_high: float
    or_low: float
    breakout_time: pd.Timestamp
    retest_time: pd.Timestamp
    spread_at_signal: float
    rationale: str
    strategy_version: str
    config_hash: str
    data_source: DataSource

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_ref - self.stop)


@dataclass(frozen=True)
class StrategyEvent:
    """SIGNAL carries a candidate; REJECT carries the reason a day produced none."""

    kind: str  # "SIGNAL" | "REJECT"
    session_day: date
    time: pd.Timestamp | None
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    candidate: Candidate | None = None
