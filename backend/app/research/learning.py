"""Evidence engine: turn losing trades into lessons ONLY when the numbers support it.

Protocol (fixed in code, not tuned per run):
1. Trades are ordered by time. The first 60% are the DISCOVERY set, the last 40% the VALIDATION set.
2. Candidate rules "skip when <feature> > / < / == value" are generated from the discovery set only
   (quartile thresholds for numbers, each value for categories).
3. A rule survives discovery if the trades it would skip number >= 20, average negative R, and the gap to
   the kept trades has t >= 3 (strict, because many rules are tried at once).
4. The SAME rule (same threshold) must then hold on the validation set: skipped trades still average
   negative R and worse than the kept ones, with >= 8 trades. Only then is it VALIDATED.
5. A validated lesson is only a proposal. The owner approves it; it becomes an entry filter and a new
   strategy version (new config hash). Nothing changes automatically.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

NUMERIC = ("gap_atr", "prev_range_atr", "atr_ratio", "minutes_after_open", "or_width_atr", "stop_atr",
           "spread_to_stop", "breakout_delay_min")
CATEGORICAL = ("trend_align", "trend_daily", "weekday")
MIN_TRADES = 60
MIN_EXCLUDED_DISCOVERY = 20
MIN_EXCLUDED_VALIDATION = 8
T_MIN = 3.0
DISCOVERY_SHARE = 0.6


def _stats(r: pd.Series) -> dict:
    n = int(len(r))
    if n == 0:
        return {"n": 0, "mean_r": None, "se": None}
    mean = float(r.mean())
    se = float(r.std(ddof=1) / math.sqrt(n)) if n > 1 else None
    return {"n": n, "mean_r": round(mean, 4), "se": round(se, 4) if se is not None else None}


def _mask(df: pd.DataFrame, feature: str, op: str, value: float) -> pd.Series:
    col = pd.to_numeric(df[feature], errors="coerce")
    valid = col.notna()
    m = col > value if op == "gt" else col < value if op == "lt" else col == value
    return m & valid


def evaluate(df: pd.DataFrame, feature: str, op: str, value: float) -> dict:
    m = _mask(df, feature, op, value)
    excl, kept = df.loc[m, "r_multiple"], df.loc[~m, "r_multiple"]
    se_e = excl.std(ddof=1) / math.sqrt(len(excl)) if len(excl) > 1 else float("nan")
    se_k = kept.std(ddof=1) / math.sqrt(len(kept)) if len(kept) > 1 else float("nan")
    denom = math.sqrt(se_e ** 2 + se_k ** 2) if not (math.isnan(se_e) or math.isnan(se_k)) else float("nan")
    diff = (kept.mean() - excl.mean()) if len(excl) and len(kept) else float("nan")
    t = diff / denom if denom and not math.isnan(denom) and denom > 0 else float("nan")
    return {"skipped": _stats(excl), "kept": _stats(kept),
            "t": None if math.isnan(t) else round(float(t), 2),
            "total_r_skipped": round(float(excl.sum()), 3)}


def candidate_rules(disc: pd.DataFrame) -> list[tuple[str, str, float]]:
    rules = []
    for f in NUMERIC:
        if f not in disc:
            continue
        col = pd.to_numeric(disc[f], errors="coerce").dropna()
        if len(col) < MIN_EXCLUDED_DISCOVERY * 2:
            continue
        rules.append((f, "gt", round(float(col.quantile(0.75)), 4)))
        rules.append((f, "lt", round(float(col.quantile(0.25)), 4)))
    for f in CATEGORICAL:
        if f not in disc:
            continue
        for v in sorted(pd.to_numeric(disc[f], errors="coerce").dropna().unique()):
            rules.append((f, "eq", float(v)))
    return rules


def lesson_id(symbol: str, feature: str, op: str, value: float) -> str:
    return "L" + hashlib.sha1(f"{symbol}|{feature}|{op}|{value}".encode()).hexdigest()[:7]


def discover(trades: pd.DataFrame, symbol: str) -> dict:
    """Run the fixed protocol. Returns {status, tested_rules, lessons[]} — never invents a lesson."""
    df = trades.dropna(subset=["r_multiple"]).sort_values("entry_time").reset_index(drop=True)
    if len(df) < MIN_TRADES:
        return {"status": "INSUFFICIENT DATA", "trades": int(len(df)),
                "note": f"Need at least {MIN_TRADES} trades with outcomes; have {len(df)}.", "lessons": []}
    cut = int(len(df) * DISCOVERY_SHARE)
    disc, val = df.iloc[:cut], df.iloc[cut:]
    rules = candidate_rules(disc)
    found = []
    for f, op, v in rules:
        d = evaluate(disc, f, op, v)
        if (d["skipped"]["n"] < MIN_EXCLUDED_DISCOVERY or d["skipped"]["mean_r"] is None
                or d["skipped"]["mean_r"] >= 0 or d["t"] is None or d["t"] < T_MIN):
            continue
        vv = evaluate(val, f, op, v)
        s, k = vv["skipped"], vv["kept"]
        validated = (s["n"] >= MIN_EXCLUDED_VALIDATION and s["mean_r"] is not None and k["mean_r"] is not None
                     and s["mean_r"] < 0 and s["mean_r"] < k["mean_r"])
        whole = evaluate(df, f, op, v)
        found.append({"id": lesson_id(symbol, f, op, v), "feature": f, "op": op, "value": v,
                      "status": "VALIDATED" if validated else "UNVALIDATED",
                      "evidence": {"discovery": d, "validation": vv, "all": whole,
                                   "trades_total": int(len(df)), "rules_tested": len(rules),
                                   "discovery_period": [str(disc["entry_time"].iloc[0]), str(disc["entry_time"].iloc[-1])],
                                   "validation_period": [str(val["entry_time"].iloc[0]), str(val["entry_time"].iloc[-1])]}})
    # One lesson per feature: keep the strongest discovery t.
    best: dict[str, dict] = {}
    for l in sorted(found, key=lambda x: -(x["evidence"]["discovery"]["t"] or 0)):
        best.setdefault(l["feature"], l)
    lessons = sorted(best.values(), key=lambda x: (x["status"] != "VALIDATED", -(x["evidence"]["discovery"]["t"] or 0)))
    return {"status": "OK", "trades": int(len(df)), "rules_tested": len(rules), "lessons": lessons}


def loss_flags(trade: pd.Series, population: pd.DataFrame) -> list[str]:
    """Deterministic 'what was unusual' list for one trade vs all trades of the same run."""
    flags = []
    for f in NUMERIC:
        if f not in population or pd.isna(trade.get(f)):
            continue
        col = pd.to_numeric(population[f], errors="coerce").dropna()
        if len(col) < 20:
            continue
        v = float(trade[f])
        if v >= col.quantile(0.8):
            flags.append(f"{f} high ({v:.3g} ≥ p80 {col.quantile(0.8):.3g})")
        elif v <= col.quantile(0.2):
            flags.append(f"{f} low ({v:.3g} ≤ p20 {col.quantile(0.2):.3g})")
    if trade.get("trend_align") == -1:
        flags.append("against the daily trend")
    if trade.get("ambiguous"):
        flags.append("stop and target inside one bar (counted as stop)")
    return flags


def resolve_idea(idea: dict, bars: pd.DataFrame, spread: float, horizon_minutes: int = 1440) -> tuple[str, float | None]:
    """Track a trade idea on M1 bars after it was proposed (hypothetical fill at entry_ref).

    BUY exits on bid (bar prices); SELL exits on ask (bar + spread). Stop and target in the same bar
    count as STOP. Not resolved within the horizon -> TIMEOUT at the last close.
    """
    entry, sl, tp = idea["entry_ref"], idea["sl"], idea.get("tp")
    buy = idea["side"] == "BUY"
    risk = abs(entry - sl)
    if risk <= 0 or bars.empty:
        return "OPEN", None
    start = pd.Timestamp(idea["created_at"])
    window = bars[(bars["time"] >= start.ceil("min")) & (bars["time"] < start + pd.Timedelta(minutes=horizon_minutes))]
    if window.empty:
        return "OPEN", None
    shift = 0.0 if buy else spread
    for _, b in window.iterrows():
        lo, hi = b["low"] + shift, b["high"] + shift
        hit_sl = lo <= sl if buy else hi >= sl
        hit_tp = tp is not None and (hi >= tp if buy else lo <= tp)
        if hit_sl:
            return "STOP", -1.0
        if hit_tp:
            return "TARGET", round(abs(tp - entry) / risk, 3)
    end_time = window["time"].iloc[-1] + pd.Timedelta(minutes=1)
    if end_time < start + pd.Timedelta(minutes=horizon_minutes):
        return "OPEN", None
    last = float(window["close"].iloc[-1]) + shift
    return "TIMEOUT", round(((last - entry) if buy else (entry - last)) / risk, 3)


def idea_stats(ideas: list[dict]) -> dict:
    done = [i for i in ideas if i.get("outcome") in ("STOP", "TARGET", "TIMEOUT") and i.get("outcome_r") is not None]
    if not done:
        return {"resolved": 0, "note": "No resolved ideas yet."}
    r = np.array([i["outcome_r"] for i in done], dtype=float)
    by = {}
    for i in done:
        by.setdefault(i["symbol"], []).append(i["outcome_r"])
    return {"resolved": len(done), "win_rate": round(float((r > 0).mean()), 3), "mean_r": round(float(r.mean()), 3),
            "total_r": round(float(r.sum()), 3),
            "by_symbol": {k: {"n": len(v), "mean_r": round(float(np.mean(v)), 3)} for k, v in by.items()},
            "warning": "fewer than 30 resolved ideas: not enough to judge" if len(done) < 30 else None}
