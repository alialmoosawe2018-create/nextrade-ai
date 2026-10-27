"""Read-only: why does a symbol have no tick? Prints select result, tick, visibility and trade mode."""
import sys

import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize failed", mt5.last_error())
    sys.exit(1)
for name in sys.argv[1:] or ["XAUUSD", "US500", "USTEC"]:
    info = mt5.symbol_info(name)
    sel = mt5.symbol_select(name, True)
    tick = mt5.symbol_info_tick(name)
    print(name, "| select:", sel, mt5.last_error(), "| tick:", tick and (tick.time, tick.bid, tick.ask),
          "| visible:", info and info.visible, "| trade_mode:", info and info.trade_mode,
          "| path:", info and info.path)
mt5.shutdown()
