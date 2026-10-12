"""Broker server time -> UTC.

MT5 bar/tick `time` values are epoch seconds whose wall clock is (for most
brokers) the trade SERVER's local time, not UTC — and documentation and forum
reports disagree, so nothing is assumed:

1. `measure_offset` compares the newest tick time with the real UTC clock
   while the market is active.
2. `score_candidates` checks, on downloaded history, which timezone rule puts
   the 09:30 New York volatility jump exactly at the NYSE open on most days,
   with the decisive days being those where the candidates disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.core.models import Session

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ServerTz:
    """A timezone rule for the broker server clock.

    kind "fixed": constant offset in hours. kind "zone": an IANA zone.
    kind "ny_shift": New York wall clock + `hours` (GMT+2/+3 switching with US DST,
    the common "New York close" server convention).
    """

    kind: str
    hours: float = 0.0
    zone: str | None = None

    @property
    def name(self) -> str:
        if self.kind == "fixed":
            return f"UTC{self.hours:+g}"
        if self.kind == "zone":
            return self.zone or "?"
        return f"NY{self.hours:+g}"

    def offset_at(self, utc: datetime) -> timedelta:
        if self.kind == "fixed":
            return timedelta(hours=self.hours)
        if self.kind == "zone":
            return utc.astimezone(ZoneInfo(self.zone)).utcoffset()
        return utc.astimezone(NEW_YORK).utcoffset() + timedelta(hours=self.hours)

    def to_utc(self, server_epoch: pd.Series) -> pd.Series:
        """Server-wall-clock epoch seconds -> tz-aware UTC timestamps."""
        naive = pd.to_datetime(server_epoch, unit="s")
        if self.kind == "fixed":
            return (naive - pd.Timedelta(hours=self.hours)).dt.tz_localize("UTC")
        if self.kind == "zone":
            return naive.dt.tz_localize(self.zone, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
        shifted = naive - pd.Timedelta(hours=self.hours)  # back to NY wall clock
        return shifted.dt.tz_localize(NEW_YORK, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")

    def utc_to_server_epoch(self, ts: pd.Timestamp) -> int:
        off = self.offset_at(ts.to_pydatetime())
        return int((ts + off).timestamp())


CANDIDATES: tuple[ServerTz, ...] = (
    ServerTz("fixed", 0.0),
    ServerTz("fixed", 2.0),
    ServerTz("fixed", 3.0),
    ServerTz("zone", zone="Europe/Nicosia"),   # EET/EEST, EU DST dates
    ServerTz("ny_shift", 7.0),                 # GMT+2 winter / GMT+3 summer on US DST dates
)


def measure_offset(newest_tick_epoch: int, utc_now: datetime, max_age_s: int = 120) -> float | None:
    """Server offset in hours (rounded to 0.25h) from a FRESH tick; None if the market looks idle."""
    raw = newest_tick_epoch - utc_now.timestamp()
    hours = round(raw / 900) * 0.25
    age = raw - hours * 3600  # residual = how old the tick is
    if abs(hours) > 14 or age > 1 or age < -max_age_s:
        return None
    return hours


def candidates_matching(offset_hours: float | None, utc_now: datetime) -> list[ServerTz]:
    if offset_hours is None:
        return list(CANDIDATES)
    return [c for c in CANDIDATES if abs(c.offset_at(utc_now).total_seconds() / 3600 - offset_hours) < 1e-9]


def _jump_ratio(rng: np.ndarray, i: int, after: int = 5, before: int = 15) -> float:
    if i - before < 0 or i + after > len(rng):
        return float("nan")
    b = rng[i - before:i]
    a = rng[i:i + after]
    if np.isnan(b).any() or np.isnan(a).any() or b.mean() <= 0:
        return float("nan")
    return float(a.mean() / b.mean())


def score_candidates(bars_server: pd.DataFrame, sessions: list[Session],
                     candidates: list[ServerTz] | tuple[ServerTz, ...] = CANDIDATES) -> list[dict]:
    """Per candidate: on how many days does its predicted 09:30 NY open show a bigger volatility
    jump than the same point shifted 1 and 2 hours either side?

    `bars_server` has `time` = raw server epoch seconds, plus high/low.
    Returns per-candidate {name, score, days_tested, wins: {day: bool}, center: {day: epoch}}.
    """
    t = bars_server["time"].to_numpy(dtype=np.int64)
    rng_all = (bars_server["high"] - bars_server["low"]).to_numpy(dtype=float)
    index = {int(v): i for i, v in enumerate(t)}
    span = 140
    shifts = (-120, -60, 0, 60, 120)

    def ranges_around(center_epoch: int) -> np.ndarray:
        out = np.full(2 * span + 1, np.nan)
        for k in range(-span, span + 1):
            j = index.get(center_epoch + 60 * k)
            if j is not None:
                out[k + span] = rng_all[j]
        return out

    results = []
    for c in candidates:
        wins: dict = {}
        centers: dict = {}
        for s in sessions:
            center = c.utc_to_server_epoch(s.open_utc)
            centers[s.day] = center
            rng = ranges_around(center)
            ratios = [_jump_ratio(rng, span + sh) for sh in shifts]
            if any(np.isnan(r) for r in ratios):
                continue
            wins[s.day] = int(np.argmax(ratios)) == 2
        tested = len(wins)
        results.append({"candidate": c, "name": c.name, "days_tested": tested,
                        "score": (sum(wins.values()) / tested) if tested else 0.0,
                        "wins": wins, "center": centers})
    return sorted(results, key=lambda r: -r["score"])


def choose(scores: list[dict], min_score: float = 0.6, min_margin: float = 0.1,
           min_decisive_days: int = 3) -> tuple[ServerTz | None, str]:
    """Pick the best rule only if it is clearly ahead; otherwise refuse to guess.

    Rules that agree on every tested day are equivalent for this data and do not block the
    choice. Close rivals that disagree on some days are decided ONLY on those days.
    """
    if not scores or scores[0]["days_tested"] < 10:
        return None, "INSUFFICIENT DATA: fewer than 10 sessions with complete bars around the open"
    best = scores[0]
    if best["score"] < min_score:
        return None, f"UNCERTAIN: best rule {best['name']} matches the open on only {best['score']:.0%} of days"
    notes = []
    for rival in scores[1:]:
        if rival["score"] <= best["score"] - min_margin:
            continue
        decisive = [d for d in best["wins"] if d in rival["wins"] and best["center"][d] != rival["center"][d]]
        if not decisive:
            notes.append(f"{rival['name']} is identical on this data")
            continue
        b = sum(best["wins"][d] for d in decisive)
        r = sum(rival["wins"][d] for d in decisive)
        if len(decisive) < min_decisive_days or b <= r or b < 2 * len(decisive) / 3:
            return None, (f"UNCERTAIN: {best['name']} vs {rival['name']} — on the {len(decisive)} days where they "
                          f"differ the open matched {b} vs {r} times")
        notes.append(f"beats {rival['name']} on {b}/{len(decisive)} decisive days")
    msg = f"{best['name']} matches the open on {best['score']:.0%} of {best['days_tested']} days"
    return best["candidate"], msg + (" (" + "; ".join(notes) + ")" if notes else "")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
