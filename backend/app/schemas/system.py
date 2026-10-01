"""Response schemas for the /system endpoints.

Phase 1 only implements /system/health. The richer SystemHealthEngine
described in PROJECT_CONTRACT.md (monitoring the strategy engine, risk
engine, broker, workers, etc.) is out of scope until the phases that
introduce those subsystems exist.
"""

from pydantic import BaseModel, Field

from app.core.constants import ComponentStatus, SystemHealthStatus, TradingMode


class ComponentHealth(BaseModel):
    """Health of a single infrastructure dependency."""

    name: str
    status: ComponentStatus
    detail: str | None = Field(
        default=None,
        description="Human-readable detail, e.g. a connection error message. "
        "Never contains secrets or credentials.",
    )


class SystemHealthResponse(BaseModel):
    """Payload returned by GET /api/v1/system/health."""

    status: SystemHealthStatus
    app_name: str
    app_env: str
    demo_mode: bool
    trading_mode: TradingMode
    live_trading_active: bool = Field(
        description="Always false until a future phase implements live "
        "execution. Present now so the frontend/API shape is stable."
    )
    components: list[ComponentHealth]
    notes: list[str] = Field(
        default_factory=list,
        description="Non-alarming informational notes, e.g. reminders that "
        "market data is simulated, not live.",
    )
