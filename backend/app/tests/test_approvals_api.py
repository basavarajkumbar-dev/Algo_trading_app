"""Approval and mode endpoints (needs Postgres)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.approvals import repository as repo
from app.database.models.audit_log import AuditLog
from app.database.session import async_session_factory
from app.main import app
from app.market_data.base import utcnow


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
        assert c.portal is not None
        c.portal.call(app.state.trading_service.set_mode, "PAPER")


def _user(client: TestClient, role: str = "TRADER") -> dict[str, str]:
    email = f"appr-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 201, r.text
    if role != "TRADER":

        async def promote() -> None:
            async with async_session_factory() as s:
                await s.execute(
                    text("UPDATE users SET role=:r WHERE email=:e"), {"r": role, "e": email}
                )
                await s.commit()

        assert client.portal is not None
        client.portal.call(promote)
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "correct-horse-1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _request(client: TestClient, *, expires_in: float = 60) -> str:
    """A PENDING request as the algo would store it (no live signal needed)."""

    async def create() -> str:
        now = utcnow()
        async with async_session_factory() as s:
            a = await repo.create(
                s,
                created_at=now,
                source="ALGO",
                signal_id=None,
                symbol="RELIANCE",
                direction="BUY",
                side="LONG",
                entry=1400.0,
                stop_loss=1390.0,
                target=1420.0,
                quantity=50,
                risk_amount=500.0,
                risk_per_share=10.0,
                position_value=70000.0,
                reward_risk=2.0,
                strategy="TREND_CONTINUATION",
                strategy_version="1.0.0+567cf21b",
                score=91.0,
                category="STRONG",
                market_regime="STRONG_BULLISH",
                regime_confidence=72.0,
                reasons=["trend aligned on 60m / 15m / 5m"],
                risk_checks=[],
                signal_timestamp=now,
                expires_at=now + timedelta(seconds=expires_in),
            )
        return str(a.id)

    assert client.portal is not None
    return client.portal.call(create)


def _audits(client: TestClient) -> list[Any]:
    async def rows() -> list[Any]:
        async with async_session_factory() as s:
            return list((await s.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars())

    assert client.portal is not None
    return client.portal.call(rows)


def test_mode_switch_is_audited_and_live_is_refused(client: TestClient) -> None:
    trader = _user(client)
    assert client.post("/api/v1/trading/mode", json={"mode": "APPROVAL"}).status_code == 401
    assert (
        client.post("/api/v1/trading/mode", json={"mode": "LIVE"}, headers=trader).status_code
        == 422
    )
    r = client.post("/api/v1/trading/mode", json={"mode": "APPROVAL"}, headers=trader)
    assert r.status_code == 200 and r.json()["mode"] == "APPROVAL"
    assert client.get("/api/v1/trading/status").json()["mode"] == "APPROVAL"
    listing = client.get("/api/v1/approvals").json()
    assert (
        listing["mode"] == "APPROVAL" and listing["ttl_seconds"] == 120 and listing["items"] == []
    )
    audit = [a for a in _audits(client) if a.action == "TRADING_MODE_CHANGED"][-1]
    assert audit.before == {"mode": "PAPER"} and audit.after["mode"] == "APPROVAL"


def test_reject_needs_a_reason_and_a_trader_and_is_stored(client: TestClient) -> None:
    trader, viewer = _user(client), _user(client, "VIEWER")
    aid = _request(client)
    one = client.get(f"/api/v1/approvals/{aid}").json()
    assert one["status"] == "PENDING" and 0 < one["seconds_remaining"] <= 60
    for field in (
        "symbol",
        "direction",
        "entry",
        "stop_loss",
        "target",
        "quantity",
        "risk_amount",
        "reward_risk",
        "strategy",
        "score",
        "market_regime",
        "reasons",
        "created_at",
    ):
        assert one[field] not in (None, ""), field
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/reject", json={"reason": "no"}, headers=viewer
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/reject", json={"reason": ""}, headers=trader
        ).status_code
        == 422
    )
    r = client.post(
        f"/api/v1/approvals/{aid}/reject", json={"reason": "news pending"}, headers=trader
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "REJECTED" and body["reason"] == "news pending"
    assert body["rejected_by_email"].startswith("appr-") and body["rejected_at"]
    assert client.post(f"/api/v1/approvals/{aid}/approve", headers=trader).status_code == 409
    assert [a.action for a in _audits(client) if a.action.startswith("APPROVAL_")] == [
        "APPROVAL_REJECTED"
    ]
    counts = client.get("/api/v1/approvals").json()["counts"]
    assert counts == {"REJECTED": 1}


def test_an_expired_request_cannot_be_approved_over_the_api(client: TestClient) -> None:
    trader = _user(client)
    client.post("/api/v1/trading/mode", json={"mode": "APPROVAL"}, headers=trader)
    aid = _request(client, expires_in=-1)
    r = client.post(f"/api/v1/approvals/{aid}/approve", headers=trader)
    assert r.status_code == 409 and "expired" in r.text
    assert client.get(f"/api/v1/approvals/{aid}").json()["status"] == "EXPIRED"
    assert client.get("/api/v1/approvals", params={"status": "EXPIRED"}).json()["total"] == 1
    assert client.get("/api/v1/approvals", params={"status": "PENDING"}).json()["total"] == 0


def test_proposals_need_approval_mode_and_a_known_signal(client: TestClient) -> None:
    trader = _user(client)
    body = {"signal_id": str(uuid.uuid4())}
    assert client.post("/api/v1/approvals", json=body).status_code == 401
    paper = client.post("/api/v1/approvals", json=body, headers=trader)
    assert paper.status_code == 409 and "not APPROVAL" in paper.text
    client.post("/api/v1/trading/mode", json={"mode": "APPROVAL"}, headers=trader)
    assert client.post("/api/v1/approvals", json=body, headers=trader).status_code == 404
    assert client.get(f"/api/v1/approvals/{uuid.uuid4()}").status_code == 404
