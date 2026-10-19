"""Aggregates all /api/v1 routers.

As later phases add routers (auth, market, scanner, signals, positions,
orders, trades, analytics, risk, strategies, backtests), they are
registered here rather than directly in app.main, keeping main.py
minimal.
"""

from fastapi import APIRouter, Depends

from app.api.deps import read_access
from app.api.v1 import (
    analytics,
    approvals,
    auth,
    backtests,
    broker,
    intelligence,
    live,
    market,
    market_ws,
    price_action,
    settings,
    signals,
    strategies,
    system,
    trading,
    universes,
)

api_v1_router = APIRouter()
# Data routers: a signed-in user is required when API_REQUIRE_AUTH is on
# (default in production); write routes keep their own role checks.
_read = [Depends(read_access)]
api_v1_router.include_router(system.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(settings.router)
api_v1_router.include_router(market.router, dependencies=_read)
api_v1_router.include_router(intelligence.router, dependencies=_read)
api_v1_router.include_router(price_action.router, dependencies=_read)
api_v1_router.include_router(strategies.router, dependencies=_read)
api_v1_router.include_router(signals.router, dependencies=_read)
api_v1_router.include_router(trading.router, dependencies=_read)
api_v1_router.include_router(approvals.router, dependencies=_read)
api_v1_router.include_router(analytics.router, dependencies=_read)
api_v1_router.include_router(backtests.router, dependencies=_read)
# broker: per-route checks (the Kite redirect /zerodha/callback must stay open)
api_v1_router.include_router(broker.router)
api_v1_router.include_router(live.router, dependencies=_read)
api_v1_router.include_router(universes.router, dependencies=_read)
api_v1_router.include_router(market_ws.router)
