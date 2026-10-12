"""Backtest metrics. Never invents numbers: no trades -> INSUFFICIENT DATA."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict

import pandas as pd

from app.execution.paper import ClosedTrade

MIN_TRADES_FOR_CONCLUSION = 30


def json_safe(v):
    """NaN/inf -> None (not computable), recursively. JSON has no NaN and we never fake a number."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    return v


def _r_stats(rs: list[float]) -> dict:
    n = len(rs)
    mean = sum(rs) / n
    if n > 1:
        sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1))
        se = sd / math.sqrt(n)
    else:
        sd = se = float("nan")
    return {"n": n, "expectancy_r": mean, "sd_r": sd, "se_r": se,
            "ci95_r": [mean - 1.96 * se, mean + 1.96 * se] if n > 1 else None}


def summarize(trades: list[ClosedTrade], starting_balance: float, sessions_covered: int) -> dict:
    if not trades:
        return {"status": "INSUFFICIENT DATA", "trades": 0, "sessions_covered": sessions_covered,
                "note": "No simulated trades were produced; no performance figures exist."}
    df = pd.DataFrame([asdict(t) for t in trades]).sort_values("exit_time")
    wins = df[df.net_pnl > 0]
    losses = df[df.net_pnl <= 0]
    gross_win, gross_loss = wins.net_pnl.sum(), -losses.net_pnl.sum()
    equity = starting_balance + df.net_pnl.cumsum()
    peak = equity.cummax().clip(lower=starting_balance)
    dd = (peak - equity)
    out = {
        "status": "OK" if len(df) >= MIN_TRADES_FOR_CONCLUSION else "INSUFFICIENT_SAMPLE",
        "simulated": True,
        "trades": int(len(df)),
        "sessions_covered": sessions_covered,
        "first_trade": str(df.entry_time.min()),
        "last_trade": str(df.exit_time.max()),
        "win_rate": float(len(wins) / len(df)),
        "gross_pnl": float(df.gross_pnl.sum()),
        "commission": float(df.commission.sum()),
        "net_pnl": float(df.net_pnl.sum()),
        "expectancy_money": float(df.net_pnl.mean()),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_win_r": float(wins.r_multiple.mean()) if len(wins) else None,
        "avg_loss_r": float(losses.r_multiple.mean()) if len(losses) else None,
        "max_drawdown_money": float(dd.max()),
        "max_drawdown_pct": float((dd / peak).max() * 100),
        "ambiguous_bar_trades": int(df.ambiguous_bar.sum()),
        "exit_reasons": dict(Counter(df.exit_reason)),
        "r": _r_stats(df.r_multiple.tolist()),
        "by_direction": {d: _r_stats(g.r_multiple.tolist()) for d, g in df.groupby("direction")},
        "by_weekday": {str(k): _r_stats(g.r_multiple.tolist())
                       for k, g in df.groupby(pd.to_datetime(df.entry_time).dt.day_name())},
    }
    return json_safe(out)
