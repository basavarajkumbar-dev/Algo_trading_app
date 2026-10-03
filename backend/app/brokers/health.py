"""BrokerHealthService: one verdict for the Zerodha integration (Phase 13).

    HEALTHY        logged in, WebSocket connected, data fresh, instruments
                   synced, REST answering, database and Redis up
    DEGRADED       working with a problem: data STALE, instrument master not
                   synced today, REST errors, Redis down
    DISCONNECTED   not configured, or the WebSocket is down
    AUTH_REQUIRED  configured but no valid Kite session (daily login)
    ERROR          the feed gave up (e.g. instrument master unavailable) or
                   the database is down

Every check is listed with its own status so the dashboard can show which
one failed. Read-only; never contains a credential.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.constants import ComponentStatus
from app.market_data.base import utcnow
from app.services.system_health import _check_database, _check_redis


class BrokerHealthStatus:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ERROR = "ERROR"


class HealthCheck(BaseModel):
    name: str
    ok: bool
    status: str
    detail: str = ""


class BrokerHealthReport(BaseModel):
    status: str
    broker: str = "ZERODHA"
    configured: bool
    market_data_source: str
    execution_mode: str
    checked_at: datetime
    checks: list[HealthCheck] = Field(default_factory=list)


def _age(ts: datetime | None, now: datetime) -> str:
    return "never" if ts is None else f"{(now - ts).total_seconds():.0f}s ago"


async def broker_health(state: Any, settings: Any) -> BrokerHealthReport:
    now = utcnow()
    rt = getattr(state, "zerodha", None)
    md = getattr(state, "market_data_service", None)
    configured = bool(rt and rt.configured)
    db, redis = await asyncio.gather(
        _check_database(settings.database_url), _check_redis(settings.redis_url)
    )
    checks: list[HealthCheck] = [
        HealthCheck(
            name="database",
            ok=db.status == ComponentStatus.CONNECTED,
            status=db.status.value,
            detail=db.detail or "",
        ),
        HealthCheck(
            name="redis",
            ok=redis.status == ComponentStatus.CONNECTED,
            status=redis.status.value,
            detail=redis.detail or "",
        ),
    ]
    source = md.source if md is not None else "UNKNOWN"
    report = BrokerHealthReport(
        status=BrokerHealthStatus.DISCONNECTED,
        configured=configured,
        market_data_source=source,
        execution_mode=settings.effective_execution_mode.value,
        checked_at=now,
        checks=checks,
    )
    if not configured or rt is None:
        checks.append(
            HealthCheck(
                name="auth",
                ok=False,
                status="NOT_CONFIGURED",
                detail="KITE_API_KEY / KITE_API_SECRET not set: SIMULATED data, PAPER execution",
            )
        )
        report.checks = checks
        return report

    auth = rt.auth.status()
    checks.append(
        HealthCheck(
            name="auth",
            ok=bool(auth["authenticated"]),
            status=auth["state"],
            detail=f"session expires {auth['expires_at']}" if auth["expires_at"] else "",
        )
    )
    client = rt.client
    last_rest = None
    if client is not None and client.last_success_at is not None:
        last_rest = time.monotonic() - client.last_success_at
    checks.append(
        HealthCheck(
            name="rest_api",
            ok=client is not None and client.last_error is None,
            status="OK" if client is not None and client.last_error is None else "ERROR",
            detail=(
                f"last success {last_rest:.0f}s ago" if last_rest is not None else "no call yet"
            )
            + (f"; last error: {client.last_error}" if client and client.last_error else ""),
        )
    )
    instruments = await rt.instruments.status() if rt.instruments is not None else None
    checks.append(
        HealthCheck(
            name="instrument_master",
            ok=bool(instruments and instruments["status"] == "SYNCED"),
            status=instruments["status"] if instruments else "UNAVAILABLE",
            detail=(
                f"{instruments['in_memory']} cash/index instruments loaded; "
                f"last sync {instruments['last_success_at']}"
                if instruments
                else ""
            ),
        )
    )
    details = md.provider.status_details() if md is not None else {}
    websocket = str(details.get("websocket") or ("N/A" if source != "ZERODHA" else "DISCONNECTED"))
    if source == "ZERODHA":
        checks.append(
            HealthCheck(
                name="websocket",
                ok=websocket == "CONNECTED",
                status=websocket,
                detail=f"{details.get('subscribed_instruments', 0)} instruments subscribed",
            )
        )
        data_status = md.data_status() if md is not None else "DISCONNECTED"
        checks.append(
            HealthCheck(
                name="data_freshness",
                ok=data_status == "LIVE",
                status=data_status,
                detail=f"last tick {_age(md.last_tick_at if md else None, now)}",
            )
        )
    else:
        checks.append(
            HealthCheck(
                name="websocket",
                ok=True,
                status="N/A",
                detail="MARKET_DATA_MODE=MOCK: the Kite feed is not used",
            )
        )

    by = {c.name: c for c in checks}
    if not by["database"].ok or details.get("feed_state") == "ERROR":
        report.status = BrokerHealthStatus.ERROR
    elif not by["auth"].ok:
        report.status = BrokerHealthStatus.AUTH_REQUIRED
    elif source == "ZERODHA" and not by["websocket"].ok:
        report.status = BrokerHealthStatus.DISCONNECTED
    elif not all(c.ok for c in checks):
        report.status = BrokerHealthStatus.DEGRADED
    else:
        report.status = BrokerHealthStatus.HEALTHY
    report.checks = checks
    return report


def market_data_status(state: Any) -> dict[str, Any]:
    """GET /system/market-data: the feed at a glance (no credentials)."""

    md = state.market_data_service
    details = md.provider.status_details()
    now = utcnow()
    last = md.last_tick_at
    status = md.data_status(now)
    rt = getattr(state, "zerodha", None)
    rest = "N/A"
    if not md.simulated and rt is not None and rt.client is not None:
        rest = "ERROR" if rt.client.last_error else ("OK" if rt.client.last_success_at else "IDLE")
    timeframes = ("5m", "15m", "60m")
    benchmark = md.get_watchlist()[0] if md.get_watchlist() else None
    from app.market_data.schemas import Timeframe

    candles = (
        {tf: md.candle_status(benchmark, Timeframe(tf)) for tf in timeframes} if benchmark else {}
    )
    return {
        "provider": md.source,
        "mode": "SIMULATED" if md.simulated else "ZERODHA_LIVE",
        "simulated": md.simulated,
        "status": status,
        "websocket": details.get("websocket", "SIMULATED" if md.simulated else "DISCONNECTED"),
        "feed_state": details.get("feed_state", "RUNNING" if md.is_running else "STOPPED"),
        "rest_api": rest,
        "last_tick": last,
        "last_tick_age_seconds": None if last is None else round((now - last).total_seconds(), 1),
        "latency_ms": details.get("latency_ms"),
        "subscribed_instruments": details.get(
            "subscribed_instruments", len(md.get_watchlist()) if md.simulated else 0
        ),
        "unresolved_symbols": details.get("unresolved_symbols", []),
        "stale": md.is_stale(),
        "stale_threshold_seconds": md.stale_threshold.total_seconds(),
        "ticks_accepted": md.stats.ticks_accepted,
        "ticks_rejected": md.stats.ticks_rejected + int(details.get("ticks_rejected", 0) or 0),
        "rejected_by_issue": {
            **md.stats.rejected_by_issue,
            **dict(details.get("rejected_by_issue", {}) or {}),
        },
        "warmup_in_progress": md.warmup_in_progress,
        "last_warmup_at": md.last_warmup_at,
        "candle_status_symbol": benchmark,
        "candles": candles,
    }


__all__ = ["BrokerHealthReport", "BrokerHealthStatus", "broker_health", "market_data_status"]
