"""Trade ideas: risk-checked proposals the OWNER executes by hand in MT5.

The platform does not send orders. An idea carries the exact symbol, side, stop, target and a lot
size computed from the broker's contract spec and the risk settings, so it can be typed into MT5.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from app.brokers.mt5_gateway import Mt5Gateway
from app.core.config import CostModel, RiskConfig
from app.risk.engine import floor_to_step

MAX_SPREAD_TO_STOP = 0.25   # spread may use at most 25% of the stop distance
IDEA_TTL = timedelta(minutes=5)


class IdeaRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class TradeIdea:
    symbol: str
    side: str
    entry_ref: float
    sl: float
    tp: float | None
    volume: float
    risk_money: float
    loss_at_sl: float
    reward_risk: float | None
    spread: float
    rationale: str
    data_source: str
    account_mode: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    valid_until: str = field(default_factory=lambda: (datetime.now(timezone.utc) + IDEA_TTL).isoformat(timespec="seconds"))
    status: str = "PROPOSED"
    execution: str = "MANUAL — the platform sends no orders; enter it yourself in MT5 if you choose to"

    def to_dict(self) -> dict:
        return asdict(self)


def propose(gw: Mt5Gateway, symbol: str, side: str, sl: float, tp: float | None, risk_pct: float | None,
            risk: RiskConfig, costs: CostModel, rationale: str) -> TradeIdea:
    if not risk.trading_enabled:
        raise IdeaRejected("TRADING_DISABLED", "Kill switch is on — release it before new trade ideas.")
    if gw.info is None:
        raise IdeaRejected("NOT_CONNECTED", "Not connected to MT5.")
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise IdeaRejected("BAD_SIDE", f"side must be BUY or SELL, got {side!r}")
    if not sl or sl <= 0:
        raise IdeaRejected("STOP_LOSS_REQUIRED", "Every trade idea needs a stop-loss.")
    spec, _ = gw.instrument_spec(symbol)
    if spec.missing_fields():
        raise IdeaRejected("SPEC_MISSING", f"Contract spec incomplete: {spec.missing_fields()}")
    q = gw.quote(symbol)
    if q is None:
        raise IdeaRejected("NO_QUOTE", f"No live quote for {symbol}.")
    buy = side == "BUY"
    entry = q["ask"] if buy else q["bid"]
    if (buy and not sl < entry) or (not buy and not sl > entry):
        raise IdeaRejected("STOP_WRONG_SIDE", f"Stop {sl} is on the wrong side of the entry {entry}.")
    if tp is not None and ((buy and not tp > entry) or (not buy and not tp < entry)):
        raise IdeaRejected("TARGET_WRONG_SIDE", f"Target {tp} is on the wrong side of the entry {entry}.")
    dist = abs(entry - sl)
    if q["spread"] > MAX_SPREAD_TO_STOP * dist:
        raise IdeaRejected("SPREAD_TOO_WIDE_FOR_STOP",
                           f"Spread {q['spread']:.5g} is more than {MAX_SPREAD_TO_STOP:.0%} of the stop distance {dist:.5g}.")
    pct = min(risk_pct or risk.risk_per_trade_pct, risk.risk_per_trade_pct)
    equity = gw.info.equity or gw.info.balance
    if not equity or equity <= 0:
        raise IdeaRejected("NO_EQUITY", "Account equity unknown.")
    risk_money = equity * pct / 100.0
    per_unit = spec.tick_value / spec.tick_size
    loss_per_lot = (dist + 2 * costs.slippage) * per_unit + 2 * costs.commission_per_lot_per_side
    cap = spec.volume_max if risk.max_volume is None else min(spec.volume_max, risk.max_volume)
    volume = floor_to_step(min(risk_money / loss_per_lot, cap), spec.volume_step)
    if volume < spec.volume_min:
        raise IdeaRejected("MIN_LOT_EXCEEDS_RISK",
                           f"Minimum lot {spec.volume_min} would risk {spec.volume_min * loss_per_lot:.2f} "
                           f"{spec.account_currency or ''} > allowed {risk_money:.2f}.")
    return TradeIdea(symbol=symbol, side=side, entry_ref=entry, sl=sl, tp=tp, volume=volume, risk_money=round(risk_money, 2),
                     loss_at_sl=round(volume * loss_per_lot, 2), reward_risk=round(abs(tp - entry) / dist, 2) if tp else None,
                     spread=q["spread"], rationale=rationale[:600], data_source=gw.info.source,
                     account_mode=gw.info.account_mode)
