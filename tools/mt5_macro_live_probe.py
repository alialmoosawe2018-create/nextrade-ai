"""Read-only: rebuild the live macro context for a session day exactly as the monitor does, and show the raw bars."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import MetaTrader5 as mt5  # noqa: E402

from app.brokers.mt5_gateway import Mt5Gateway  # noqa: E402
from app.brokers.server_time import ServerTz  # noqa: E402
from app.core.clock import MarketCalendar  # noqa: E402
from app.research.features import MacroContext, fetch_macro_bars  # noqa: E402

day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
gw = Mt5Gateway(mt5)
gw.connect("attach")
tz = ServerTz("zone", zone="Europe/Nicosia")
cal = MarketCalendar()
s = cal.session(day)
prior = [p for p in cal.sessions_between(day - pd.Timedelta(days=8), day) if p.day < day][-2:]
bars = fetch_macro_bars(gw, tz, prior[0].open_utc - pd.Timedelta(hours=6), s.open_utc)
for k, b in bars.items():
    near = b[(b["time"] > s.open_utc - pd.Timedelta(hours=8)) & (b["time"] <= s.open_utc)]
    print(k, "rows", len(b), "| last", b["time"].iloc[-1] if len(b) else None, "| rows in 8h before open", len(near),
          "| first in window", near["time"].iloc[0] if len(near) else None)
print(MacroContext(bars).for_session(s, prior[-1], prior[-2]))
mt5.shutdown()
