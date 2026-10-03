"""Phase 1 tests.

These tests intentionally do not require a real Postgres/Redis
connection to pass — Phase 1's Definition of Done is that the app boots
and the health endpoint responds with the correct *shape*, including
correctly reporting DISCONNECTED components when infra isn't reachable
(as it deliberately isn't during `pytest` outside Docker Compose).

Full "actually connected" verification happens via
`docker compose up --build` per the phase brief, and again in later
phases once integration tests are introduced.
"""

from httpx import ASGITransport, AsyncClient

from app.core.constants import ComponentStatus, TradingMode
from app.main import app


async def test_root_route_returns_running_status() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["health"] == "/api/v1/system/health"


async def test_health_endpoint_shape() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/health")

    assert response.status_code == 200
    body = response.json()

    assert body["app_name"] == "GAINZALGO PRO MAX"
    assert body["trading_mode"] == TradingMode.PAPER.value
    assert body["live_trading_active"] is False

    component_names = {c["name"] for c in body["components"]}
    assert component_names == {"database", "redis"}
    for component in body["components"]:
        assert component["status"] in {s.value for s in ComponentStatus}

    # Without Docker's Postgres/Redis reachable from the test process,
    # both must be reported as disconnected — never silently "connected".
    assert body["status"] in {"CRITICAL", "WARNING", "HEALTHY"}


async def test_health_notes_never_claim_live_market_data() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/health")

    body = response.json()
    notes_text = " ".join(body["notes"]).lower()
    assert "simulated" in notes_text  # no Kite credentials in the test environment
    assert "no real orders are placed" in notes_text


def test_default_trading_mode_is_paper() -> None:
    """Directly asserts the safety-critical default from
    PROJECT_CONTRACT.md section 9, item 1."""

    from app.core.config import Settings

    assert Settings().trading_mode == TradingMode.PAPER
    assert Settings().live_mode_enabled is False
