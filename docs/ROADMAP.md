# Roadmap

Rules and target architecture come from the NexTrade AI project brief (owner's constitution).

| Phase | Content | Status |
|---|---|---|
| **A1** | Clock, ORB+Retest, risk engine, paper broker, backtest, metrics, quality, splits/holdout ledger, MOCK demo, HTML report, tests + mutation check | ✅ 2026-10-12 |
| **Platform (B-lite)** | Owner request 2026-10-12: log in to any MT5 broker from inside the platform. FastAPI + Next.js (Arabic RTL / English, dark): Overview, MT5 accounts (read-only gateway, investor password advised), Live monitor (same engine as backtest, paper only), Analysis (background jobs on broker history, server-time detection), Settings | ✅ built; real-terminal connection not yet exercised |
| **A2 on real data** | Owner connects a demo account → symbol + spec verified against MT5 → server time decided from history → dev/validation runs → visual review of 5 days vs MT5 | waiting for owner |
| **Gate 1** | Holdout run once; criteria written before it is opened (positive net expectancy after costs, holds at ×1.5, acceptable drawdown, adequate sample) | **owner's decision** |
| **Learning loop** | Owner request 2026-10-12: learn from failed trades without overfitting. Features at entry (no look-ahead), SQLite store, evidence engine (discovery 60% → validation 40%, t≥3, ≥20 skipped), owner approves → entry filter + new config hash, Claude loss reviews = hypotheses, Claude idea tracking + memory | ✅ built; on MOCK it correctly finds no lesson (random data) |
| C | AI council in shadow mode: one provider first, validated structured output, strategy-only vs +1 model vs council; key metric = mean R of REJECTED candidates with CI | after Gate 1 |
| D | Demo orders (still never real) after paper results hold | later |
| E | Hardening: auth, secrets store, TLS, monitoring, backups — before any network exposure | later |
