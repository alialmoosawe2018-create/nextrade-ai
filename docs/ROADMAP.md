# Roadmap (order agreed with the owner, 2026-10-12)

Rules and target architecture come from the NexTrade AI project brief. The phase order was changed
deliberately: the question "does this strategy have an edge after costs on the broker's own data?"
is answered **before** any UI or AI work.

| Phase | Content | Exit condition |
|---|---|---|
| **A1** ✅ | Repo, clock, ORB+Retest, risk engine, paper broker, backtest runner, metrics, quality checks, MOCK demo, HTML review report, tests + mutation check | All tests green; every mutation killed |
| **A2** | Read-only MT5 connection; `instrument.yaml` from `symbol_info`; history depth; data download; quality report; spread by minute from the open; daily tick recording; news-day filter; dev/validation backtests | Owner reviews 5 days visually against MT5 |
| **Gate 1** | Holdout run once. Criteria written **before** it is opened: positive net expectancy after costs, holds at costs ×1.5, drawdown acceptable to the owner, adequate sample | **Owner's decision** |
| B | FastAPI + Next.js UI (Arabic RTL / English, dark, responsive): Overview, Backtesting, Journal, Risk first | — |
| C | AI council in shadow mode: one provider first, structured validated output, compare strategy-only vs +1 model vs council on the same candidates; key metric = mean R of rejected candidates with its CI | Months of shadow data |
| D | MT5 demo: read-only first, then demo orders after paper tests. Real trading stays locked | — |
| E | Hardening: auth, secrets, TLS, monitoring, backups — before any network exposure | — |
