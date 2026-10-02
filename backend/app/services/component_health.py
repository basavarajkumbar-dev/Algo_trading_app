"""Per-component system health for the dashboard (Phase 9).

Eight components, each HEALTHY / WARNING / CRITICAL with a plain-English
detail and the metrics it was judged on:

    market_data      simulated feed running, provider connected, latest tick age
    backend          the API process (answering this request)
    database         PostgreSQL reachable
    redis            Redis reachable (not on the trading path yet: WARNING when down)
    websocket        the /ws/market broadcaster and its connected clients
    strategy_engine  intelligence, price action, strategy and signal services
                     running, refreshing on time, without errors
    risk_engine      the RiskManager inside the paper trading service; WARNING
                     while new entries are blocked (emergency stop, limits)
    paper_broker     the paper broker and trade storage

Read-only: this module observes the services, it never changes them.
"""

from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.constants import ComponentStatus
from app.market_data.base import utcnow
from app.market_data.schemas import MarketDataProviderStatus
from app.services.system_health import _check_database, _check_redis

STARTED_AT = time.monotonic()


class HealthLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


_ORDER = [HealthLevel.HEALTHY, HealthLevel.WARNING, HealthLevel.CRITICAL]


def worst(levels: list[HealthLevel]) -> HealthLevel:
    return max(levels, key=_ORDER.index, default=HealthLevel.HEALTHY)


class ComponentReport(BaseModel):
    key: str
    name: str
    status: HealthLevel
    detail: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class ComponentHealthResponse(BaseModel):
    status: HealthLevel
    components: list[ComponentReport]
    demo_mode: bool
    trading_mode: str
    checked_at: datetime
    note: str = (
        "Market data is simulated. HEALTHY means a component is running as "
        "designed; it says nothing about trading results."
    )


# --- classification (pure) --------------------------------------------------------------


def classify_market_data(
    *,
    running: bool,
    provider: str | None,
    tick_age_seconds: float | None,
    demo_mode: bool,
    max_age: float = 10.0,
) -> tuple[HealthLevel, str]:
    if not running:
        return HealthLevel.CRITICAL, "Market data feed is not running"
    if provider != MarketDataProviderStatus.CONNECTED.value:
        return HealthLevel.CRITICAL, f"Provider is {provider or 'unknown'}"
    if tick_age_seconds is None:
        return HealthLevel.WARNING, "No tick received yet"
    if tick_age_seconds > 6 * max_age:
        return HealthLevel.CRITICAL, f"Last tick {tick_age_seconds:.0f}s ago"
    if tick_age_seconds > max_age:
        return HealthLevel.WARNING, f"Last tick {tick_age_seconds:.0f}s ago"
    if not demo_mode:
        return (
            HealthLevel.WARNING,
            "Simulated feed running while DEMO_MODE=false: no live provider exists yet",
        )
    return HealthLevel.HEALTHY, f"Simulated feed streaming, last tick {tick_age_seconds:.1f}s ago"


def classify_service(
    *,
    name: str,
    running: bool,
    last_refresh_age: float | None,
    expected_seconds: float,
    last_error: str | None,
) -> tuple[HealthLevel, str]:
    if not running:
        return HealthLevel.CRITICAL, f"{name} is not running"
    if last_error:
        return HealthLevel.WARNING, f"{name}: last refresh failed ({last_error})"
    if last_refresh_age is None:
        return HealthLevel.WARNING, f"{name} has not refreshed yet"
    if last_refresh_age > 3 * expected_seconds:
        return HealthLevel.WARNING, f"{name} last refreshed {last_refresh_age:.0f}s ago"
    return HealthLevel.HEALTHY, f"{name} refreshed {last_refresh_age:.0f}s ago"


def _age(ts: datetime | None, now: datetime) -> float | None:
    return (now - ts).total_seconds() if ts is not None else None


# --- assembly -------------------------------------------------------------------------------


