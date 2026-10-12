# Security boundaries

## Real trading is locked in code
- `backend/app/core/safety.py`: `REAL_TRADING_ENABLED = False`. No setting, env var, API call or UI
  control changes it. The risk engine rejects `REAL` mode even when a config lists it as allowed,
  and the paper broker refuses to start in `REAL` mode.
- The MT5 gateway (`backend/app/brokers/mt5_gateway.py`) can only invoke an allow-list of READ calls
  (`READ_ONLY_CALLS`). There is no code path to `order_send`, `order_check`, positions or history.
  Tests + mutation checks prove an order call is refused and never reaches the terminal.

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
- The repository is private and local; it must not be pushed to a public remote.
- AI provider keys (phase C) will live only in `.env` on this machine, never in the browser.
