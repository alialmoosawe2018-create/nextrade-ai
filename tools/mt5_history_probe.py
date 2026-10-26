"""Read-only: how much M1 history does the terminal return for USTEC? Prints counts only."""
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=60000)
mt5.symbol_select("USTEC", True)
now = datetime.now(timezone.utc)
for days in (3, 30, 120, 365):
    r = mt5.copy_rates_range("USTEC", mt5.TIMEFRAME_M1, now - timedelta(days=days), now)
    n = 0 if r is None else len(r)
    first = datetime.fromtimestamp(int(r[0]["time"]), timezone.utc) if n else None
    print(f"last {days}d: {n} bars, first={first}", mt5.last_error() if r is None else "")
t = mt5.symbol_info_tick("USTEC")
print("tick:", None if t is None else (t.time, t.bid, t.ask))
mt5.shutdown()
