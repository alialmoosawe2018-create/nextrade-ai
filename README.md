# NexTrade AI — Nasdaq research & paper-trading platform

Private, local platform. **Simulation / paper trading only. Real trading is locked in code.**
Nothing here is a trading recommendation or a claim of profitability.

## What it does
- **MT5 accounts (read-only):** add any MT5 broker (server + account number), connect with the
  password typed at connect time (never stored) or attach to the MT5 already logged in on this PC.
  Discovers the broker's Nasdaq symbol (US100 / NAS100 / USTEC …) and reads its contract spec.
- **Broker server time is measured, not assumed:** live from the newest tick, and from history by
  checking which timezone rule puts the 09:30 New York volatility jump at the NYSE open.
- **Analysis:** ORB + Retest backtest on the broker's own M1 history (background job), costs ×1/×1.5/×2,
  dev / validation / holdout (holdout runs once), data-quality report, visual HTML report.
- **Live monitor:** the same engine as the backtest, on live completed bars → risk engine → paper broker.
  Late/stale signals are rejected. Every decision is journaled under `data/live/`.
- **Chat with Claude:** «حلل الذهب»، «شغّل المراقبة»، «اقترح صفقة شراء على US100»، «وقف». Claude analyses
  facts computed from your broker's data and can propose risk-sized trade ideas — **executed by hand in MT5**;
  the platform sends no orders. Needs a one-time `claude` → `/login` on this computer.
- **Learning (evidence, not stories):** every trade stores the facts known at entry (gap, trend, range
  width, stop size, spread vs stop, timing). The platform searches the latest analysis for rules like
  "skip when spread_to_stop > x": a rule must hold on the first 60% of trades (≥20 skipped, negative mean,
  t ≥ 3) AND on the unseen last 40%. Validated rules are **proposed**; you approve → entry filter → new
  strategy version. Claude reviews losses as hypotheses (variance vs process). Claude's chat ideas are
  tracked on prices (target/stop first) to measure its real accuracy, which it sees in its memory.
  Data: SQLite file `data/nextrade.db`.
- **Markets:** US100, US30, gold, oil, EURUSD, GBPUSD, USDJPY — matched to whatever names your broker uses.
- **Emergency stop** (top bar, or type «وقف»): kill switch + live monitor off, instantly.
- **MOCK broker:** synthetic data (labelled MOCK everywhere) to try the whole platform without MT5.

## Start (Windows)
Double-click **`start-platform.bat`**. First run creates the Python environment, installs packages and
builds the interface. It then opens http://localhost:3100. Close the two windows to stop.

Requirements: Python 3.12 (`py -3.12`), Node.js 20.9+, and for real accounts the broker's MT5 terminal
installed on this PC (the official `MetaTrader5` Python package is Windows-only).

## First use
1. **حسابات MT5 / MT5 accounts** → add your account (server name exactly as in MT5's login window,
   account number). Prefer the **investor (read-only) password** and a **demo** account first.
   Or add the MOCK broker to try everything.
2. Connect → choose the Nasdaq symbol → compare the shown spec with MT5's *Specification* window.
3. **الإعدادات / Settings → Risk:** set `max_spread` (every trade is rejected until it is set).
4. **التحليل / Analysis:** run on `dev` first. The first run also determines the broker's server time.
5. **المراقبة الحية / Live:** start the monitor during (or before) the US session.

Check your broker's client agreement about third-party software before connecting a real account.

## Developer commands
```powershell
# backend tests (no broker, no keys needed)
cd backend; ..\.venv\Scripts\python.exe -m pytest; cd ..
# mutation check: every guard must be caught by a test
.\.venv\Scripts\python.exe tools\mutate.py
# frontend checks
cd frontend; npx tsc --noEmit; npx eslint src; npm run build; cd ..
# CLI MOCK demo (no UI)
cd backend; ..\.venv\Scripts\python.exe -m app.cli demo; cd ..
```

Docs: `docs/TRADING_RULES.md` · `docs/ARCHITECTURE.md` · `docs/SECURITY.md` · `docs/ROADMAP.md`.
