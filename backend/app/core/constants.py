"""Project-wide constants and enums that do not belong to a single domain
module yet. Domain-specific enums (SignalDirection, MarketRegime, order/
trade states, etc.) will be introduced in the phases that own them —
this file intentionally stays minimal in Phase 1.
"""

from enum import Enum


class TradingMode(str, Enum):
    """Supported trading modes.

    PAPER: fully simulated capital, orders, and fills. This is the
        mandatory default — see PROJECT_CONTRACT.md section 9.
    APPROVAL: system proposes trades; a human must approve execution.
    LIVE: real orders sent to a connected broker. Requires multiple
        simultaneous safety conditions before it may ever be used.
    """

    PAPER = "PAPER"
    APPROVAL = "APPROVAL"
    LIVE = "LIVE"


class SystemHealthStatus(str, Enum):
    """Overall system health values surfaced on /system/health and the
    frontend system status panel."""

    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"


class ComponentStatus(str, Enum):
    """Per-dependency status (database, redis, etc.) used inside the
    health payload."""

    CONNECTED = "CONNECTED"
    PENDING = "PENDING"
    DISCONNECTED = "DISCONNECTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class UserRole(str, Enum):
    """User roles and their permissions, per PROJECT_CONTRACT.md section 8.

    ADMIN: full access.
    TRADER: can configure trading and approve trades.
    VIEWER: read-only access.
    """

    ADMIN = "ADMIN"
    TRADER = "TRADER"
    VIEWER = "VIEWER"


class BrokerType(str, Enum):
    """Supported broker/account backends. Only PAPER is functional in
    this phase — MOCK and ZERODHA are structural placeholders for the
    market-data and broker-adapter phases."""

    PAPER = "PAPER"
    MOCK = "MOCK"
    ZERODHA = "ZERODHA"


API_V1_PREFIX = "/api/v1"
