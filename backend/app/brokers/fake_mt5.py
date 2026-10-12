"""MOCK stand-in for the MetaTrader5 module (same call surface the gateway uses).

Synthetic data only — labelled MOCK everywhere it surfaces. Used by tests and by
the platform's "MOCK broker" connection so the whole stack can be exercised
without a terminal. Server clock = New York + 7h (GMT+2/+3), like many brokers,
so the server-time detection is exercised for real.

Optional replay clock: time runs from `replay_start_utc` at `speed`x, so the
live monitor can be watched end-to-end at any hour.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app.brokers.server_time import ServerTz
from app.core.clock import MarketCalendar

TIMEFRAME_M1 = 1
MOCK_SERVER_TZ = ServerTz("ny_shift", 7.0)
RATE_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                       ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])

SYMBOLS = {
    "US100.mock": dict(description="MOCK Nasdaq 100 (synthetic data)", path="Indices\\US100.mock",
                       point=0.01, digits=2, trade_tick_size=0.01, trade_tick_value=0.01,
                       trade_contract_size=1.0, volume_min=0.1, volume_max=100.0, volume_step=0.1,
                       currency_profit="USD", currency_base="USD", trade_mode=4, spread=120, spread_float=True),
    "EURUSD.mock": dict(description="MOCK Euro vs US Dollar", path="Forex\\EURUSD.mock", point=0.00001,
                        digits=5, trade_tick_size=0.00001, trade_tick_value=1.0, trade_contract_size=100000.0,
                        volume_min=0.01, volume_max=50.0, volume_step=0.01, currency_profit="USD",
                        currency_base="EUR", trade_mode=4, spread=8, spread_float=True),
}


class FakeMt5:
    TIMEFRAME_M1 = TIMEFRAME_M1

    def __init__(self, seed: int = 11, accounts: dict[int, tuple[str, str]] | None = None,
                 attach_logged_in: bool = True, replay_start_utc: datetime | None = None,
                 speed: float = 60.0, investor_mode: bool = True) -> None:
        self.seed = seed
        self.accounts = accounts or {1234567: ("mock-investor-pass", "NexTrade-MOCK")}
        self.attach_logged_in = attach_logged_in
        self.investor_mode = investor_mode
        self.replay_start = replay_start_utc
        self.speed = speed
        self._wall0 = _time.monotonic()
        self._cal = MarketCalendar()
        self._days: dict[date, np.ndarray] = {}
        self._selected: set[str] = set()
        self.connected = False
        self.login_id: int | None = None
        self.server: str | None = None
        self._err = (1, "Success")
        self.calls: list[str] = []

    # ------------------------------------------------------------------ clock
    def set_now(self, utc: datetime | None) -> None:
        """Tests: pin the clock (None = back to replay/real time)."""
        self._fixed_now = utc

    def now_utc(self) -> datetime:
        if getattr(self, "_fixed_now", None) is not None:
            return self._fixed_now
        if self.replay_start is None:
            return datetime.now(timezone.utc)
        return self.replay_start + timedelta(seconds=(_time.monotonic() - self._wall0) * self.speed)

    # --------------------------------------------------------------- session
    def initialize(self, path=None, login=None, password=None, server=None, timeout=None, portable=False):
        self.calls.append("initialize")
        if login is None:
            if not self.attach_logged_in:
                self._err = (-6, "Terminal: Authorization failed")
                return False
            self.login_id, self.server = next(iter(self.accounts.items()))[0], next(iter(self.accounts.values()))[1]
            self.connected = True
            return True
        return self.login(login, password=password, server=server, timeout=timeout)

    def login(self, login, password=None, server=None, timeout=None):
        self.calls.append("login")
        acct = self.accounts.get(int(login))
        if acct is None or acct[0] != password or acct[1] != server:
            self._err = (-6, "Terminal: Authorization failed")
            self.connected = False
            return False
        self.login_id, self.server, self.connected = int(login), server, True
        self._err = (1, "Success")
        return True

    def shutdown(self):
        self.calls.append("shutdown")
        self.connected = False
        return True

    def last_error(self):
        return self._err

    def account_info(self):
        self.calls.append("account_info")
        if not self.connected:
            return None
        return SimpleNamespace(login=self.login_id, trade_mode=0, leverage=100, trade_allowed=not self.investor_mode,
                               trade_expert=not self.investor_mode, name="MOCK trader", server=self.server,
                               currency="USD", company="MOCK BROKER (synthetic data)", balance=10000.0,
                               equity=10000.0, margin_free=10000.0)

    def terminal_info(self):
        self.calls.append("terminal_info")
        if not self.connected:
            return None
        return SimpleNamespace(company="MOCK BROKER (synthetic data)", name="MOCK terminal", build=0,
                               connected=True, trade_allowed=False, path="(mock)")

    # ---------------------------------------------------------------- symbols
    def _sym(self, name):
        meta = SYMBOLS.get(name)
        if meta is None:
            return None
        return SimpleNamespace(name=name, visible=name in self._selected, select=name in self._selected, **meta)

    def symbols_get(self, group=None):
        self.calls.append("symbols_get")
        if not self.connected:
            return None
        return tuple(self._sym(n) for n in SYMBOLS)

    def symbol_info(self, name):
        self.calls.append("symbol_info")
        return self._sym(name) if self.connected else None

    def symbol_select(self, name, enable=True):
        self.calls.append("symbol_select")
        if name not in SYMBOLS or not self.connected:
            return False
        (self._selected.add if enable else self._selected.discard)(name)
        return True

    def symbol_info_tick(self, name):
        self.calls.append("symbol_info_tick")
        if not self.connected or name != "US100.mock":
            return None
        now = pd.Timestamp(self.now_utc())
        epoch_now = MOCK_SERVER_TZ.utc_to_server_epoch(now)
        rates = self._range(epoch_now - 3 * 86400, epoch_now)
        if len(rates) == 0:
            return None
        last = rates[-1]
        bid = float(last["close"])
        sp = int(last["spread"]) * SYMBOLS[name]["point"]
        return SimpleNamespace(time=int(last["time"]) + 59, bid=bid, ask=bid + sp, last=0.0,
                               time_msc=(int(last["time"]) + 59) * 1000)

    # ------------------------------------------------------------------ rates
    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        self.calls.append("copy_rates_range")
        if not self.connected or symbol != "US100.mock" or timeframe != TIMEFRAME_M1:
            self._err = (-1, "symbol/timeframe not available")
            return None
        lo = int(pd.Timestamp(date_from).timestamp())
        hi = int(pd.Timestamp(date_to).timestamp())
        return self._range(lo, hi)

    def _range(self, lo_epoch: int, hi_epoch: int) -> np.ndarray:
        now_server = MOCK_SERVER_TZ.utc_to_server_epoch(pd.Timestamp(self.now_utc()))
        hi_epoch = min(hi_epoch, now_server - 60)  # only completed bars exist
        if hi_epoch < lo_epoch:
            return np.zeros(0, dtype=RATE_DTYPE)
        d0 = (pd.Timestamp(lo_epoch, unit="s") - pd.Timedelta(days=1)).date()
        d1 = (pd.Timestamp(hi_epoch, unit="s") + pd.Timedelta(days=1)).date()
        parts = []
        d = d0
        while d <= d1:
            arr = self._day(d)
            if len(arr):
                parts.append(arr[(arr["time"] >= lo_epoch) & (arr["time"] <= hi_epoch)])
            d += timedelta(days=1)
        return np.concatenate(parts) if parts else np.zeros(0, dtype=RATE_DTYPE)

    def _day(self, d: date) -> np.ndarray:
        """Bars for UTC calendar day d (22:00 prev day .. 21:00), server-time stamped."""
        if d in self._days:
            return self._days[d]
        if d.weekday() >= 5 or not (self._cal.first_day <= d <= self._cal.last_day):
            self._days[d] = np.zeros(0, dtype=RATE_DTYPE)
            return self._days[d]
        rng = np.random.default_rng(self.seed * 100_003 + d.toordinal())
        start = pd.Timestamp(d, tz="UTC") - pd.Timedelta(hours=2)
        times = pd.date_range(start, periods=23 * 60, freq="1min")
        sess = self._cal.session(d)
        in_rth = np.zeros(len(times), bool)
        open_spike = np.zeros(len(times), bool)
        if sess is not None:
            in_rth = (times >= sess.open_utc) & (times < sess.close_utc)
            open_spike = (times >= sess.open_utc) & (times < sess.open_utc + pd.Timedelta(minutes=5))
        vol = np.where(in_rth, 6.0, 1.5)
        price0 = 30000 + 600 * np.sin(d.toordinal() / 17.0) + rng.normal(0, 40)
        closes = price0 + np.cumsum(rng.normal(0, vol))
        opens = np.concatenate([[price0], closes[:-1]])
        wick = np.abs(rng.normal(0, vol * 0.5, size=(2, len(times))))
        highs = np.maximum(opens, closes) + wick[0]
        lows = np.minimum(opens, closes) - wick[1]
        spread = np.where(in_rth, rng.integers(90, 160, len(times)), rng.integers(250, 400, len(times)))
        spread = np.where(open_spike, rng.integers(200, 320, len(times)), spread)
        epochs = np.array([MOCK_SERVER_TZ.utc_to_server_epoch(t) for t in times], dtype=np.int64)
        arr = np.zeros(len(times), dtype=RATE_DTYPE)
        arr["time"], arr["open"], arr["high"], arr["low"], arr["close"] = epochs, opens, highs, lows, closes
        arr["tick_volume"] = np.where(in_rth, rng.integers(80, 200, len(times)), rng.integers(5, 40, len(times)))
        arr["spread"] = spread
        self._days[d] = arr
        return arr
