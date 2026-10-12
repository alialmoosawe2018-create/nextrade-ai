"""Spread/slippage/commission helpers shared by strategy, risk and the paper broker."""
from __future__ import annotations

from app.core.config import CostModel, InstrumentSpec
from app.core.models import Bar


def spread_price(bar: Bar, point: float, costs: CostModel) -> float:
    """Spread in price units for this bar: data spread x multiplier, floored at min_spread."""
    return max(bar.spread_points * point * costs.spread_multiplier, costs.min_spread)


def money_per_price_unit(spec: InstrumentSpec, volume: float) -> float:
    """Account-currency value of a 1.0 price move for `volume` lots (tick_value / tick_size)."""
    if spec.tick_size is None or spec.tick_value is None:
        raise ValueError("tick_size/tick_value missing")
    return volume * spec.tick_value / spec.tick_size
