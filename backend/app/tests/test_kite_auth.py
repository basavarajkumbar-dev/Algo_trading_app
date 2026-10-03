"""KiteAuthService: login URL with a one-time state, the request-token
callback, session validation, daily expiry, logout, and that no token or
secret ever leaks (repr, status, logs). Uses the fake Kite REST server."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.brokers.zerodha.auth import AuthState, KiteAuthService, KiteLoginError
from app.brokers.zerodha.exceptions import KiteError, KiteOrdersNotAllowedError
from app.brokers.zerodha.kite_client import KiteClient
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, REQUEST_TOKEN, FakeKite


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)  # 09:30 IST

    def __call__(self) -> datetime:
        return self.now


def make(fake: FakeKite, *, token: str | None = None, clock: Clock | None = None):
    client = KiteClient(api_key=API_KEY, access_token=None, transport=fake.transport())
    auth = KiteAuthService(
        api_key=API_KEY,
        api_secret=API_SECRET,
        access_token=token,
        redirect_url="http://localhost:8000/api/v1/broker/zerodha/callback",
        client=client,
        clock=clock or Clock(),
    )
    return auth, client


def _state(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    assert q["api_key"] == [API_KEY] and q["v"] == ["3"]
    return parse_qs(q["redirect_params"][0])["state"][0]


async def test_full_login_flow_and_listener(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeKite(token=None)
    auth, client = make(fake)
    seen: list[AuthState] = []

    async def listener(state: AuthState) -> None:
        seen.append(state)

    auth.add_listener(listener)
    assert auth.state is AuthState.LOGIN_REQUIRED and not auth.authenticated
    state = _state(auth.get_login_url())
    session = await auth.handle_request_token(REQUEST_TOKEN, status="success", state=state)
    assert auth.state is AuthState.AUTHENTICATED and auth.authenticated
    assert seen == [AuthState.AUTHENTICATED]
    assert session.user_id is not None and "AB1234" not in session.user_id  # masked
    # expires at 06:00 IST the next day
    assert session.expires_at == datetime(2026, 10, 2, 0, 30, tzinfo=UTC)
    assert client.has_token
    profile = await auth.get_profile()
    assert profile["broker"] == "ZERODHA" and profile["user_id"] != "AB1234"
    text = repr(auth) + str(auth.status()) + str(profile) + caplog.text
    for secret in (ACCESS_TOKEN, API_SECRET, REQUEST_TOKEN):
        assert secret not in text
    assert "BROKER_AUTH_SUCCESS" in caplog.text


async def test_callback_state_is_single_use_and_must_be_ours() -> None:
    fake = FakeKite(token=None)
    auth, _ = make(fake)
    with pytest.raises(KiteLoginError):
        await auth.handle_request_token(REQUEST_TOKEN, status="success", state="forged")
    state = _state(auth.get_login_url())
    await auth.handle_request_token(REQUEST_TOKEN, status="success", state=state)
    with pytest.raises(KiteLoginError):  # replay
        await auth.handle_request_token(REQUEST_TOKEN, status="success", state=state)


async def test_state_expires_and_failed_logins_are_auth_errors() -> None:
    clock = Clock()
    fake = FakeKite(token=None)
    auth, _ = make(fake, clock=clock)
    state = _state(auth.get_login_url())
    clock.now += timedelta(minutes=11)
    with pytest.raises(KiteLoginError):
        await auth.handle_request_token(REQUEST_TOKEN, status="success", state=state)
    state = _state(auth.get_login_url())
    with pytest.raises(KiteLoginError):
        await auth.handle_request_token(None, status="cancelled", state=state)
    assert auth.state is AuthState.AUTH_ERROR
    state = _state(auth.get_login_url())
    with pytest.raises(KiteError):  # Kite refuses a bad request token
        await auth.handle_request_token("wrong-token", status="success", state=state)
    assert auth.state is AuthState.AUTH_ERROR and not auth.authenticated


async def test_env_token_is_validated_at_start() -> None:
    fake = FakeKite()
    auth, _ = make(fake, token=ACCESS_TOKEN)
    assert await auth.start() is AuthState.AUTHENTICATED
    bad = FakeKite(token="something-else")
    auth2, client2 = make(bad, token=ACCESS_TOKEN)
    assert await auth2.start() is AuthState.TOKEN_EXPIRED
    assert not client2.has_token  # a rejected token is dropped


async def test_daily_expiry_and_403_mark_the_session_expired() -> None:
    clock = Clock()
    fake = FakeKite(token=None)
    auth, client = make(fake, clock=clock)
    await auth.handle_request_token(
        REQUEST_TOKEN, status="success", state=_state(auth.get_login_url())
    )
    clock.now = datetime(2026, 10, 2, 0, 31, tzinfo=UTC)  # 06:01 IST next day
    assert not auth.authenticated
    assert await auth.validate_session() is AuthState.TOKEN_EXPIRED
    # a token Kite rejects mid-session
    clock.now = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
    fake2 = FakeKite(token=None)
    auth2, client2 = make(fake2, clock=clock)
    await auth2.handle_request_token(
        REQUEST_TOKEN, status="success", state=_state(auth2.get_login_url())
    )
    fake2.valid_token = "rotated"
    assert await auth2.validate_session() is AuthState.TOKEN_EXPIRED
    assert not client2.has_token


async def test_logout_invalidates_and_forgets_the_token() -> None:
    fake = FakeKite(token=None)
    auth, client = make(fake)
    await auth.handle_request_token(
        REQUEST_TOKEN, status="success", state=_state(auth.get_login_url())
    )
    await auth.logout()
    assert auth.state is AuthState.LOGGED_OUT and not client.has_token
    assert any(m == "DELETE" and p == "/session/token" for m, p, _, _ in fake.requests)


async def test_the_shared_client_refuses_orders_unless_live_is_enabled() -> None:
    fake = FakeKite()
    client = KiteClient(api_key=API_KEY, access_token=ACCESS_TOKEN, transport=fake.transport())
    with pytest.raises(KiteOrdersNotAllowedError):
        await client.place_order("regular", {"tradingsymbol": "INFY"})
    assert fake.posts() == []  # nothing reached Kite


def test_not_configured_is_inert() -> None:
    auth = KiteAuthService(api_key=None, api_secret=None)
    assert auth.state is AuthState.NOT_CONFIGURED and not auth.configured
    with pytest.raises(KiteLoginError):
        auth.get_login_url()
