"""Aggregates all /api/v1 routers.

As later phases add routers (auth, market, scanner, signals, positions,
orders, trades, analytics, risk, strategies, backtests), they are
registered here rather than directly in app.main, keeping main.py
minimal.
"""

from fastapi import APIRouter

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
)

api_v1_router = APIRouter()
api_v1_router.include_router(system.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(settings.router)
api_v1_router.include_router(market.router)
api_v1_router.include_router(intelligence.router)
api_v1_router.include_router(price_action.router)
api_v1_router.include_router(strategies.router)
api_v1_router.include_router(signals.router)
api_v1_router.include_router(trading.router)
api_v1_router.include_router(approvals.router)
api_v1_router.include_router(analytics.router)
api_v1_router.include_router(backtests.router)
api_v1_router.include_router(broker.router)
api_v1_router.include_router(live.router)
api_v1_router.include_router(market_ws.router)
