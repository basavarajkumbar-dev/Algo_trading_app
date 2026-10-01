"""BrokerMarketDataProvider.

An abstract extension of `MarketDataProvider` for real broker-backed
market data (e.g. Zerodha, added in docs/development-roadmap.md
Phase 16). This phase defines the interface only — there is
intentionally no concrete, connectable implementation yet, and none is
required for the app to run: `PaperMarketDataProvider` /
`MockMarketDataProvider` cover every current use.

Per PROJECT_CONTRACT.md's broker architecture requirement, credentials
are read only from environment variables and a missing/invalid
credential must never crash the app — `credentials_configured` lets
callers check that before attempting to `connect()`.
"""

from __future__ import annotations

from abc import abstractmethod

from app.market_data.base import MarketDataProvider


class BrokerMarketDataProviderError(Exception):
    """Raised when a broker-backed provider is used without being
    configured/connected."""


class BrokerMarketDataProvider(MarketDataProvider):
    """Base class for real broker market data integrations.

    Subclasses (e.g. a future `ZerodhaMarketDataProvider`) must still
    implement every `MarketDataProvider` method, plus declare whether
    their credentials are present via `credentials_configured`.
    """

    @property
    @abstractmethod
    def credentials_configured(self) -> bool:
        """Whether this provider has everything it needs (from
        environment variables) to attempt a real connection.

        Callers (e.g. a provider factory) should check this BEFORE
        calling `connect()` and fall back to
        `PaperMarketDataProvider`/`MockMarketDataProvider` if False,
        rather than letting `connect()` fail loudly at startup.
        """

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """Human-readable broker name, for system-health reporting."""
