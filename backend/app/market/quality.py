"""Data-quality checks for M1 bars. Reports problems; `clean` removes only what is provably bad."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.models import Session


@dataclass
class QualityReport:
    rows: int = 0
    duplicates: int = 0
    unsorted: bool = False
    misaligned: int = 0
    ohlc_invalid: int = 0
    nonpositive: int = 0
    negative_spread: int = 0
    first_bar: str | None = None
    last_bar: str | None = None
    sessions_expected: int = 0
    sessions_with_full_data: int = 0
    sessions_partial: int = 0
    sessions_empty: int = 0
    missing_minutes_total: int = 0
    worst_sessions: list[dict] = field(default_factory=list)
    extreme_moves: int = 0

    @property
    def verdict(self) -> str:
        if self.rows == 0:
            return "INSUFFICIENT DATA"
        # Structural errors win over everything: they must never be hidden behind another verdict.
        if self.duplicates or self.misaligned or self.ohlc_invalid or self.nonpositive or self.negative_spread:
            return "FAIL"
        if self.sessions_with_full_data + self.sessions_partial == 0:
            return "INSUFFICIENT DATA"
        if self.sessions_partial or self.sessions_empty or self.extreme_moves:
            return "WARN"
        return "OK"


def check(df: pd.DataFrame, sessions: list[Session], extreme_k: float = 15.0) -> QualityReport:
    r = QualityReport(rows=len(df), sessions_expected=len(sessions))
    if df.empty:
        r.sessions_empty = len(sessions)
        return r
    t = pd.to_datetime(df["time"], utc=True)
    r.first_bar, r.last_bar = str(t.min()), str(t.max())
    r.duplicates = int(t.duplicated().sum())
    r.unsorted = not t.is_monotonic_increasing
    r.misaligned = int((t != t.dt.floor("1min")).sum())
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    r.nonpositive = int(((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)).sum())
    r.ohlc_invalid = int(((h < np.maximum(o, c)) | (l > np.minimum(o, c)) | (h < l)).sum())
    r.negative_spread = int((df["spread"].to_numpy() < 0).sum())

    ts = np.sort(t.unique().to_numpy(dtype="datetime64[ns]"))
    per_session = []
    for s in sessions:
        expected = int((s.close_utc - s.open_utc) / pd.Timedelta(minutes=1))
        lo = np.searchsorted(ts, s.open_utc.to_datetime64(), side="left")
        hi = np.searchsorted(ts, s.close_utc.to_datetime64(), side="left")
        have = int(hi - lo)
        missing = expected - have
        if have == 0:
            r.sessions_empty += 1
        elif missing > 0:
            r.sessions_partial += 1
        else:
            r.sessions_with_full_data += 1
        r.missing_minutes_total += max(missing, 0)
        per_session.append({"day": str(s.day), "expected": expected, "have": have, "missing": missing})
    r.worst_sessions = sorted([p for p in per_session if p["missing"] > 0],
                              key=lambda p: -p["missing"])[:10]

    rets = np.abs(np.diff(c))
    if len(rets) > 10:
        med = float(np.median(rets[rets > 0])) if (rets > 0).any() else 0.0
        if med > 0:
            r.extreme_moves = int((rets > extreme_k * med).sum())
    return r


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Sort, drop exact-duplicate timestamps (keep first), drop rows with impossible OHLC."""
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.sort_values("time", kind="stable")
    n0 = len(out)
    out = out[~out["time"].duplicated(keep="first")]
    dup = n0 - len(out)
    o, h, l, c = (out[k] for k in ("open", "high", "low", "close"))
    bad = (o <= 0) | (h <= 0) | (l <= 0) | (c <= 0) | (h < l) | (h < o) | (h < c) | (l > o) | (l > c)
    bad |= out["time"] != out["time"].dt.floor("1min")
    out = out[~bad].reset_index(drop=True)
    return out, {"dropped_duplicates": int(dup), "dropped_invalid": int(bad.sum())}
