"""Read-only: earliest USTEC M1 bar available, fetched in 20-day chunks going back. Counts only."""
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=60000)
print("account:", (mt5.account_info().server if mt5.account_info() else None))
mt5.symbol_select("USTEC", True)
end = datetime.now(timezone.utc); total = 0; earliest = None; empty = 0
while empty < 3 and end > datetime(2015, 1, 1, tzinfo=timezone.utc):
    start = end - timedelta(days=20)
    r = mt5.copy_rates_range("USTEC", mt5.TIMEFRAME_M1, start, end)
    n = 0 if r is None else len(r)
    if n:
        total += n; earliest = datetime.fromtimestamp(int(r[0]["time"]), timezone.utc); empty = 0
    else:
        empty += 1
    end = start
print("M1 bars:", total, "earliest:", earliest)
mt5.shutdown()
