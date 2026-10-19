"""The app's Zerodha components, built once from settings (Phase 13).

    KiteClient          one shared REST client; its access token lives in
                        memory only. Order endpoints are disabled on it unless
                        the effective execution mode is ZERODHA (EXECUTION_MODE
                        =ZERODHA *and* LIVE_TRADING_ENABLED=true); even then
                        only OrderManager reaches them, through the adapter
                        and its execution gate.
    KiteAuthService     login / callback / logout / expiry
    InstrumentService   instrument master sync + resolver
    ZerodhaHistoricalDataProvider
    ZerodhaMarketDataProvider (when MARKET_DATA_MODE=ZERODHA)

Credentials come from a user's encrypted record (entered in the app) or,
for an operator, from the environment; they can be set, replaced and removed
while the server runs (`apply_credentials`). Their presence never enables
live trading.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.zerodha.auth import KiteAuthService
from app.brokers.zerodha.historical import ZerodhaHistoricalDataProvider
from app.brokers.zerodha.instruments import (
    InstrumentRepository,
    InstrumentResolver,
    InstrumentService,
)
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.market_data import ZerodhaMarketDataProvider
from app.brokers.zerodha.ticker import KiteTickerClient, TickMode
from app.core.constants import ExecutionMode
from app.core.logging import get_logger

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


@dataclass
class ZerodhaRuntime:
    settings: Any
    client: KiteClient | None
    auth: KiteAuthService
    instruments: InstrumentService | None
    resolver: InstrumentResolver
    historical: ZerodhaHistoricalDataProvider | None
    credential_listeners: list[Callable[[str | None, str | None], Any]] = field(
        default_factory=list
    )

    owner_user_id: Any = None  # whose saved credentials drive the client (None: environment)
    credentials_source: str = "NONE"  # NONE | ENVIRONMENT | USER

    @property
    def configured(self) -> bool:
        return self.auth.configured

    async def apply_credentials(
        self, api_key: str | None, api_secret: str | None, *, owner_user_id: Any = None
    ) -> None:
        """Use these Kite Connect credentials from now on (None: remove them)."""

        await self.auth.set_credentials(api_key, api_secret)
        self.owner_user_id = owner_user_id if api_key and api_secret else None
        if api_key and api_secret:
            self.credentials_source = "USER" if owner_user_id is not None else "ENVIRONMENT"
        else:
            self.credentials_source = "NONE"
        for listener in list(self.credential_listeners):
            try:
                await listener(api_key, api_secret)
            except Exception:  # noqa: BLE001 - one listener must not block the others
                logger.exception("Zerodha credential listener failed")

    def market_data_provider(
        self, *, ticker_factory: Callable[[], KiteTickerClient] | None = None
    ) -> ZerodhaMarketDataProvider:
        if self.client is None or self.historical is None or not self.configured:
            raise RuntimeError("Zerodha is not configured")
        s = self.settings
        return ZerodhaMarketDataProvider(
            auth=self.auth,
            client=self.client,
            resolver=self.resolver,
            historical=self.historical,
            instrument_service=self.instruments,
            api_key=s.zerodha_api_key,
            ws_url=s.kite_ws_url,
            tick_mode=TickMode(s.kite_tick_mode),
            stale_tick_seconds=max(30.0, 3 * s.data_stale_threshold_seconds),
            max_move_percent=s.tick_max_move_percent,
            ticker_factory=ticker_factory,
        )

    async def start(self) -> None:
        if self.configured:
            try:
                # the last synced instrument master, before any login
                await self.resolver.reload()
            except Exception:  # noqa: BLE001 - no DB yet: loaded again at login
                logger.warning("Instrument master not loaded from the database")
            await self.auth.start()  # validates KITE_ACCESS_TOKEN, if one is set

    async def stop(self) -> None:
        await self.auth.stop()
        if self.client is not None:
            await self.client.aclose()

    def safe_status(self, *, market_data: str, execution_mode: str) -> dict[str, Any]:
        """What the frontend may see: no token, secret or request token."""

        a = self.auth.status()
        return {
            "broker": "ZERODHA",
            "configured": a["configured"],
            "connected": bool(a["authenticated"]),
            "auth": a["state"],
            "user_id": a["user_id"],
            "user_name": a["user_name"],
            "login_time": a["login_time"],
            "expires_at": a["expires_at"],
            "last_error": a["last_error"],
            "redirect_url_configured": a["redirect_url_configured"],
            "credentials_source": self.credentials_source,
            "market_data": market_data,
            "execution_mode": execution_mode,
        }


def build_zerodha_runtime(
    settings: Any,
    *,
    session_factory: SessionFactory | None = None,
    transport: Any = None,
) -> ZerodhaRuntime:
    """The runtime starts with the environment's credentials (operator
    override), if any; credentials a user saved in the app are applied later
    by BrokerCredentialService. The Kite client always exists so credentials
    can be added without a restart; without them it cannot authenticate."""

    secret = settings.zerodha_api_secret
    token = settings.zerodha_access_token
    api_key = settings.zerodha_api_key or None
    api_secret = secret.get_secret_value() if secret else None
    access_token = token.get_secret_value() if token else None
    env_configured = bool(api_key and api_secret)
    client = KiteClient(
        api_key=api_key or "",
        access_token=None,  # KiteAuthService sets and validates it
        base_url=settings.zerodha_api_base_url,
        timeout=settings.zerodha_timeout_seconds,
        orders_per_second=settings.zerodha_orders_per_second,
        transport=transport,
        orders_allowed=settings.effective_execution_mode == ExecutionMode.ZERODHA,
    )
    auth = KiteAuthService(
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token if env_configured else None,
        login_url=settings.zerodha_login_url,
        redirect_url=settings.kite_redirect_url,
        client=client,
    )
    repository = InstrumentRepository(session_factory) if session_factory else None
    resolver = InstrumentResolver(repository=repository)
    instruments = (
        InstrumentService(
            client, repository, resolver, exchanges=list(settings.instrument_exchanges)
        )
        if repository is not None
        else None
    )
    historical = ZerodhaHistoricalDataProvider(client, resolver)
    runtime = ZerodhaRuntime(
        settings=settings,
        client=client,
        auth=auth,
        instruments=instruments,
        resolver=resolver,
        historical=historical,
    )
    runtime.credentials_source = "ENVIRONMENT" if env_configured else "NONE"
    return runtime


__all__ = ["ZerodhaRuntime", "build_zerodha_runtime"]