async def get_component_health(state: Any, settings: Settings) -> ComponentHealthResponse:
    """`state` is `app.state`; missing services are reported, not assumed."""

    now = utcnow()
    reports: list[ComponentReport] = []

    # market data
    md = getattr(state, "market_data_service", None)
    provider: str | None = None
    tick_age: float | None = None
    if md is not None:
        try:
            provider = (await md.get_provider_status()).value
        except Exception as exc:  # noqa: BLE001
            provider = f"ERROR ({type(exc).__name__})"
        ages = [
            (now - s.timestamp).total_seconds()
            for s in (md.get_snapshot(sym) for sym in md.get_watchlist())
            if s is not None
        ]
        tick_age = min(ages) if ages else None
    level, detail = classify_market_data(
        running=bool(md and md.is_running),
        provider=provider,
        tick_age_seconds=tick_age,
        demo_mode=settings.demo_mode,
    )
    reports.append(
        ComponentReport(
            key="market_data",
            name="Market Data",
            status=level,
            detail=detail,
            metrics={
                "provider": provider,
                "simulated": True,
                "last_tick_age_seconds": round(tick_age, 2) if tick_age is not None else None,
                "ticks_accepted": md.stats.ticks_accepted if md else 0,
                "ticks_rejected": md.stats.ticks_rejected if md else 0,
                "symbols": len(md.get_watchlist()) if md else 0,
            },
        )
    )

    reports.append(
        ComponentReport(
            key="backend",
            name="Backend",
            status=HealthLevel.HEALTHY,
            detail=f"API answering ({settings.app_env})",
            metrics={"uptime_seconds": round(time.monotonic() - STARTED_AT)},
        )
    )

    db, redis = await _check_database(settings.database_url), await _check_redis(settings.redis_url)
    db_ok = db.status == ComponentStatus.CONNECTED
    reports.append(
        ComponentReport(
            key="database",
            name="Database",
            status=HealthLevel.HEALTHY if db_ok else HealthLevel.CRITICAL,
            detail="PostgreSQL reachable" if db_ok else (db.detail or "PostgreSQL unreachable"),
        )
    )
    redis_ok = redis.status == ComponentStatus.CONNECTED
    reports.append(
        ComponentReport(
            key="redis",
            name="Redis",
            status=HealthLevel.HEALTHY if redis_ok else HealthLevel.WARNING,
            detail=(
                "Redis reachable"
                if redis_ok
                else "Redis unreachable (not used by trading in this phase)"
            ),
        )
    )

    ws_clients = md.connection_manager.connection_count if md else 0
    reports.append(
        ComponentReport(
            key="websocket",
            name="WebSocket",
            status=HealthLevel.HEALTHY if md and md.is_running else HealthLevel.CRITICAL,
            detail=(
                f"/ws/market broadcasting to {ws_clients} client(s)"
                if md and md.is_running
                else "Nothing to broadcast: market data feed is not running"
            ),
            metrics={"connected_clients": ws_clients},
        )
    )

    # strategy engine = the four analysis services
    pa = getattr(state, "price_action_service", None)
    expected = pa.status().refresh_seconds if pa is not None else 15.0
    parts: list[tuple[HealthLevel, str]] = []
    sub: dict[str, Any] = {}
    for key, label, attr, interval in (
        ("intelligence", "Market intelligence", "market_intelligence_service", None),
        ("price_action", "Price action", "price_action_service", expected),
        ("strategies", "Strategies", "strategy_service", expected),
        ("signals", "Signal scanner", "signal_service", expected),
    ):
        service = getattr(state, attr, None)
        if service is None:
            parts.append((HealthLevel.CRITICAL, f"{label} is not running"))
            sub[key] = "missing"
            continue
        st = service.status()
        last = getattr(st, "last_refresh_at", None) or getattr(st, "last_scan_at", None)
        seconds: float = (
            interval if interval is not None else getattr(st, "refresh_seconds", expected)
        )
        lvl, text = classify_service(
            name=label,
            running=st.is_running,
            last_refresh_age=_age(last, now),
            expected_seconds=seconds,
            last_error=st.last_error,
        )
        parts.append((lvl, text))
        sub[key] = lvl.value
    level = worst([p[0] for p in parts])
    problems = [t for lvl, t in parts if lvl != HealthLevel.HEALTHY]
    reports.append(
        ComponentReport(
            key="strategy_engine",
            name="Strategy Engine",
            status=level,
            detail="; ".join(problems) if problems else "All analysis services refreshing on time",
            metrics=sub,
        )
    )

    # risk engine and paper broker live in the trading service
    trading = getattr(state, "trading_service", None)
    if trading is None:
        reports.append(
            ComponentReport(
                key="risk_engine",
                name="Risk Engine",
                status=HealthLevel.CRITICAL,
                detail="Paper trading service is not running",
            )
        )
        reports.append(
            ComponentReport(
                key="paper_broker",
                name="Paper Broker",
                status=HealthLevel.CRITICAL,
                detail="Paper trading service is not running",
            )
        )
    else:
        st = await trading.status()
        risk = st.risk
        if st.emergency_stop:
            r_level, r_detail = HealthLevel.WARNING, "Emergency stop active: new entries blocked"
        elif risk.entries_blocked and any(
            r.value in ("DAILY_LOSS_LIMIT", "MAX_TRADES_REACHED", "MAX_CONSECUTIVE_LOSSES")
            for r in risk.blocked_reasons
        ):
            r_level = HealthLevel.WARNING
            r_detail = "Risk limit reached: " + ", ".join(r.value for r in risk.blocked_reasons)
        else:
            r_level, r_detail = HealthLevel.HEALTHY, "Evaluating every order before it is placed"
        reports.append(
            ComponentReport(
                key="risk_engine",
                name="Risk Engine",
                status=r_level,
                detail=r_detail,
                metrics={
                    "trades_today": risk.state.trades_today,
                    "daily_loss_remaining": risk.daily_loss_remaining,
                    "blocked_reasons": [r.value for r in risk.blocked_reasons],
                },
            )
        )
        if not st.is_running:
            b_level, b_detail = HealthLevel.CRITICAL, "Paper trading loop is not running"
        elif not st.persistence_ok and st.persistence_error:
            b_level, b_detail = (
                HealthLevel.CRITICAL,
                f"Trades cannot be stored: {st.persistence_error}",
            )
        elif not st.system_healthy:
            b_level, b_detail = HealthLevel.WARNING, st.health_detail
        else:
            b_level, b_detail = HealthLevel.HEALTHY, "Simulating fills; trades are being stored"
        reports.append(
            ComponentReport(
                key="paper_broker",
                name="Paper Broker",
                status=b_level,
                detail=b_detail,
                metrics={
                    "state": st.state.value,
                    "working_orders": len(st.working_orders),
                    "open_positions": len(st.positions),
                    "slippage_bps": st.broker.slippage_bps,
                },
            )
        )

    return ComponentHealthResponse(
        status=worst([r.status for r in reports]),
        components=reports,
        demo_mode=settings.demo_mode,
        trading_mode=settings.trading_mode.value,
        checked_at=now,
    )
