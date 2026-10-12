"""Hard safety gates. These are code, not settings.

There is no configuration key, environment variable or API call that turns
real trading on. Enabling it later must be a reviewed code change to this
file plus a real broker adapter that does not exist yet.
"""
from __future__ import annotations

from typing import Final

from app.core.models import AccountMode

REAL_TRADING_ENABLED: Final[bool] = False


class RealTradingLocked(RuntimeError):
    pass


def assert_mode_allowed(mode: AccountMode) -> None:
    if mode == AccountMode.REAL and not REAL_TRADING_ENABLED:
        raise RealTradingLocked("Real trading is locked in code (REAL_TRADING_ENABLED=False).")
