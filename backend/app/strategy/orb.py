"""Opening Range Breakout + Retest — deterministic, incremental, broker-free.

The strategy is a per-session state machine fed one COMPLETED M1 bar at a
time. It can only ever see bars that have already closed, so look-ahead is
impossible by construction (and tested by prefix invariance).

Rules are specified in docs/TRADING_RULES.md; reason codes below match it.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable

import pandas as pd

from app.core.config import OrbConfig, StopMethod
from app.core.models import (ONE_MINUTE, Bar, Candidate, DataSource, Direction,
                             Session, StrategyEvent)


class Phase(Enum):
    WAIT_RANGE = "WAIT_RANGE"
    WAIT_BREAKOUT = "WAIT_BREAKOUT"
    WAIT_RETEST = "WAIT_RETEST"
    DONE = "DONE"


class OrbRetestDay:
    def __init__(
        self,
        cfg: OrbConfig,
        session: Session,
        spread_price: Callable[[Bar], float],
        config_hash: str,
        data_source: DataSource,
    ) -> None:
        self.cfg = cfg
        self.session = session
        self._spread_price = spread_price
        self._hash = config_hash
        self._source = data_source

        o = session.open_utc
        self.or_end = o + pd.Timedelta(minutes=cfg.opening_range_minutes)
        self.breakout_deadline = o + pd.Timedelta(minutes=cfg.breakout_deadline_minutes)
        # A position needs at least one full bar before the forced session exit.
        session_exit = session.close_utc - pd.Timedelta(minutes=cfg.flat_before_close_minutes)
        self.last_entry_at = min(o + pd.Timedelta(minutes=cfg.last_entry_minutes),
                                 session_exit - ONE_MINUTE)
        self._tf = pd.Timedelta(minutes=cfg.confirm_tf_minutes)

        self.phase = Phase.WAIT_RANGE
        self._last_time: pd.Timestamp | None = None
        self._bars_seen = 0
        self._or_bars: list[Bar] = []
        self.or_high: float | None = None
        self.or_low: float | None = None
        self._bucket: list[Bar] = []
        self._bucket_start: pd.Timestamp | None = None
        self.incomplete_buckets = 0
        self.direction: Direction | None = None
        self.breakout_time: pd.Timestamp | None = None
        self._retest_deadline: pd.Timestamp | None = None
        self._swing: float | None = None
        self._touched = False
        self._retest_time: pd.Timestamp | None = None

    # ------------------------------------------------------------------ public
    def on_bar(self, bar: Bar) -> list[StrategyEvent]:
        if self.phase is Phase.DONE:
            return []
        if bar.time.second != 0 or bar.time.microsecond != 0 or bar.time.nanosecond != 0:
            raise ValueError(f"bar time {bar.time} is not aligned to the minute")
        if self._last_time is not None and bar.time <= self._last_time:
            raise ValueError(f"bars must be strictly increasing ({bar.time} after {self._last_time})")
        if bar.time < self.session.open_utc or bar.time >= self.session.close_utc:
            return []  # outside the regular session: ignored, never traded
        prev, self._last_time = self._last_time, bar.time
        self._bars_seen += 1

        if self.phase is Phase.WAIT_RANGE:
            if bar.time < self.or_end:
                self._or_bars.append(bar)
                if bar.time == self.or_end - ONE_MINUTE:
                    return self._finalize_range()
                return []
            # Past the range window without its last bar -> range is incomplete.
            return self._finalize_range()

        if prev is not None:
            missing = int((bar.time - prev) / ONE_MINUTE) - 1
            if missing > self.cfg.max_gap_minutes:
                return self._reject(bar.time, "DATA_GAP", missing_minutes=missing,
                                    gap_after=str(prev))

        if self.phase is Phase.WAIT_BREAKOUT:
            return self._on_breakout_bar(bar)
        return self._on_retest_bar(bar)

    def finish(self) -> list[StrategyEvent]:
        """Session (or data) ended. Emit why the day produced no signal."""
        if self.phase is Phase.DONE:
            return []
        if self.phase is Phase.WAIT_RANGE:
            reason = "NO_DATA" if self._bars_seen == 0 else "INCOMPLETE_OPENING_RANGE"
        elif self.phase is Phase.WAIT_BREAKOUT:
            reason = "NO_BREAKOUT"
        else:
            reason = "RETEST_EXPIRED" if self._touched else "NO_RETEST"
        return self._reject(self._last_time, reason, ended="SESSION_OR_DATA_END")

    # ----------------------------------------------------------------- phases
    def _finalize_range(self) -> list[StrategyEvent]:
        n = self.cfg.opening_range_minutes
        if len(self._or_bars) != n:
            return self._reject(self._last_time, "INCOMPLETE_OPENING_RANGE",
                                bars_in_range=len(self._or_bars), required=n)
        self.or_high = max(b.high for b in self._or_bars)
        self.or_low = min(b.low for b in self._or_bars)
        width = self.or_high - self.or_low
        if self.cfg.min_range is not None and width < self.cfg.min_range:
            return self._reject(self._last_time, "RANGE_TOO_NARROW", width=width)
        if self.cfg.max_range is not None and width > self.cfg.max_range:
            return self._reject(self._last_time, "RANGE_TOO_WIDE", width=width)
        self.phase = Phase.WAIT_BREAKOUT
        return []

    def _on_breakout_bar(self, bar: Bar) -> list[StrategyEvent]:
        idx = (bar.time - self.or_end) // self._tf
        start = self.or_end + idx * self._tf
        if start != self._bucket_start:
            if self._bucket:
                self.incomplete_buckets += 1  # previous confirmation candle never completed
            self._bucket = []
            self._bucket_start = start
        self._bucket.append(bar)

        if len(self._bucket) == self.cfg.confirm_tf_minutes:
            close_time = start + self._tf
            close = self._bucket[-1].close
            self._bucket = []
            if close_time <= self.breakout_deadline:
                buf = self.cfg.breakout_buffer
                if close > self.or_high + buf:
                    self._start_retest(Direction.LONG, close_time)
                    return []
                if close < self.or_low - buf:
                    self._start_retest(Direction.SHORT, close_time)
                    return []

        if bar.close_time >= self.breakout_deadline:
            return self._reject(bar.close_time, "NO_BREAKOUT")
        return []

    def _start_retest(self, direction: Direction, breakout_close: pd.Timestamp) -> None:
        self.direction = direction
        self.breakout_time = breakout_close
        self._retest_deadline = breakout_close + pd.Timedelta(minutes=self.cfg.retest_window_minutes)
        self.phase = Phase.WAIT_RETEST

    def _on_retest_bar(self, bar: Bar) -> list[StrategyEvent]:
        cfg = self.cfg
        long = self.direction is Direction.LONG
        level = self.or_high if long else self.or_low

        if long:
            self._swing = bar.low if self._swing is None else min(self._swing, bar.low)
            if bar.close < level - cfg.retest_max_penetration:
                return self._reject(bar.close_time, "FALSE_BREAKOUT", close=bar.close)
            if not self._touched and bar.low <= level + cfg.retest_tolerance:
                self._touched, self._retest_time = True, bar.time
            if self._touched and bar.close > level:
                return self._signal(bar)
        else:
            self._swing = bar.high if self._swing is None else max(self._swing, bar.high)
            if bar.close > level + cfg.retest_max_penetration:
                return self._reject(bar.close_time, "FALSE_BREAKOUT", close=bar.close)
            if not self._touched and bar.high >= level - cfg.retest_tolerance:
                self._touched, self._retest_time = True, bar.time
            if self._touched and bar.close < level:
                return self._signal(bar)

        if bar.close_time >= self._retest_deadline:
            return self._reject(bar.close_time, "RETEST_EXPIRED" if self._touched else "NO_RETEST")
        return []

    def _signal(self, bar: Bar) -> list[StrategyEvent]:
        cfg = self.cfg
        signal_time = bar.close_time
        if signal_time > self.last_entry_at:
            return self._reject(signal_time, "ENTRY_WINDOW_CLOSED")
        sp = self._spread_price(bar)
        if self.direction is Direction.LONG:
            entry_ref = bar.close + sp
            base = self._swing if cfg.stop_method is StopMethod.RETEST_SWING else self.or_low
            stop = base - cfg.stop_buffer
            dist = entry_ref - stop
            target = entry_ref + cfg.reward_risk * dist
        else:
            entry_ref = bar.close
            base = self._swing if cfg.stop_method is StopMethod.RETEST_SWING else self.or_high
            stop = base + sp + cfg.stop_buffer
            dist = stop - entry_ref
            target = entry_ref - cfg.reward_risk * dist
        if not (cfg.min_stop_distance <= dist <= cfg.max_stop_distance):
            return self._reject(signal_time, "STOP_DISTANCE_INVALID", stop_distance=dist)

        cand = Candidate(
            id=f"{self.session.day:%Y%m%d}-{cfg.name}-v{cfg.version}-{self.direction.name}-{self._hash[:8]}",
            session_day=self.session.day,
            direction=self.direction,
            signal_time=signal_time,
            entry_ref=entry_ref,
            stop=stop,
            target=target,
            or_high=self.or_high,
            or_low=self.or_low,
            breakout_time=self.breakout_time,
            retest_time=self._retest_time,
            spread_at_signal=sp,
            rationale=(f"{self.direction.name}: {cfg.confirm_tf_minutes}m close beyond OR "
                       f"[{self.or_low:.2f}, {self.or_high:.2f}] at {self.breakout_time:%H:%M}Z, "
                       f"retest at {self._retest_time:%H:%M}Z, confirmed at {signal_time:%H:%M}Z"),
            strategy_version=f"{cfg.name}@{cfg.version}",
            config_hash=self._hash,
            data_source=self._source,
        )
        self.phase = Phase.DONE
        return [StrategyEvent("SIGNAL", self.session.day, signal_time, "SIGNAL",
                              self._details(), candidate=cand)]

    # ---------------------------------------------------------------- helpers
    def _details(self, **extra) -> dict:
        d = {"or_high": self.or_high, "or_low": self.or_low,
             "incomplete_confirm_candles": self.incomplete_buckets}
        if self.direction is not None:
            d["direction"] = self.direction.name
        d.update(extra)
        return d

    def _reject(self, time: pd.Timestamp | None, reason: str, **extra) -> list[StrategyEvent]:
        self.phase = Phase.DONE
        return [StrategyEvent("REJECT", self.session.day, time, reason, self._details(**extra))]
