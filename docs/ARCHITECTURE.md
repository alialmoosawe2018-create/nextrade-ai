# Architecture

```
Browser (Next.js 16, Arabic RTL / English)  ──/api, /reports──►  FastAPI on 127.0.0.1:8000
                                                                     │
        ┌────────────────────────────────────────────────────────────┤
        │ app/brokers/mt5_gateway.py  READ-ONLY allow-list ──► MetaTrader5 package ──► MT5 terminal (this PC)
        │ app/brokers/fake_mt5.py     MOCK stand-in (synthetic, labelled MOCK)
        │ app/brokers/server_time.py  server clock → UTC (measured live + scored on history)
        │
        │ app/services/analysis.py ─► history → quality → splits → backtest ×costs → data/runs/<run>/
        │ app/services/live.py ─────► poll completed M1 bars → SessionRunner → data/live/<day>/journal.jsonl
        │ app/services/backtest.py ─► SessionRunner: per bar t
        │       1. fill order approved at close of t-1 (open of t)      ─► execution/paper.py
        │       2. exits on bar t                                        ─► execution/paper.py
        │       3. strategy sees bar t; SIGNAL → risk/engine.py evaluate ─► queue for t+1
        │ app/storage/store.py ─────► data/accounts.json (no password field), data/settings/*.json
        └────────────────────────────────────────────────────────────
```

- One engine: the live monitor and the backtest run the same `SessionRunner`; a test proves a live
  minute-by-minute replay produces exactly the backtest's trades. The only difference is the risk
  clock (real time live), so signals from late/backfilled data are rejected as STALE_DATA.
- `core/`: domain types, validated configs + config hash, NYSE calendar (exchange_calendars XNYS),
  hard safety gate.
- Defaults live in `config/*.yaml`; the owner's edits are saved in `data/settings/`.
- Analysis runs as a background job (`/api/analysis/run` → poll `/api/analysis/jobs/{id}`): the Next.js
  proxy cuts requests at 30 s and a first MT5 history download can take longer.
- Learning: `research/features.py` (day context from PRIOR sessions + signal features; vectorized
  `context_table` proven equal to the reference), `research/learning.py` (fixed discovery→validation
  protocol, idea outcome tracking), `storage/db.py` (SQLite), `api/learning.py`. Approved lessons are
  `EntryFilter`s inside `OrbConfig` (so they change the config hash) and are applied by `SessionRunner`
  before the risk engine — identically in backtest and live.
- Tests: `backend/tests` (pytest, no broker/keys). `tools/mutate.py` breaks each guard in a throwaway
  copy and expects a red test.
