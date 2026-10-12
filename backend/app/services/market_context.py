"""Compact, factual market snapshot of one symbol — the only market data the chat model sees.

Everything is computed here from the broker's own bars, so the model analyses numbers instead of
inventing them. Times are UTC (plus Baghdad/New York labels for the session).
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from app.brokers.mt5_gateway import Mt5Gateway, to_utc_bars
from app.brokers.server_time import ServerTz
from app.core.clock import BAGHDAD, NEW_YORK, MarketCalendar


def _atr(df: pd.DataFrame, n: int = 14) -> float | None:
    if len(df) < n + 1:
        return None
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def _resample(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = bars.set_index("time").resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last()}).dropna()
    return out.reset_index()


def _candles(df: pd.DataFrame, digits: int, n: int) -> list[list]:
    tail = df.tail(n)
    return [[t.strftime("%m-%d %H:%M"), round(o, digits), round(h, digits), round(l, digits), round(c, digits)]
            for t, o, h, l, c in zip(tail["time"], tail["open"], tail["high"], tail["low"], tail["close"])]


def snapshot(gw: Mt5Gateway, symbol: str, tz: ServerTz | None, now_utc: datetime, source: str) -> dict:
    spec, raw_spec = gw.instrument_spec(symbol)
    digits = spec.digits if spec.digits is not None else 2
    now = pd.Timestamp(now_utc)
    time_note = None
    if tz is None:
        measured = gw.measured_offset_hours(symbol, now_utc)
        if measured is not None:
            tz = ServerTz("fixed", measured)
            time_note = f"server clock measured live at UTC{measured:+g}; rule not yet confirmed on history"
        else:
            tz = ServerTz("fixed", 0.0)
            time_note = "server time NOT determined: candle times may be shifted by a few hours"
    raw = gw.rates_m1_server(symbol, now - pd.Timedelta(days=6), now)
    bars = to_utc_bars(raw, tz)
    bars = bars[bars["time"] <= now].reset_index(drop=True)
    out: dict = {"symbol": symbol, "description": raw_spec.get("description"), "data_source": source,
                 "digits": digits, "time_note": time_note}
    if bars.empty:
        out["error"] = "no bars returned by the terminal for the last 6 days"
        return out

    q = gw.quote(symbol)
    last = float(bars["close"].iloc[-1])
    last_t = bars["time"].iloc[-1]
    ny = bars["time"].dt.tz_convert(NEW_YORK)
    today = ny.iloc[-1].date()
    t_mask = ny.dt.date == today
    prev_days = sorted({d for d in ny.dt.date.unique() if d < today})
    prev = bars[ny.dt.date == prev_days[-1]] if prev_days else bars.iloc[0:0]
    day = bars[t_mask]
    m5, h1 = _resample(bars, "5min"), _resample(bars, "1h")
    sma20 = float(h1["close"].tail(20).mean()) if len(h1) >= 20 else None
    sma50 = float(h1["close"].tail(50).mean()) if len(h1) >= 50 else None
    trend = None
    if sma20 is not None and sma50 is not None:
        trend = "up" if last > sma20 > sma50 else "down" if last < sma20 < sma50 else "mixed"
    atr_m5, atr_h1 = _atr(m5), _atr(h1)
    spread = q["spread"] if q else None

    cal = MarketCalendar()
    sess = cal.session(today)
    session = None
    if sess is not None:
        session = {"ny_open_utc": str(sess.open_utc), "ny_close_utc": str(sess.close_utc),
                   "open_baghdad": sess.open_utc.tz_convert(BAGHDAD).strftime("%H:%M"),
                   "minutes_to_open": round((sess.open_utc - now).total_seconds() / 60),
                   "minutes_to_close": round((sess.close_utc - now).total_seconds() / 60),
                   "early_close": sess.early_close}

    r = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), digits)  # noqa: E731
    out.update({
        "now_utc": str(now.floor("s")), "last_bar_utc": str(last_t),
        "data_age_seconds": round((now - (last_t + pd.Timedelta(minutes=1))).total_seconds()),
        "last": r(last), "bid": r(q["bid"]) if q else None, "ask": r(q["ask"]) if q else None,
        "spread": r(spread), "spread_vs_atr_m5": round(spread / atr_m5, 3) if spread and atr_m5 else None,
        "today_ny": {"date": str(today), "open": r(day["open"].iloc[0]) if len(day) else None,
                     "high": r(day["high"].max()) if len(day) else None, "low": r(day["low"].min()) if len(day) else None,
                     "change_pct": round((last / float(day["open"].iloc[0]) - 1) * 100, 3) if len(day) else None},
        "previous_day": {"high": r(prev["high"].max()) if len(prev) else None, "low": r(prev["low"].min()) if len(prev) else None,
                         "close": r(prev["close"].iloc[-1]) if len(prev) else None},
        "five_day": {"high": r(bars["high"].max()), "low": r(bars["low"].min())},
        "atr14_m5": r(atr_m5), "atr14_h1": r(atr_h1), "sma20_h1": r(sma20), "sma50_h1": r(sma50), "h1_trend": trend,
        "us_session": session,
        "m5_last_36": _candles(m5, digits, 36), "h1_last_24": _candles(h1, digits, 24),
        "candle_format": "[MM-DD HH:MM UTC, open, high, low, close]",
    })
    return out
