"""Helpers (not tests) for building synthetic ClosedTrade lists."""
from __future__ import annotations

import pandas as pd

from app.execution.paper import ClosedTrade

T0 = pd.Timestamp("2026-10-12 13:40", tz="UTC")


def fake_trades(rs: list[float], risk: float = 100.0) -> list[ClosedTrade]:
    out = []
    for i, r in enumerate(rs):
        t = T0 + pd.Timedelta(days=i)
        out.append(ClosedTrade(
            candidate_id=f"c{i}", session_day=t.date(), direction="LONG", strategy_version="v",
            config_hash="h", data_source="TEST", volume=1.0, entry_time=t, entry_price=100.0,
            stop=99.0, target=102.0, exit_time=t + pd.Timedelta(minutes=10), exit_price=100.0 + r,
            exit_reason="TARGET" if r > 0 else "STOP", ambiguous_bar=False, spread_at_entry=0.5,
            gross_pnl=r * risk, commission=0.0, net_pnl=r * risk, planned_risk_money=risk,
            r_multiple=r, mfe=max(r, 0), mae=max(-r, 0)))
    return out
