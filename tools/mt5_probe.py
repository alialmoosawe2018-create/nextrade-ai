"""Read-only check that Python can reach the MT5 terminal on this PC. Sends no orders, prints no secrets."""
import re

import MetaTrader5 as mt5

ok = mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=60000)
print("initialize:", ok, mt5.last_error())
if ok:
    a = mt5.account_info()
    print("logged in:", a is not None)
    if a:
        print("server:", a.server, "| mode:", {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(a.trade_mode),
              "| read_only:", not a.trade_allowed, "| currency:", a.currency)
    syms = mt5.symbols_get() or ()
    pat = re.compile(r"US ?100|USTEC|NAS|NDX|US ?30|DJ|XAU|GOLD|OIL|WTI|BRENT|EURUSD|GBPUSD|USDJPY", re.I)
    print("symbols:", len(syms), [s.name for s in syms if pat.search(s.name)][:30])
    mt5.shutdown()
