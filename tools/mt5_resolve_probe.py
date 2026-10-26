"""Read-only: which broker symbol does resolve() pick for every asset, and what else matched? Writes data/probes/resolve.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import MetaTrader5 as mt5  # noqa: E402

from app.brokers.assets import ASSETS, resolve  # noqa: E402

if not mt5.initialize():
    print("initialize failed", mt5.last_error())
    sys.exit(1)
rows = [{"name": s.name, "path": s.path} for s in (mt5.symbols_get() or ())]
out = {"symbols": len(rows), "assets": {}}
for a in ASSETS:
    bases = a.symbol_patterns + a.substitutes
    near = [r for r in rows if any(r["name"].upper().startswith(b) for b in bases)][:15]
    out["assets"][a.key] = {"chosen": resolve(a, rows), "candidates": near}
out["non_equity_oil_like"] = [
    {"name": s.name, "path": s.path, "description": s.description} for s in (mt5.symbols_get() or ())
    if not any(w in s.path.lower() for w in ("stock", "etf"))
    and any(w in f"{s.name} {s.description} {s.path}".lower() for w in ("oil", "crude", "brent", "wti", "energ"))]
mt5.shutdown()
dest = ROOT / "data" / "probes" / "resolve.json"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
for k, v in out["assets"].items():
    print(k, "->", v["chosen"])
