"""Kite orders: request building, status mapping and trade-book rows.

The implementations live in `mapping` (shared with the adapter since Phase
11); this module is the documented entry point for order-related Kite
translation. Placing, modifying and cancelling orders happens only in
ZerodhaBrokerAdapter, called only by OrderManager behind the execution
gate — never from market data, strategies or signals. The shared
KiteClient refuses order endpoints unless EXECUTION_MODE=ZERODHA and
LIVE_TRADING_ENABLED=true.
"""

from app.brokers.zerodha.mapping import (
    external_order_id,
    map_status,
    order_from_kite,
    order_params,
    order_tag,
    trade_from_kite,
)

__all__ = [
    "external_order_id",
    "map_status",
    "order_from_kite",
    "order_params",
    "order_tag",
    "trade_from_kite",
]
