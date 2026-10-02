"""Position sizing.

    risk_amount = capital x risk_percentage
    quantity    = risk_amount / abs(entry - stop_loss)

then, in order: prices rounded to the tick size (the stop is rounded
away from the entry, so rounding never shrinks the real risk), quantity
rounded down to whole lots, and capped by the maximum position value,
the capital available for margin and the broker's maximum order
quantity. Every cap that reduced the quantity is named in
`constraints`. Rounding is always down, so the actual risk is never
more than the risk amount (before slippage).
"""

from __future__ import annotations

import math

from app.risk.config import RiskLimits
from app.risk.schemas import PositionSize, TradeSide

_EPS = 1e-9


def round_to_tick(price: float, tick: float, mode: str = "nearest") -> float:
    steps = price / tick
    if mode == "down":
        n = math.floor(steps + _EPS)
    elif mode == "up":
        n = math.ceil(steps - _EPS)
    else:
        n = round(steps)
    # Round the float noise away (0.05 * 2047 = 102.35000000000001).
    decimals = max(0, -int(math.floor(math.log10(tick))) + 2)
    return round(n * tick, decimals)


def floor_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    return int(math.floor(quantity / lot_size + _EPS)) * lot_size


def calculate_position_size(
    *,
    capital: float,
    available_capital: float,
    entry: float,
    stop_loss: float,
    side: TradeSide,
    limits: RiskLimits,
) -> PositionSize:
    entry_r = round_to_tick(entry, limits.tick_size)
    stop_r = round_to_tick(stop_loss, limits.tick_size, "down" if side == TradeSide.LONG else "up")
    risk_amount = round(max(capital, 0.0) * limits.risk_per_trade_percent / 100.0, 2)
    risk_per_unit = round(abs(entry_r - stop_r), 6)

    if risk_per_unit <= 0 or entry_r <= 0:
        return PositionSize(
            quantity=0,
            raw_quantity=0.0,
            risk_amount=risk_amount,
            risk_per_unit=risk_per_unit,
            actual_risk=0.0,
            position_value=0.0,
            entry=entry_r,
            stop_loss=stop_r,
            constraints=["invalid stop distance"],
        )

    raw = risk_amount / risk_per_unit
    quantity = floor_to_lot(raw, limits.lot_size)
    constraints: list[str] = []
    if quantity < raw - _EPS and quantity > 0:
        constraints.append(f"rounded down to lot size {limits.lot_size}")

    caps = [
        ("max position value", floor_to_lot(limits.max_position_value / entry_r, limits.lot_size)),
        (
            "available capital",
            floor_to_lot(
                max(available_capital, 0.0) * limits.margin_multiplier / entry_r, limits.lot_size
            ),
        ),
        ("max order quantity", floor_to_lot(limits.max_order_quantity, limits.lot_size)),
    ]
    for name, cap in caps:
        if cap < quantity:
            quantity = cap
            constraints.append(f"capped by {name}")

    return PositionSize(
        quantity=quantity,
        raw_quantity=round(raw, 4),
        risk_amount=risk_amount,
        risk_per_unit=risk_per_unit,
        actual_risk=round(quantity * risk_per_unit, 2),
        position_value=round(quantity * entry_r, 2),
        entry=entry_r,
        stop_loss=stop_r,
        constraints=constraints,
    )
