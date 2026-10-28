# NexTrade AI — Nasdaq research & paper-trading platform

Local platform (the code is published on GitHub by the owner; it still runs only on 127.0.0.1). **Simulation / paper trading only. Real trading is locked in code.**
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

## Status at hand-over (2026-10-28)
- Tested on a **MetaQuotes-Demo** account (symbol `USTEC`; server time Europe/Nicosia, chosen from the live clock).
  That broker serves only ~100k M1 bars (history from 2026-07-15).
- Settings in use: `data/settings/strategy.json` (version `2-scaled`: distances scaled from the median 15-min
  opening-range width on the dev period, W = 144.4) and `data/settings/risk.json` (`news_filter: false` — the owner
  chose to trade through news days).
- Backtest 2026-07-16 → 2026-10-23: 34 trades, expectancy about −0.03 R per trade → **no edge shown yet**.
  The holdout period has never been opened. The learning loop needs ≥ 60 trades before it proposes lessons.
- Live paper monitor: 2026-10-26 LONG −1.0 R, 2026-10-27 SHORT −1.0 R (both stopped out within 15 min).
- Fundamental context is recorded in **shadow** only: macro moves before the open (S&P 500, dollar, gold) and a daily
  Claude bias (`/api/learning/biases`). Neither gates trades yet.
- Open item: on 2026-10-27 the live S&P/gold pre-open values were empty (they were recomputed fine later);
  check `spx_overnight` / `gold_overnight` in the live trade features.
- The in-app chat and daily brief use the local Claude Code CLI (`claude -p`), found by `find_claude_exe()`.
- There is **no order-sending code** (a test enforces it). Trade ideas are executed by hand.
