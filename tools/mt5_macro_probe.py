"""Read-only: which macro/intermarket symbols does this MT5 account offer, and how much M1 history?"""
import re
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=60000)
pat = re.compile(r"^(DXY|USDX|DX|VIX|VOLX|US10Y|UST10|TNOTE|10Y|US2Y|BUND|US500|SPX|US2000|RUSSELL)", re.I)
syms = [s for s in (mt5.symbols_get() or ()) if pat.search(s.name) or re.search(r"(dollar index|volatility|treasury|10[- ]?year)", s.description or "", re.I)]
now = datetime.now(timezone.utc)
for s in syms[:40]:
    mt5.symbol_select(s.name, True)
    r = mt5.copy_rates_range(s.name, mt5.TIMEFRAME_M1, now - timedelta(days=20), now)
    print(f"{s.name:14} | {s.description[:45]:45} | {s.path[:30]:30} | M1 bars 20d: {0 if r is None else len(r)}")
print("count", len(syms))
mt5.shutdown()
