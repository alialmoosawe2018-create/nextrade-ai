# Security boundaries

## Real trading is locked in code
- `backend/app/core/safety.py`: `REAL_TRADING_ENABLED = False`. No setting, env var, API call or UI
  control changes it. The risk engine rejects `REAL` mode even when a config lists it as allowed,
  and the paper broker refuses to start in `REAL` mode.
- The MT5 gateway (`backend/app/brokers/mt5_gateway.py`) can only invoke an allow-list of READ calls
  (`READ_ONLY_CALLS`). There is no code path to `order_send`, `order_check`, positions or history.
  Tests + mutation checks prove an order call is refused and never reaches the terminal.

## In-platform chat with Claude (owner request 2026-10-12)
- The chat calls the locally installed Claude Code CLI under the owner's own Claude login (no API key in
  this app). The model runs with NO tools (`--tools ""`), no MCP servers, no session persistence, in an
  empty folder, and must answer in a JSON schema `{reply, actions[]}`.
- The model's actions are untrusted input: only six known action types are accepted, malformed ones are
  dropped, risk_pct above 2% is ignored. Actions run through the same guarded paths as the UI.
- "Stop" messages (وقف / stop …) never reach the model: they trigger the kill switch deterministically.
- Trade ideas are risk-checked proposals for MANUAL execution. **The platform sends no orders.**
  An execution module (order_send after a confirmation button, demo + real) was requested by the owner,
  but writing it was blocked by the session's safety classifier; it was not built. A test asserts that
  no `order_send(` call exists anywhere in the app.

## MT5 credentials (owner decision 2026-10-12: log in from inside the platform)
- The password is typed in the Accounts page only when connecting, sent over loopback to the local
  API as a `SecretStr`, handed once to `MetaTrader5.initialize()` and not kept by NexTrade.
- Account profiles (`data/accounts.json`) have no password field; the API rejects any extra key.
- The password is never logged, never returned by any endpoint, and the field is wiped as soon as
  the Accounts page is hidden (Next.js keeps hidden pages mounted). Tests scan every data file and
  response for the password.
- Recommended: the **investor (read-only) password** — the broker itself then refuses any order.
  The UI warns when a login can trade, and when the account is REAL.
- Note: MT5's own "save password" option, if ticked inside the terminal, is MT5's storage, not ours.

## Network
- The API binds to `127.0.0.1` and additionally refuses non-loopback clients. The web app proxies
  `/api` and `/reports` to it; nothing listens on a public interface.
- Authentication must be added before anything is ever exposed beyond this machine (phase E).

## Data
- `data/` is gitignored: market data, runs, journals, profiles and ledgers never enter git.
- The owner published the code on GitHub (2026-10-28). Never commit `data/` (accounts, trades, price history) or any `.env`; only `data/settings/*.json` (strategy + risk parameters, no account data) is tracked.
- AI provider keys (phase C) will live only in `.env` on this machine, never in the browser.
