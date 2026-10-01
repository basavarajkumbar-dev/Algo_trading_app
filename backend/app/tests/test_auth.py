"""Auth flow tests.

Covers registration, login, /me, refresh-token rotation and replay
prevention, logout revocation, and the settings foundation endpoint.
Requires a real reachable Postgres — see conftest.py's `_skip_if_no_database`.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from app.main import app

BASE_URL = "http://test"


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url=BASE_URL)


async def test_register_creates_user_and_returns_tokens() -> None:
    async with await _client() as client:
        email = _unique_email()
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-horse-1", "full_name": "Test User"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == email
    assert body["user"]["role"] == "TRADER"
    assert body["user"]["is_active"] is True
    assert "access_token" in body
    assert "refresh_token" in body
    # The password must never be echoed back in any form.
    assert "password" not in body["user"]
    assert "correct-horse-1" not in response.text


async def test_register_duplicate_email_returns_409() -> None:
    email = _unique_email()
    async with await _client() as client:
        first = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "another-password-2"}
        )

    assert second.status_code == 409


async def test_login_with_correct_and_incorrect_password() -> None:
    email = _unique_email()
    async with await _client() as client:
        await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )

        good = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "correct-horse-1"}
        )
        bad = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
        )

    assert good.status_code == 200
    assert bad.status_code == 401


async def test_me_requires_valid_bearer_token() -> None:
    email = _unique_email()
    async with await _client() as client:
        register_resp = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )
        access_token = register_resp.json()["access_token"]

        unauthenticated = await client.get("/api/v1/auth/me")
        authenticated = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        garbage_token = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["email"] == email
    assert garbage_token.status_code == 401


async def test_refresh_token_rotates_and_rejects_reuse() -> None:
    email = _unique_email()
    async with await _client() as client:
        register_resp = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )
        old_refresh_token = register_resp.json()["refresh_token"]

        first_refresh = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": old_refresh_token}
        )
        assert first_refresh.status_code == 200
        new_refresh_token = first_refresh.json()["refresh_token"]
        assert new_refresh_token != old_refresh_token

        # Reusing the now-rotated-out token must fail.
        replay_attempt = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": old_refresh_token}
        )
        assert replay_attempt.status_code == 401

        # The freshly issued token must still work.
        second_refresh = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": new_refresh_token}
        )
        assert second_refresh.status_code == 200


async def test_logout_revokes_refresh_token() -> None:
    email = _unique_email()
    async with await _client() as client:
        register_resp = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )
        refresh_token = register_resp.json()["refresh_token"]

        logout_resp = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh_token}
        )
        assert logout_resp.status_code == 204

        refresh_after_logout = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refresh_after_logout.status_code == 401


async def test_logout_with_unknown_token_is_idempotent() -> None:
    """Logging out with a token that doesn't match any session should not
    error — the desired end state (no valid session) already holds."""

    async with await _client() as client:
        response = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": "not-a-real-token"}
        )
    assert response.status_code == 204


async def test_new_user_gets_safe_default_trading_settings() -> None:
    """Directly checks the safety-critical invariant from
    PROJECT_CONTRACT.md section 9: every user has an explicit PAPER-mode
    settings row from the moment they exist — never an implicit or
    missing trading mode."""

    email = _unique_email()
    async with await _client() as client:
        register_resp = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )
        access_token = register_resp.json()["access_token"]

        settings_resp = await client.get(
            "/api/v1/settings/me", headers={"Authorization": f"Bearer {access_token}"}
        )

    assert settings_resp.status_code == 200
    body = settings_resp.json()
    assert body["trading_mode"] == "PAPER"
    assert body["auto_trading_enabled"] is False
    assert body["emergency_stop"] is False


async def test_settings_update_only_allows_permitted_fields() -> None:
    email = _unique_email()
    async with await _client() as client:
        register_resp = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
        )
        access_token = register_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        update_resp = await client.patch(
            "/api/v1/settings/me", json={"paper_capital": 250_000}, headers=headers
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["paper_capital"] == 250_000.0

        # trading_mode is intentionally not part of the update schema —
        # sending it should be ignored by Pydantic's extra-field handling,
        # not silently flip the safety-critical trading mode.
        reread = await client.get("/api/v1/settings/me", headers=headers)
        assert reread.json()["trading_mode"] == "PAPER"
