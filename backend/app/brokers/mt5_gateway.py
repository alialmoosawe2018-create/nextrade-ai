"""READ-ONLY gateway to a MetaTrader 5 terminal (official package or the MOCK stand-in).

Hard rules enforced here:
- Only functions in READ_ONLY_CALLS can be invoked. There is no code path to
  order_send / order_check / positions or any trading call.
- The password is accepted as a SecretStr, handed to initialize() once and
  never stored, logged, or returned.
- Every call is serialized by a lock (the MetaTrader5 module is not thread-safe).
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from pydantic import SecretStr

from app.brokers.server_time import ServerTz, measure_offset
from app.core.config import InstrumentSpec, SpecSource

READ_ONLY_CALLS = frozenset({
    "initialize", "login", "shutdown", "last_error", "account_info", "terminal_info",
    "symbols_get", "symbol_info", "symbol_select", "symbol_info_tick", "copy_rates_range",
})
NASDAQ_PATTERN = re.compile(r"(US\s?-?100|USTEC|US\s?TECH|NAS(DAQ)?\s?-?100|NAS100|NDX|\bNQ\b|NASDAQ)", re.I)
TRADE_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}


class GatewayError(RuntimeError):
    pass


@dataclass
class ConnectionInfo:
    method: str                 # "credentials" | "attach" | "mock"
    source: str                 # "DEMO" | "LIVE" | "MOCK" (data label for everything downstream)
    login: int | None
    server: str | None
    company: str | None
    currency: str | None
    account_mode: str           # DEMO | CONTEST | REAL | UNKNOWN
    read_only: bool             # True when the account cannot trade (investor password)
    terminal_build: int | None
    connected_at: str
    balance: float | None = None
    equity: float | None = None
    warnings: list[str] = field(default_factory=list)


class Mt5Gateway:
    def __init__(self, module, label_mock: bool = False) -> None:
        self._m = module
        self._mock = label_mock
        self._lock = threading.RLock()
        self.info: ConnectionInfo | None = None

    # ------------------------------------------------------------- internals
    def _call(self, name: str, *args, **kwargs):
        if name not in READ_ONLY_CALLS:
            raise GatewayError(f"refused: {name} is not a read-only call")
        with self._lock:
            return getattr(self._m, name)(*args, **kwargs)

    def _error(self) -> str:
        try:
            code, msg = self._call("last_error")
            return f"{msg} (code {code})"
        except Exception:  # noqa: BLE001
            return "unknown MT5 error"

    @property
    def connected(self) -> bool:
        return self.info is not None

    # ------------------------------------------------------------- connect
    def connect(self, method: str, login: int | None = None, password: SecretStr | None = None,
                server: str | None = None, terminal_path: str | None = None, timeout_ms: int = 60_000) -> ConnectionInfo:
        if method not in ("credentials", "attach"):
            raise GatewayError("method must be 'credentials' or 'attach'")
        kwargs: dict = {}
        if terminal_path:
            kwargs["path"] = terminal_path
        if method == "credentials":
            if not (login and password and server):
                raise GatewayError("account number, password and server are all required")
            kwargs.update(login=int(login), password=password.get_secret_value(), server=server, timeout=timeout_ms)
        ok = self._call("initialize", **kwargs)
        kwargs.clear()  # drop our reference to the password; nothing else ever held it
        if not ok:
            err = self._error()
            self._call("shutdown")
            raise GatewayError(f"MT5 connection failed: {err}")
        acct = self._call("account_info")
        term = self._call("terminal_info")
        if acct is None:
            err = self._error()
            self._call("shutdown")
            raise GatewayError(f"connected to the terminal but no account is logged in: {err}")
        mode = TRADE_MODES.get(getattr(acct, "trade_mode", None), "UNKNOWN")
        read_only = not bool(getattr(acct, "trade_allowed", True))
        warnings = []
        if not read_only:
            warnings.append("This login CAN trade. Use the investor (read-only) password for analysis.")
        if mode == "REAL":
            warnings.append("REAL account. NexTrade never sends orders, but check your broker's terms "
                            "on third-party software before analysing a real account.")
        self.info = ConnectionInfo(
            method="mock" if self._mock else method,
            source="MOCK" if self._mock else ("DEMO" if mode == "DEMO" else "LIVE"),
            login=getattr(acct, "login", None), server=getattr(acct, "server", None),
            company=getattr(acct, "company", None), currency=getattr(acct, "currency", None),
            account_mode=mode, read_only=read_only, terminal_build=getattr(term, "build", None),
            connected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            balance=getattr(acct, "balance", None), equity=getattr(acct, "equity", None), warnings=warnings,
        )
        return self.info

    def disconnect(self) -> None:
        with self._lock:
            if self.info is not None:
                self._call("shutdown")
            self.info = None

    def refresh_account(self) -> ConnectionInfo | None:
        if self.info is None:
            return None
        acct = self._call("account_info")
        if acct is None:
            self.info = None
            raise GatewayError(f"connection lost: {self._error()}")
        self.info.balance, self.info.equity = getattr(acct, "balance", None), getattr(acct, "equity", None)
        return self.info

    def _require(self) -> None:
        if self.info is None:
            raise GatewayError("not connected")

    # ------------------------------------------------------------- symbols
    def find_symbols(self, query: str | None = None, nasdaq_only: bool = False) -> list[dict]:
        self._require()
        syms = self._call("symbols_get") or ()
        out = []
        for s in syms:
            text = f"{s.name} {getattr(s, 'description', '')}"
            is_nasdaq = bool(NASDAQ_PATTERN.search(text))
            if nasdaq_only and not is_nasdaq:
                continue
            if query and query.lower() not in text.lower():
                continue
            out.append({"name": s.name, "description": getattr(s, "description", ""),
                        "path": getattr(s, "path", ""), "nasdaq_candidate": is_nasdaq})
        return sorted(out, key=lambda r: (not r["nasdaq_candidate"], r["name"]))

    def instrument_spec(self, symbol: str) -> tuple[InstrumentSpec, dict]:
        """Contract spec straight from symbol_info. Zero/absent values stay None (never guessed)."""
        self._require()
        if not self._call("symbol_select", symbol, True):
            raise GatewayError(f"symbol {symbol!r} not available: {self._error()}")
        si = self._call("symbol_info", symbol)
        if si is None:
            raise GatewayError(f"symbol {symbol!r} not found: {self._error()}")

        def pos(name):
            v = getattr(si, name, None)
            return float(v) if v not in (None, 0, 0.0) else None

        spec = InstrumentSpec(
            symbol=symbol, source=SpecSource.MT5_SYMBOL_INFO, point=pos("point"),
            digits=int(si.digits) if getattr(si, "digits", None) is not None else None,
            tick_size=pos("trade_tick_size"), tick_value=pos("trade_tick_value"),
            contract_size=pos("trade_contract_size"), volume_min=pos("volume_min"),
            volume_max=pos("volume_max"), volume_step=pos("volume_step"),
            currency_profit=getattr(si, "currency_profit", None) or None,
            account_currency=self.info.currency,
        )
        raw = {k: getattr(si, k, None) for k in ("name", "description", "path", "point", "digits",
                                                 "trade_tick_size", "trade_tick_value", "trade_contract_size",
                                                 "volume_min", "volume_max", "volume_step", "currency_profit",
                                                 "spread", "spread_float", "trade_mode")}
        return spec, raw

    # --------------------------------------------------------------- prices
    def quote(self, symbol: str) -> dict | None:
        self._require()
        t = self._call("symbol_info_tick", symbol)
        if t is None:
            return None
        return {"server_epoch": int(t.time), "bid": float(t.bid), "ask": float(t.ask),
                "spread": float(t.ask) - float(t.bid)}

    def measured_offset_hours(self, symbol: str, now: datetime | None = None) -> float | None:
        q = self.quote(symbol)
        if q is None:
            return None
        return measure_offset(q["server_epoch"], now or datetime.now(timezone.utc))

    def rates_m1_server(self, symbol: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp,
                        chunk_days: int = 20) -> pd.DataFrame:
        """Raw M1 bars (time = server epoch) covering [start_utc, end_utc], padded by a day each side
        because the request bounds' timezone is not trustworthy; callers convert and filter."""
        self._require()
        lo = start_utc - pd.Timedelta(days=1)
        hi = end_utc + pd.Timedelta(days=1)
        frames = []
        cur = lo
        while cur < hi:
            nxt = min(cur + pd.Timedelta(days=chunk_days), hi)
            arr = self._call("copy_rates_range", symbol, self._m.TIMEFRAME_M1,
                             cur.to_pydatetime(), nxt.to_pydatetime())
            if arr is not None and len(arr):
                frames.append(pd.DataFrame(np.asarray(arr)))
            cur = nxt
        if not frames:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume", "spread"])
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
        return df[["time", "open", "high", "low", "close", "tick_volume", "spread"]]


def to_utc_bars(raw: pd.DataFrame, tz: ServerTz) -> pd.DataFrame:
    """Convert raw server-epoch bars into the engine's UTC bar format; ambiguous DST hours are dropped."""
    if raw.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "spread"])
    out = raw[["open", "high", "low", "close", "spread"]].copy()
    out.insert(0, "time", tz.to_utc(raw["time"]).values)
    out = out.dropna(subset=["time"])
    out["time"] = pd.to_datetime(out["time"], utc=True)
    return out.reset_index(drop=True)


def load_mt5_module():
    """The official package, or None when it is not installed (non-Windows / not set up)."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
        return mt5
    except Exception:  # noqa: BLE001
        return None
