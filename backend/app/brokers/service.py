"""BrokerService: supervises the live broker (Zerodha) next to paper trading.

Paper trading is unaffected: the TradingService keeps executing on its
PaperBrokerAdapter. This service only

  * builds the ZerodhaBrokerAdapter from environment variables. Without
    credentials it reports NOT_CONFIGURED and does nothing else: the app
    runs in PAPER mode;
  * connects (read-only) when credentials and an access token exist,
    re-checks health every BROKER_HEALTH_INTERVAL_SECONDS, reconnects after
    a disconnect and reconciles orders every
    BROKER_RECONCILE_INTERVAL_SECONDS through its OrderManager;
  * exposes status, read-only account data and the login flow to the API;
  * stores order / broker events as system events (BROKER_*).

Live orders stay disabled: the execution gate needs, among other things,
an explicit user arming action, and this phase provides none. Nothing here
switches the trading mode.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.zerodha import ZerodhaBrokerAdapter, zerodha_from_settings
from app.brokers.zerodha.auth import AuthState, KiteAuthService
from app.core.config import Settings
from app.core.logging import get_logger
from app.database.models.system_event import SystemEvent
from app.execution.models import BrokerHealth, ConnectionState
from app.execution.order_manager import ExecutionGate, OrderEvent, OrderManager
from app.execution.reconciliation import OrderReconciliationService, positions_from_orders

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


class BrokerService:
    def __init__(
        self,
        settings: Settings,
        *,
        adapter: ZerodhaBrokerAdapter | None = None,
        session_factory: SessionFactory | None = None,
        system_healthy: Callable[[], bool] = lambda: False,
        session_open: Callable[[], bool] = lambda: False,
        emergency_stop: Callable[[], bool] = lambda: False,
        broadcast: Callable[[dict[str, Any]], Any] | None = None,
        auth: KiteAuthService | None = None,
        user_armed: Callable[[], bool] = lambda: False,
    ) -> None:
        self.settings = settings
        self.auth = auth
        if adapter is None:
            if auth is not None and auth.client is not None:
                # Phase 13: share the app's Kite client (token owned by the
                # auth service; order endpoints off unless live is enabled).
                adapter = zerodha_from_settings(settings, client=auth.client, access_token=None)
            else:
                adapter = zerodha_from_settings(settings)
        self.adapter = adapter
        if auth is not None:
            auth.add_listener(self._on_auth_state)
        self._session_factory = session_factory
        self._broadcast = broadcast
        self.gate = ExecutionGate(
            live_mode_configured=lambda: settings.is_live_trading_possible,
            user_armed=user_armed,  # Phase 14: an admin's explicit arming action
            system_healthy=system_healthy,
            session_open=session_open,
            emergency_stop=emergency_stop,
        )
        self.orders = OrderManager(self.adapter, self.gate, on_events=self._store_events)
        self.reconciliation = OrderReconciliationService(
            self.adapter,
            self.orders.orders,
            lambda: positions_from_orders(self.orders.orders()),
        )
        self._task: asyncio.Task[None] | None = None
        self.last_health: BrokerHealth | None = None
        self.last_loop_error: str | None = None
        self.paused = False  # disconnected by the user: no automatic reconnect

    # --- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        if not self.adapter.configured:
            logger.info("Zerodha not configured: running in PAPER mode only")
            self.last_health = await self.adapter.health_check()
            return
        state = await self.adapter.connect()
        logger.info("Zerodha adapter: %s (read-only; live orders not armed)", state.value)
        self.last_health = await self.orders.check_connection()
        self._task = asyncio.create_task(self._loop(), name="broker-supervisor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.adapter.aclose()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        health_every = self.settings.broker_health_interval_seconds
        reconcile_every = self.settings.broker_reconcile_interval_seconds
        loop = asyncio.get_running_loop()
        next_health = loop.time() + health_every
        while True:
            await asyncio.sleep(min(reconcile_every, health_every))
            if self.paused:
                continue
            try:
                if loop.time() >= next_health:
                    self.last_health = await self.orders.check_connection()
                    next_health = loop.time() + health_every
                if self.adapter.connection_state is ConnectionState.CONNECTED:
                    await self.orders.reconcile()
                self.last_loop_error = None
            except Exception as exc:  # noqa: BLE001 - supervision must keep running
                self.last_loop_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Broker supervision step failed")

    # --- actions ------------------------------------------------------------------

    async def refresh(self) -> BrokerHealth:
        self.paused = False
        self.last_health = await self.orders.check_connection()
        if self.adapter.configured and not self.is_running:
            self._task = asyncio.create_task(self._loop(), name="broker-supervisor")
        return self.last_health

    async def _on_auth_state(self, state: AuthState) -> None:
        if self.auth is None:
            return
        if state is AuthState.AUTHENTICATED:
            self.adapter.attach_session(self.auth.session.expires_at if self.auth.session else None)
            await self.refresh()
        elif state in (AuthState.TOKEN_EXPIRED, AuthState.LOGGED_OUT, AuthState.AUTH_ERROR):
            self.adapter.detach_session()
            self.last_health = await self.adapter.health_check()

    async def on_credentials_present(self) -> None:
        """Credentials were applied to the shared runtime before this service
        existed (saved credentials at start-up): give them to the adapter."""

        if self.auth is not None and self.auth.configured:
            self.adapter.set_credentials(*self.auth.credentials())

    async def on_credentials(self, api_key: str | None, api_secret: str | None) -> None:
        """The Zerodha runtime got new credentials (or lost them)."""

        self.adapter.set_credentials(api_key, api_secret)
        if self.adapter.configured:
            await self.refresh()
        else:
            if self._task is not None:
                self._task.cancel()
                self._task = None
            self.last_health = await self.adapter.health_check()

    async def login(self, request_token: str) -> dict[str, Any]:
        if self.auth is not None and self.auth.configured:
            await self.auth.generate_access_token(request_token)
            profile = await self.adapter.get_profile()
            return profile.model_dump()
        profile = await self.adapter.generate_session(request_token)
        await self.refresh()
        return profile.model_dump()

    async def disconnect(self) -> None:
        self.paused = True
        await self.adapter.disconnect()
        self.last_health = await self.adapter.health_check()

    # --- status -------------------------------------------------------------------------

    def live_blockers(self) -> list[str]:
        decision = self.gate.evaluate(
            self.adapter, risk_approved=True, resync_pending=self.orders.resync_pending
        )
        return decision.reasons

    def status(self) -> dict[str, Any]:
        a = self.adapter
        h = self.last_health
        last = self.orders.last_reconcile
        return {
            "execution_broker": "paper",
            "trading_mode": self.settings.trading_mode.value,
            "live_trading_possible": self.settings.is_live_trading_possible,
            "live_blockers": self.live_blockers(),
            "zerodha": {
                "configured": a.configured,
                "state": a.connection_state.value,
                "healthy": bool(h and h.healthy),
                "detail": h.detail if h else None,
                "checked_at": h.checked_at if h else None,
                "latency_ms": h.latency_ms if h else None,
                "last_success_at": h.last_success_at if h else None,
                "last_error": h.last_error if h else None,
                "consecutive_failures": h.consecutive_failures if h else 0,
                "session_expires_at": a.session_expires_at,
                "live_orders_armed": a.live_orders_armed,
                "market_protection": a.market_protection,
                "orders_per_second": self.settings.zerodha_orders_per_second,
                "supervisor_running": self.is_running,
                "paused_by_user": self.paused,
                "supervisor_error": self.last_loop_error,
                "auth": self.auth.status() if self.auth is not None else None,
            },
            "order_manager": {
                "tracked": len(self.orders.orders()),
                "working": len(self.orders.working()),
                "unresolved": len(self.orders.unresolved()),
                "external_orders": self.orders.external_orders,
                "resync_pending": self.orders.resync_pending,
                "last_reconcile_at": last.at if last else None,
                "last_reconcile_ok": last.ok if last else None,
                "last_reconcile_error": last.error if last else None,
            },
        }

    # --- events ---------------------------------------------------------------------------

    async def _store_events(self, events: list[OrderEvent]) -> None:
        if self._session_factory is not None:
            try:
                async with self._session_factory() as session:
                    for e in events:
                        session.add(
                            SystemEvent(
                                event_type=f"BROKER_{e.kind}",
                                severity=e.severity,
                                message=e.message[:1000],
                                event_metadata=_event_metadata(e),
                                created_at=e.at,
                            )
                        )
                    await session.commit()
            except Exception:  # noqa: BLE001 - events are also kept in memory
                logger.warning("Broker events not stored")
        if self._broadcast is not None:
            for e in events:
                try:
                    await self._broadcast(
                        {
                            "type": "trading_event",
                            "event_type": f"BROKER_{e.kind}",
                            "severity": e.severity,
                            "message": e.message,
                            "at": e.at.isoformat(),
                            "metadata": _event_metadata(e),
                        }
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("Broker event broadcast failed")


def _event_metadata(e: OrderEvent) -> dict[str, Any]:
    out: dict[str, Any] = {
        "broker_order_id": e.broker_order_id,
        "client_order_id": e.client_order_id,
        "symbol": e.symbol,
        "status": e.status.value if e.status else None,
        "filled_delta": e.filled_delta or None,
    }
    out.update(e.metadata)
    return {k: v for k, v in out.items() if v is not None}


def ensure_not_live_by_accident(settings: Settings) -> None:
    """Startup guard: LIVE configuration never changes how this phase trades.
    The paper engine refuses every non-PAPER mode, and no live order path
    is armed."""

    if settings.trading_mode.value != "PAPER":
        logger.warning(
            "TRADING_MODE=%s: live execution is not available in this build; "
            "the paper engine blocks every order outside PAPER mode",
            settings.trading_mode.value,
        )


__all__ = ["BrokerService", "ensure_not_live_by_accident"]
