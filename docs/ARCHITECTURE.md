# Architecture (phase A)

```
market data (MOCK | HISTORICAL parquet | MT5 in A2)
   └─ market/quality.py ── check / clean
        └─ services/backtest.py  (per bar t, fixed order)
             1. fill order approved at close of t-1  ─► execution/paper.py
             2. exits on bar t                       ─► execution/paper.py
             3. strategy sees bar t                  ─► strategy/orb.py (state machine)
                  SIGNAL ─► risk/engine.py evaluate() ─► approved? queue for t+1
             └─ journal (every candidate, taken or rejected) + trades
                  └─ reporting/metrics.py + reporting/html.py ─► data/runs/<stamp>-<tag>-<hash>/
```

- `core/` — domain types (`models.py`), validated configs + config hash (`config.py`), NYSE calendar
  (`clock.py`), hard safety gate (`safety.py`).
- Pure, broker-free modules: strategy, risk, paper execution. The MT5 adapter (A2) only produces bars
  and the instrument spec; it never decides anything.
- Storage in phase A: Parquet for bars, per-run folders (`run.json`, `summary.json`, `trades.csv`,
  `journal.csv`, `report.html`). SQLite/append-only DB arrives with the API in phase B.
- Decision records keep UTC internally; display converts to New York / Baghdad.
- Tests: `backend/tests` (pytest, no broker or keys needed). `tools/mutate.py` proves each guard is
  covered by breaking it in a throwaway copy and expecting a red test.
