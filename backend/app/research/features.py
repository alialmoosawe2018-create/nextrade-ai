"""Trade context features — computed ONLY from information available at the moment of the decision.

Two levels, identical in backtest and live:
- Day context: from PREVIOUS regular sessions (daily ATR, trend, previous range) plus the session's
  opening price (known at 09:30). Computed once per session.
- Signal features: at the signal bar's close (minutes after open, opening-range width, stop size,
  spread vs stop, trend alignment, breakout delay).

These are what the learning loop groups trades by. A feature that is not computable yet (e.g. fewer
than 15 prior sessions) is None, and a lesson filter never acts on a None value.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.core.clock import NEW_YORK
from app.core.models import Candidate, Direction, Session

DAILY_ATR_N = 14
MIN_PRIOR_SESSIONS = DAILY_ATR_N + 1

FEATURE_DOCS = {
    "gap_atr": "abs(session open - previous close) / daily ATR14",
    "prev_range_atr": "previous session range / daily ATR14",
    "atr_ratio": "daily ATR5 / daily ATR14 (volatility expanding > 1)",
    "trend_daily": "+1 if previous close > SMA10 of closes > SMA20, -1 if below both, 0 mixed",
    "weekday": "0=Monday … 4=Friday",
    "minutes_after_open": "signal time - session open, minutes",
    "or_width_atr": "opening-range width / daily ATR14",
    "stop_atr": "stop distance / daily ATR14",
    "spread_to_stop": "spread at signal / stop distance",
    "trend_align": "+1 trade with the daily trend, -1 against, 0 mixed/unknown",
    "breakout_delay_min": "minutes from opening-range end to the breakout candle close",
}


def daily_rth(bars_utc: pd.DataFrame, sessions: list[Session]) -> pd.DataFrame:
    """One row per session: regular-session open/high/low/close from M1 bars (UTC)."""
    if bars_utc.empty or not sessions:
        return pd.DataFrame(columns=["day", "open", "high", "low", "close"])
    t = bars_utc["time"].to_numpy(dtype="datetime64[ns]")
    rows = []
    for s in sessions:
        lo = np.searchsorted(t, s.open_utc.to_datetime64(), side="left")
        hi = np.searchsorted(t, s.close_utc.to_datetime64(), side="left")
        if hi <= lo:
            continue
        d = bars_utc.iloc[lo:hi]
        rows.append({"day": s.day, "open": float(d["open"].iloc[0]), "high": float(d["high"].max()),
                     "low": float(d["low"].min()), "close": float(d["close"].iloc[-1])})
    return pd.DataFrame(rows)


def _atr(daily: pd.DataFrame, n: int) -> float | None:
    if len(daily) < n + 1:
        return None
    prev_close = daily["close"].shift(1)
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - prev_close).abs(),
                    (daily["low"] - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def day_context(prior_daily: pd.DataFrame, session: Session, session_open_price: float | None) -> dict:
    """Context from sessions BEFORE `session` (prior_daily must not contain it) + today's open price."""
    prior = prior_daily[prior_daily["day"] < session.day].sort_values("day")
    ctx: dict = {k: None for k in ("gap_atr", "prev_range_atr", "atr_ratio", "trend_daily", "atr14")}
    ctx["weekday"] = session.day.weekday()
    if len(prior) < MIN_PRIOR_SESSIONS:
        return ctx
    atr14, atr5 = _atr(prior, DAILY_ATR_N), _atr(prior, 5)
    last = prior.iloc[-1]
    closes = prior["close"]
    sma10, sma20 = closes.tail(10).mean(), closes.tail(20).mean() if len(closes) >= 20 else None
    ctx["atr14"] = atr14
    if atr14 and atr14 > 0:
        ctx["prev_range_atr"] = (last["high"] - last["low"]) / atr14
        if session_open_price is not None:
            ctx["gap_atr"] = abs(session_open_price - last["close"]) / atr14
        if atr5 is not None:
            ctx["atr_ratio"] = atr5 / atr14
    if sma20 is not None:
        c = last["close"]
        ctx["trend_daily"] = 1 if c > sma10 > sma20 else -1 if c < sma10 < sma20 else 0
    return ctx


def context_table(daily: pd.DataFrame) -> dict:
    """Vectorized day context for every session in `daily`, using only rows BEFORE each day.

    Same definitions as day_context(); returns {day: partial ctx} where the gap is filled in later from
    the session's first bar (`with_gap`). Proven equal to day_context() in tests.
    """
    if daily.empty:
        return {}
    d = daily.sort_values("day").reset_index(drop=True)
    prev_close = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev_close).abs(), (d["low"] - prev_close).abs()],
                   axis=1).max(axis=1)
    atr14 = tr.rolling(DAILY_ATR_N).mean().shift(1)
    atr5 = tr.rolling(5).mean().shift(1)
    sma10 = d["close"].rolling(10).mean().shift(1)
    sma20 = d["close"].rolling(20).mean().shift(1)
    out = {}
    for i, row in d.iterrows():
        ctx = {"gap_atr": None, "prev_range_atr": None, "atr_ratio": None, "trend_daily": None, "atr14": None,
               "weekday": row["day"].weekday(), "_prev_close": None}
        if i >= MIN_PRIOR_SESSIONS:
            a14, a5 = float(atr14.iloc[i]), float(atr5.iloc[i])
            prev = d.iloc[i - 1]
            ctx["atr14"], ctx["_prev_close"] = a14, float(prev["close"])
            if a14 > 0:
                ctx["prev_range_atr"] = (prev["high"] - prev["low"]) / a14
                ctx["atr_ratio"] = a5 / a14
            if not math.isnan(sma20.iloc[i]):
                c = prev["close"]
                ctx["trend_daily"] = 1 if c > sma10.iloc[i] > sma20.iloc[i] else -1 if c < sma10.iloc[i] < sma20.iloc[i] else 0
        out[row["day"]] = ctx
    return out


def with_gap(base: dict, session_open_price: float) -> dict:
    ctx = {k: v for k, v in base.items() if k != "_prev_close"}
    pc, atr = base.get("_prev_close"), base.get("atr14")
    if pc is not None and atr:
        ctx["gap_atr"] = abs(session_open_price - pc) / atr
    return ctx


def signal_features(c: Candidate, ctx: dict, session: Session, or_end: pd.Timestamp) -> dict:
    atr = ctx.get("atr14")
    stop = c.risk_distance
    trend = ctx.get("trend_daily")
    d = 1 if c.direction is Direction.LONG else -1
    f = {
        "gap_atr": ctx.get("gap_atr"), "prev_range_atr": ctx.get("prev_range_atr"),
        "atr_ratio": ctx.get("atr_ratio"), "trend_daily": trend, "weekday": ctx.get("weekday"),
        "minutes_after_open": (c.signal_time - session.open_utc).total_seconds() / 60,
        "or_width_atr": (c.or_high - c.or_low) / atr if atr else None,
        "stop_atr": stop / atr if atr else None,
        "spread_to_stop": c.spread_at_signal / stop if stop > 0 else None,
        "trend_align": (trend * d) if trend is not None else None,
        "breakout_delay_min": (c.breakout_time - or_end).total_seconds() / 60,
    }
    return {k: (None if v is None or (isinstance(v, float) and not math.isfinite(v)) else
                (round(float(v), 4) if isinstance(v, float) else v)) for k, v in f.items()}


def ny_day(ts: pd.Timestamp):
    return ts.tz_convert(NEW_YORK).date()
