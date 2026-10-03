"""GAINZALGO PRO MAX — backend application entrypoint.

Application shell, configuration, structured logging, CORS, API
versioning, auth and settings, plus the background services started and
stopped by this module's lifespan: the market data feed (simulated,
Phase 3; or real Zerodha data once logged in, Phase 13), market intelligence (Phase 4), price action (Phase 5), the
strategy engine (Phase 6) and the signal engine + NIFTY 50 scanner
(Phase 7), and the paper trading service (Phase 8): the risk engine and
a simulated paper broker. Mode is PAPER; no real broker is connected and
automatic trading starts OFF. See docs/development-roadmap.md for what
each later phase adds.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_v1_router
from app.approvals.service import ApprovalService
from app.backtesting.service import BacktestService
from app.brokers.service import BrokerService, ensure_not_live_by_accident
from app.brokers.zerodha.backtest_data import download_history
from app.brokers.zerodha.runtime import build_zerodha_runtime
from app.core.config import get_settings
from app.core.constants import API_V1_PREFIX, MarketDataMode
from app.core.logging import configure_logging, get_logger
from app.database.session import async_session_factory
from app.execution.paper_broker import paper_broker_config_from_settings
from app.market_data.base import MarketDataProvider
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.paper_provider import PaperMarketDataProvider
from app.market_data.service import MarketDataService
from app.market_data.sinks import CandleStore, MarketDataMonitor, MarketStateCache
from app.market_data.universe import NIFTY_INDEX_SYMBOL
from app.market_intelligence.service import MarketIntelligenceService
from app.price_action.config import MultiTimeframeConfig, PriceActionConfig
from app.price_action.service import PriceActionService
from app.risk.config import risk_limits_from_settings
from app.signals.config import signal_engine_config
from app.signals.service import SignalService
from app.strategies.service import StrategyService
from app.trading.engine import EngineConfig
from app.trading.service import TradingService

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting %s (env=%s, demo_mode=%s, trading_mode=%s)",
        settings.app_name,
        settings.app_env,
        settings.demo_mode,
        settings.trading_mode.value,
    )

    # Zerodha (Phase 13): one shared Kite client + auth + instrument master.
    # Without KITE_API_KEY / KITE_API_SECRET everything here is inert.
    zerodha = build_zerodha_runtime(settings, session_factory=async_session_factory)
    app.state.zerodha = zerodha

    # Market data: MARKET_DATA_MODE=ZERODHA (the default when Kite credentials
    # are configured) streams real NSE data once someone logs in to Kite;
    # MOCK keeps the simulator. Neither choice affects execution, which stays
    # PAPER unless EXECUTION_MODE=ZERODHA and LIVE_TRADING_ENABLED=true.
    data_mode = settings.effective_market_data_mode
    provider: MarketDataProvider
    if data_mode == MarketDataMode.ZERODHA and zerodha.configured:
        provider = zerodha.market_data_provider()
    else:
        if data_mode == MarketDataMode.ZERODHA:
            logger.warning(
                "MARKET_DATA_MODE=ZERODHA but Kite credentials are missing: "
                "using SIMULATED data (clearly labelled)"
            )
        rotation = settings.market_data_regime_rotation_seconds or None
        provider = PaperMarketDataProvider(
            delegate=MockMarketDataProvider(
                tick_interval_seconds=settings.market_data_tick_interval_seconds,
                regime_rotation_seconds=rotation,
            )
        )
    logger.info(
        "Market data source: %s; execution mode: %s",
        provider.source,
        settings.effective_execution_mode.value,
    )
    market_data_service = MarketDataService(
        provider,
        watchlist=settings.market_data_default_watchlist,
        warmup_candles=settings.market_data_warmup_candles,
        stale_threshold_seconds=settings.data_stale_threshold_seconds,
    )
    state_cache = MarketStateCache(settings.redis_url)
    if not provider.simulated:
        # Redis market:* keys hold real data only, so a reader can never
        # mistake simulated prices for real ones (market:status says which).
        market_data_service.add_candle_listener(state_cache.on_candles)
    candle_store: CandleStore | None = None
    if not provider.simulated and settings.persist_candles:
        candle_store = CandleStore(
            async_session_factory,
            source=provider.source,
            persist_raw_ticks=settings.persist_raw_ticks,
        )
        market_data_service.add_candle_listener(candle_store.on_candles)
    listeners = getattr(provider, "tick_listeners", None)
    if listeners is not None and not provider.simulated:
        listeners.append(state_cache.on_tick)
        if candle_store is not None:
            listeners.append(candle_store.on_tick)
    await market_data_service.start()
    await zerodha.start()  # validates KITE_ACCESS_TOKEN (if set): the feed starts on success
    await state_cache.start()
    if candle_store is not None:
        await candle_store.start()
    market_monitor = MarketDataMonitor(
        market_data_service, session_factory=async_session_factory, cache=state_cache
    )
    await market_monitor.start()
    app.state.market_data_service = market_data_service
    app.state.market_state_cache = state_cache
    app.state.candle_store = candle_store
    app.state.market_monitor = market_monitor

    intelligence_service = MarketIntelligenceService(
        market_data_service,
        benchmark_symbol=NIFTY_INDEX_SYMBOL,
        timeframe=settings.intelligence_timeframe,
        refresh_seconds=settings.intelligence_refresh_seconds,
    )
    await intelligence_service.start()
    app.state.market_intelligence_service = intelligence_service

    price_action_service = PriceActionService(
        market_data_service,
        config=PriceActionConfig(
            multi_timeframe=MultiTimeframeConfig(
                context_timeframe=settings.price_action_context_timeframe,
                setup_timeframe=settings.price_action_setup_timeframe,
                entry_timeframe=settings.price_action_entry_timeframe,
            )
        ),
        refresh_seconds=settings.price_action_refresh_seconds,
    )
    await price_action_service.start()
    app.state.price_action_service = price_action_service

    strategy_service = StrategyService(
        market_data_service, price_action_service, benchmark_symbol=NIFTY_INDEX_SYMBOL
    )
    await strategy_service.start()
    app.state.strategy_service = strategy_service

    signal_service = SignalService(
        market_data_service,
        strategy_service,
        benchmark_symbol=NIFTY_INDEX_SYMBOL,
        session_factory=async_session_factory,
        config=signal_engine_config(settings),
    )
    await signal_service.start()
    app.state.signal_service = signal_service

    trading_service = TradingService(
        market_data_service,
        signal_service,
        starting_capital=settings.paper_capital,
        limits=risk_limits_from_settings(settings),
        broker_config=paper_broker_config_from_settings(settings),
        engine_config=EngineConfig(
            # PAPER and APPROVAL execute on the paper broker (APPROVAL only
            # after a person approves); LIVE fails every risk check.
            trading_mode=settings.trading_mode.value,
            entry_timeout_seconds=settings.paper_entry_timeout_seconds,
            emergency_close_positions=settings.paper_emergency_close_positions,
        ),
        session_factory=async_session_factory,
    )
    await trading_service.start()
    app.state.trading_service = trading_service

    approval_service = ApprovalService(
        trading_service,
        async_session_factory,
        ttl_seconds=settings.approval_ttl_seconds,
        broadcast=market_data_service.connection_manager.broadcast,
    )
    await approval_service.start()
    app.state.approval_service = approval_service

    def _session_open() -> bool:
        ok, _ = trading_service.engine.risk.entries_allowed(datetime.now(UTC))
        return bool(ok)

    broker_service = BrokerService(
        settings,
        session_factory=async_session_factory,
        system_healthy=lambda: getattr(app.state, "last_overall_health", None) == "HEALTHY",
        session_open=_session_open,
        emergency_stop=lambda: trading_service.engine.emergency_stop_active,
        broadcast=market_data_service.connection_manager.broadcast,
        auth=zerodha.auth if zerodha.configured else None,
    )
    ensure_not_live_by_accident(settings)
    await broker_service.start()
    app.state.broker_service = broker_service

    history_fetcher = None
    if zerodha.configured and zerodha.historical is not None:
        historical = zerodha.historical
        instruments = zerodha.instruments

        async def history_fetcher(req: Any, progress: Any) -> str:
            # DATA_SOURCE=ZERODHA backtests: real 5m candles, downloaded once
            # per range into the backtest cache. Needs today's Kite login.
            if not zerodha.auth.authenticated:
                raise RuntimeError("Zerodha history needs a Kite login (Settings > Zerodha)")
            if instruments is not None:
                await instruments.ensure_fresh()
            path = await download_history(
                historical,
                list(req.symbols),
                req.start_date,
                req.end_date,
                Path(os.environ.get("BACKTEST_CACHE_DIR", "/tmp/gainzalgo-backtest-cache")),
                warmup_days=req.warmup_days,
                progress=progress,
            )
            return str(path)

    backtest_service = BacktestService(
        async_session_factory,
        data_dir=settings.backtest_data_dir,
        workers=settings.backtest_workers,
        history_fetcher=history_fetcher,
    )
    await backtest_service.start()
    app.state.backtest_service = backtest_service

    yield

    await backtest_service.stop()
    await broker_service.stop()
    await approval_service.stop()
    await trading_service.stop()
    await signal_service.stop()
    await strategy_service.stop()
    await price_action_service.stop()
    await intelligence_service.stop()
    await market_monitor.stop()
    await market_data_service.stop()
    if candle_store is not None:
        await candle_store.stop()
    await state_cache.stop()
    await zerodha.stop()
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description=(
        "Modular, explainable, risk-controlled algorithmic intraday "
        "trading platform. Runs in PAPER/demo mode by default with "
        "simulated market data — see PROJECT_CONTRACT.md."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix=API_V1_PREFIX)


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Structured error shape defined in PROJECT_CONTRACT.md
    ("Error Handling")."""

    return {"error": {"code": code, "message": message, "details": details or {}}}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global fallback so an unexpected error never leaks a raw traceback
    or an unstructured response to the client."""

    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=_error_body(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected error occurred.",
        ),
    )


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    """Minimal root route so hitting the backend directly shows something
    useful instead of a 404."""

    return {
        "name": settings.app_name,
        "status": "running",
        "docs": "/docs",
        "health": f"{API_V1_PREFIX}/system/health",
    }
