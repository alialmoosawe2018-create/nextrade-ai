"""Validated configuration models + YAML loading + config hashing.

All strategy distances are in PRICE units (index points as quoted, e.g. 5.0),
never MT5 "points". MT5 spreads arrive in MT5 points and are converted with
the instrument's `point`.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.models import AccountMode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SpecSource(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    TEST = "TEST"
    MT5_SYMBOL_INFO = "MT5_SYMBOL_INFO"


class InstrumentSpec(_Strict):
    """Contract specification. Every value must come from the broker (A2).

    Missing values stay None; the risk engine rejects instead of guessing.
    """

    symbol: str | None = None
    source: SpecSource = SpecSource.UNVERIFIED
    point: float | None = Field(default=None, gt=0)
    digits: int | None = Field(default=None, ge=0)
    tick_size: float | None = Field(default=None, gt=0)
    tick_value: float | None = Field(default=None, gt=0)  # profit per tick per 1 lot, account currency
    contract_size: float | None = Field(default=None, gt=0)
    volume_min: float | None = Field(default=None, gt=0)
    volume_max: float | None = Field(default=None, gt=0)
    volume_step: float | None = Field(default=None, gt=0)
    currency_profit: str | None = None
    account_currency: str | None = None

    REQUIRED_FOR_SIZING: ClassVar[tuple[str, ...]] = (
        "point", "tick_size", "tick_value", "volume_min", "volume_max", "volume_step",
    )

    def missing_fields(self) -> list[str]:
        return [f for f in self.REQUIRED_FOR_SIZING if getattr(self, f) is None]


class StopMethod(str, Enum):
    RETEST_SWING = "retest_swing"
    RANGE_OPPOSITE = "range_opposite"


FILTER_FEATURES = ("gap_atr", "prev_range_atr", "atr_ratio", "trend_daily", "weekday", "minutes_after_open",
                   "or_width_atr", "stop_atr", "spread_to_stop", "trend_align", "breakout_delay_min")


class EntryFilter(_Strict):
    """An owner-approved lesson: skip a signal when `feature op value`. None never matches."""

    id: str = Field(min_length=1, max_length=40)
    feature: str
    op: str = Field(pattern="^(gt|lt|eq)$")
    value: float
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _known(self) -> "EntryFilter":
        if self.feature not in FILTER_FEATURES:
            raise ValueError(f"unknown feature {self.feature}")
        return self

    def matches(self, features: dict) -> bool:
        v = features.get(self.feature)
        if v is None:
            return False
        return v > self.value if self.op == "gt" else v < self.value if self.op == "lt" else v == self.value


class OrbConfig(_Strict):
    """Opening Range Breakout + Retest. See docs/TRADING_RULES.md."""

    name: str = "orb_retest"
    version: str = "1"
    opening_range_minutes: int = Field(15, ge=1, le=120)
    confirm_tf_minutes: int = Field(5, ge=1, le=60)
    breakout_buffer: float = Field(0.0, ge=0)
    breakout_deadline_minutes: int = Field(90, ge=1, le=390)
    retest_window_minutes: int = Field(30, ge=1, le=240)
    retest_tolerance: float = Field(3.0, ge=0)
    retest_max_penetration: float = Field(10.0, gt=0)
    stop_method: StopMethod = StopMethod.RETEST_SWING
    stop_buffer: float = Field(2.0, ge=0)
    min_stop_distance: float = Field(5.0, gt=0)
    max_stop_distance: float = Field(60.0, gt=0)
    reward_risk: float = Field(1.5, gt=0, le=10)
    last_entry_minutes: int = Field(120, ge=1, le=390)
    max_hold_minutes: int = Field(30, ge=1, le=390)
    flat_before_close_minutes: int = Field(5, ge=0, le=60)
    max_gap_minutes: int = Field(2, ge=0, le=30)
    min_range: float | None = Field(None, ge=0)
    max_range: float | None = Field(None, gt=0)
    entry_filters: tuple[EntryFilter, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "OrbConfig":
        if self.min_stop_distance >= self.max_stop_distance:
            raise ValueError("min_stop_distance must be < max_stop_distance")
        if self.breakout_deadline_minutes <= self.opening_range_minutes:
            raise ValueError("breakout_deadline_minutes must be after the opening range ends")
        if self.last_entry_minutes <= self.opening_range_minutes:
            raise ValueError("last_entry_minutes must be after the opening range ends")
        if self.min_range is not None and self.max_range is not None and self.min_range >= self.max_range:
            raise ValueError("min_range must be < max_range")
        return self


class RiskConfig(_Strict):
    """Hard limits enforced in backend code. Upper bounds are deliberate."""

    risk_per_trade_pct: float = Field(0.5, gt=0, le=2.0)
    max_daily_loss_pct: float = Field(1.5, gt=0, le=5.0)
    max_trades_per_day: int = Field(2, ge=1, le=10)
    max_concurrent_positions: int = Field(1, ge=1, le=3)
    max_spread: float | None = Field(None, gt=0)  # price units; None = not configured -> reject
    max_data_age_seconds: int = Field(90, ge=1, le=3600)
    max_volume: float | None = Field(None, gt=0)
    trading_enabled: bool = True  # False = kill switch
    allowed_modes: tuple[AccountMode, ...] = (AccountMode.SIMULATION,)


class CostModel(_Strict):
    """Execution cost assumptions. Spread itself comes from the data."""

    spread_multiplier: float = Field(1.0, ge=1.0)
    min_spread: float = Field(0.0, ge=0)          # price units, floor on the data's spread
    slippage: float = Field(0.0, ge=0)            # price units per market fill (entry, stop, time exit)
    commission_per_lot_per_side: float = Field(0.0, ge=0)  # account currency
    max_entry_deviation: float | None = Field(None, gt=0)  # reject fill if |fill - entry_ref| exceeds it

    def scaled(self, k: float) -> "CostModel":
        """Cost-sensitivity copy: every cost component multiplied by k (k >= 1)."""
        if k < 1:
            raise ValueError("cost scaling below 1 would understate costs")
        return self.model_copy(update={
            "spread_multiplier": self.spread_multiplier * k,
            "min_spread": self.min_spread * k,
            "slippage": self.slippage * k,
            "commission_per_lot_per_side": self.commission_per_lot_per_side * k,
        })


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def config_hash(*models: BaseModel) -> str:
    """Stable short hash of the exact configuration a result was produced with."""
    payload = [m.model_dump(mode="json") for m in models]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]
