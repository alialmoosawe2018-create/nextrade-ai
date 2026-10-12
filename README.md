# NexTrade AI — Nasdaq ORB research engine (phase A1)

Private research project. **Simulation only. Real trading is locked in code.**
Nothing in this repository is a trading recommendation or a claim of profitability.

## What works now (A1)
- Deterministic Opening Range Breakout + Retest strategy on M1 bars (rules: `docs/TRADING_RULES.md`)
- Independent risk engine (sizing from the broker's contract spec, daily loss lockout, kill switch, …)
- Paper broker (bid/ask, slippage, commission, conservative same-bar stop/target)
- Backtest runner with full journal, metrics with confidence intervals, cost sensitivity ×1/×1.5/×2
- Chronological dev/validation/holdout split with a holdout-reuse ledger
- Data-quality checks; HTML report with charts for visual review
- MOCK demo (synthetic data, labelled everywhere)

Not yet: MT5 connection (A2), web UI (B), AI council (C). See `docs/ROADMAP.md`.

## Setup (Windows, PowerShell, from the project folder)
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

## Run the tests
```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
cd ..
.\.venv\Scripts\python.exe tools\mutate.py
```

## MOCK pipeline demo
```powershell
cd backend
..\.venv\Scripts\python.exe -m app.cli demo
```
Writes `data\runs\<stamp>-mock-demo-<hash>\report.html`. Its numbers are synthetic and mean nothing.

## Real data (after phase A2 fills `config/instrument.yaml` from MT5)
```powershell
cd backend
..\.venv\Scripts\python.exe -m app.cli quality  --data ..\data\NAS100_M1.parquet --start 2025-01-02 --end 2026-09-30
..\.venv\Scripts\python.exe -m app.cli backtest --data ..\data\NAS100_M1.parquet --start 2025-01-02 --end 2026-09-30 --split dev
```
`backtest` refuses to run while the instrument spec is unverified. `--split holdout` runs once.

## Configuration
`config/strategy_orb.yaml`, `config/risk.yaml`, `config/costs.yaml`, `config/instrument.yaml`.
Strategy distances are in price units (index points). Risk caps are enforced in code.
