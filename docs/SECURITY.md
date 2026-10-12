# Security boundaries

## Real trading is locked in code
- `backend/app/core/safety.py`: `REAL_TRADING_ENABLED = False`. No setting, env var, API call or UI
  control changes it. The risk engine rejects `REAL` mode even when a config lists it as allowed,
  and the paper broker refuses to start in `REAL` mode. Both are covered by tests and mutation checks.
- There is no live order adapter in the codebase. Phase D adds a **demo-only, read-first** MT5 adapter.

## Credentials
- **No MT5 password is ever stored or requested.** The MT5 Python package attaches to the terminal
  the owner logged into by hand. There is no password field anywhere in the product.
- AI provider keys (phase C) live only in `.env` on the machine running the backend, are never sent
  to a browser, never logged, never committed (`.gitignore` covers `.env`, `*.env`).
- Nobody should paste passwords, API keys or account secrets into chat.

## Data
- `data/` is gitignored: market data, runs and ledgers never enter git.
- The repository is private and local; it must not be pushed to a public remote.

## Network (current phase)
- Phase A has no server. The optional report viewer binds `127.0.0.1` only.
- Phase B (API + UI) must add authentication before anything listens on a non-loopback address.
