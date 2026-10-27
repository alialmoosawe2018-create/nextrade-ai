"""Test helper: remove symbols from Market Watch (no trading), then show what tick MT5 returns."""
import sys

import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize failed", mt5.last_error())
    sys.exit(1)
for name in sys.argv[1:]:
    print(name, "| unselect:", mt5.symbol_select(name, False), mt5.last_error(),
          "| tick after:", mt5.symbol_info_tick(name))
mt5.shutdown()
