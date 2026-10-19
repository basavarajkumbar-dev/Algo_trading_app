"""KiteAuthService: the backend-only Kite Connect login (official flow).

    1. get_login_url()      https://kite.zerodha.com/connect/login?v=3&api_key=...
                            with redirect_params carrying a one-time `state`
    2. the user logs in at Zerodha; Kite redirects the browser to
       KITE_REDIRECT_URL (= GET /api/v1/broker/zerodha/callback) with
       request_token, status=success and our state
    3. handle_request_token()  checks status and the state (single use,
                            10 minutes), then
    4. generate_access_token() POST /session/token with
                            checksum = sha256(api_key + request_token + api_secret)
    5. the access token is kept in memory only (on the shared KiteClient).
       It expires at 06:00 IST the next day: then the state becomes
       TOKEN_EXPIRED and a new login is needed. Nothing persists it: no
       database, no Redis, no file, no browser storage, no log.

KITE_ACCESS_TOKEN may provide a token obtained elsewhere for the current
day; it is validated with GET /user/profile at startup.

States: NOT_CONFIGURED, LOGIN_REQUIRED, AUTHENTICATING, AUTHENTICATED,
TOKEN_EXPIRED, AUTH_ERROR, LOGGED_OUT.

Listeners (market-data provider, instrument sync, broker adapter) are told
about AUTHENTICATED / TOKEN_EXPIRED / LOGGED_OUT so the WebSocket connects
after a login and stops when the session ends.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from app.brokers.zerodha import mapping
from app.brokers.zerodha.exceptions import (
    KiteAuthError,
    KiteError,
    KiteNetworkError,
)
from app.brokers.zerodha.kite_client import KiteClient
from app.core.logging import get_logger
from app.market_data.base import utcnow

logger = get_logger(__name__)

STATE_TTL = timedelta(minutes=10)


class AuthState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    AUTH_ERROR = "AUTH_ERROR"
    LOGGED_OUT = "LOGGED_OUT"


class KiteSessionInfo(BaseModel):
    """What may be shown about a session. Never the token."""

    user_id: str | None  # masked
    user_name: str | None
    broker: str = "ZERODHA"
    exchanges: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    login_time: datetime | None
    expires_at: datetime | None


class KiteLoginError(KiteError):
    """The login callback was not acceptable (status, state, token)."""


AuthListener = Callable[[AuthState], Awaitable[None]]


class KiteAuthService:
    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        access_token: str | None = None,
        login_url: str = "https://kite.zerodha.com/connect/login",
        redirect_url: str | None = None,
        client: KiteClient | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._api_key = api_key or None
        self._api_secret = api_secret or None
        self._login_url = login_url
        self.redirect_url = redirect_url
        self._clock = clock
        self.client = client
        if client is not None:
            client.on_auth_error = self._on_auth_error
            if access_token:
                client.set_access_token(access_token)
        self._env_token = bool(access_token)
        self.state = AuthState.NOT_CONFIGURED if not self.configured else AuthState.LOGIN_REQUIRED
        self.session: KiteSessionInfo | None = None
        self.last_error: str | None = None
        self.last_validated_at: datetime | None = None
        self._states: dict[str, datetime] = {}
        self._listeners: list[AuthListener] = []
        self._expiry_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"KiteAuthService(configured={self.configured}, state={self.state.value})"

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret and self.client is not None)

    @property
    def api_key(self) -> str | None:
        return self._api_key

    def credentials(self) -> tuple[str | None, str | None]:
        """For the broker adapter sharing this client only. Never logged."""

        return self._api_key, self._api_secret

    async def set_credentials(self, api_key: str | None, api_secret: str | None) -> None:
        """Replace (or with None, remove) the Kite Connect app credentials.

        Any current session ends: an access token belongs to the API key that
        created it. Pending login states are discarded. The secret is held in
        memory only for the checksum of the token exchange."""

        had_session = self.client is not None and self.client.has_token
        self._api_key = api_key or None
        self._api_secret = api_secret or None
        self._states.clear()
        if self.client is not None:
            self.client.set_api_key(self._api_key or "")
            self.client.set_access_token(None)
        self.session = None
        if self._expiry_task is not None:
            self._expiry_task.cancel()
            self._expiry_task = None
        self.last_error = None
        self.state = AuthState.LOGIN_REQUIRED if self.configured else AuthState.NOT_CONFIGURED
        if had_session:
            await self._notify(AuthState.LOGGED_OUT)

    async def restore_session(self, access_token: str, expires_at: datetime | None) -> AuthState:
        """A token saved (encrypted) after an earlier login today: use it if it
        has not expired and Kite still accepts it."""

        if not self.configured or self.client is None:
            return self.state
        if expires_at is not None and self._clock() >= expires_at:
            self.state = AuthState.TOKEN_EXPIRED
            self.last_error = "the saved Kite session expired (06:00 IST)"
            return self.state
        self.client.set_access_token(access_token)
        self.session = None
        state = await self.validate_session()
        if self.session is not None and expires_at is not None:
            self.session.expires_at = expires_at
            self._schedule_expiry()
        return state

    def cancel_pending_login(self) -> None:
        """Forget issued login states: a late callback is then refused."""

        self._states.clear()
        if self.state in (AuthState.AUTHENTICATING, AuthState.AUTH_ERROR):
            self.state = AuthState.LOGIN_REQUIRED if self.configured else AuthState.NOT_CONFIGURED

    def has_pending_login(self) -> bool:
        now = self._clock()
        return any(v > now for v in self._states.values())

    def current_access_token(self) -> str | None:
        """For encrypting the token at rest only. Never logged or returned."""

        return self.client.access_token_for_ticker() if self.client is not None else None

    @property
    def authenticated(self) -> bool:
        return self.state is AuthState.AUTHENTICATED and not self._expired()

    def add_listener(self, listener: AuthListener) -> None:
        self._listeners.append(listener)

    async def _notify(self, state: AuthState) -> None:
        for listener in list(self._listeners):
            try:
                await listener(state)
            except Exception:  # noqa: BLE001 - one listener must not break the others
                logger.exception("Kite auth listener failed on %s", state.value)

    def _expired(self) -> bool:
        return (
            self.session is not None
            and self.session.expires_at is not None
            and self._clock() >= self.session.expires_at
        )

    # --- lifecycle ------------------------------------------------------------

    async def start(self) -> AuthState:
        """Validate a KITE_ACCESS_TOKEN from the environment, if any."""

        if not self.configured:
            return self.state
        if self._env_token:
            await self.validate_session()
        return self.state

    async def stop(self) -> None:
        if self._expiry_task is not None:
            self._expiry_task.cancel()
            self._expiry_task = None

    def _schedule_expiry(self) -> None:
        if self._expiry_task is not None:
            self._expiry_task.cancel()
        expires = self.session.expires_at if self.session else None
        if expires is None:
            return

        async def expire() -> None:
            delay = (expires - self._clock()).total_seconds()
            await asyncio.sleep(max(delay, 0))
            await self.mark_expired("the Kite session expired (06:00 IST)")

        self._expiry_task = asyncio.create_task(expire(), name="kite-session-expiry")

    # --- login flow ---------------------------------------------------------------

    def get_login_url(self) -> str:
        """Kite login URL with a one-time `state` for the callback."""

        if not self.configured:
            raise KiteLoginError("Kite API key/secret are not configured")
        now = self._clock()
        self._states = {k: v for k, v in self._states.items() if v > now}
        nonce = secrets.token_urlsafe(24)
        self._states[nonce] = now + STATE_TTL
        query = {"v": "3", "api_key": self._api_key, "redirect_params": urlencode({"state": nonce})}
        return f"{self._login_url}?{urlencode(query)}"

    def consume_state(self, nonce: str | None) -> bool:
        if not nonce:
            return False
        expires = self._states.pop(nonce, None)
        return expires is not None and expires > self._clock()

    async def handle_request_token(
        self, request_token: str | None, *, status: str | None, state: str | None
    ) -> KiteSessionInfo:
        """The Kite redirect. The state must be one this server issued."""

        if not self.consume_state(state):
            self.last_error = "login callback with an unknown or expired state"
            logger.warning("BROKER_AUTH_FAILURE %s", self.last_error)
            raise KiteLoginError("Login link expired or not issued by this server; start again.")
        if status != "success" or not request_token:
            self.state = AuthState.AUTH_ERROR
            self.last_error = f"login status {status!r}"
            logger.warning("BROKER_AUTH_FAILURE %s", self.last_error)
            raise KiteLoginError("Zerodha did not complete the login.")
        return await self.generate_access_token(request_token)

    async def generate_access_token(self, request_token: str) -> KiteSessionInfo:
        if not self.configured:
            raise KiteLoginError("Kite API key/secret are not configured")
        assert self.client is not None and self._api_key and self._api_secret
        async with self._lock:
            self.state = AuthState.AUTHENTICATING
            checksum = mapping.session_checksum(self._api_key, request_token, self._api_secret)
            try:
                data = await self.client.create_session(request_token, checksum)
            except KiteError as exc:
                self.state = AuthState.AUTH_ERROR
                self.last_error = f"token exchange failed: {type(exc).__name__}"
                logger.warning("BROKER_AUTH_FAILURE %s", self.last_error)
                raise
            token = data.get("access_token")
            if not token:
                self.state = AuthState.AUTH_ERROR
                self.last_error = "Kite returned no access token"
                raise KiteLoginError(self.last_error)
            self.client.set_access_token(str(token))
            login = mapping._dt(data.get("login_time")) or self._clock()
            self.session = KiteSessionInfo(
                user_id=mapping.mask(data.get("user_id")),
                user_name=data.get("user_shortname") or data.get("user_name"),
                exchanges=list(data.get("exchanges") or []),
                products=list(data.get("products") or []),
                login_time=login,
                expires_at=mapping.session_expiry(login),
            )
            self.state = AuthState.AUTHENTICATED
            self.last_error = None
            self.last_validated_at = self._clock()
            self._schedule_expiry()
        logger.info(
            "BROKER_AUTH_SUCCESS user=%s expires=%s", self.session.user_id, self.session.expires_at
        )
        await self._notify(AuthState.AUTHENTICATED)
        return self.session

    async def validate_session(self) -> AuthState:
        """Check the current token with GET /user/profile."""

        if not self.configured:
            return self.state
        assert self.client is not None
        if not self.client.has_token:
            if self.state not in (
                AuthState.LOGGED_OUT,
                AuthState.TOKEN_EXPIRED,
                AuthState.AUTH_ERROR,
            ):
                self.state = AuthState.LOGIN_REQUIRED
            return self.state
        if self._expired():
            await self.mark_expired("the Kite session expired (06:00 IST)")
            return self.state
        try:
            data = await self.client.profile()
        except KiteAuthError:
            await self.mark_expired("Kite rejected the access token")
            return self.state
        except KiteNetworkError as exc:
            self.last_error = f"session check failed: {exc}"
            return self.state  # unknown: keep the current state
        first = self.state is not AuthState.AUTHENTICATED
        now = self._clock()
        if self.session is None:
            self.session = KiteSessionInfo(
                user_id=mapping.mask(data.get("user_id")),
                user_name=data.get("user_shortname") or data.get("user_name"),
                exchanges=list(data.get("exchanges") or []),
                products=list(data.get("products") or []),
                login_time=None,
                expires_at=mapping.session_expiry(now),
            )
            self._schedule_expiry()
        self.state = AuthState.AUTHENTICATED
        self.last_error = None
        self.last_validated_at = now
        if first:
            logger.info("BROKER_AUTH_SUCCESS (existing token) user=%s", self.session.user_id)
            await self._notify(AuthState.AUTHENTICATED)
        return self.state

    async def get_profile(self) -> dict[str, Any]:
        if not self.configured or self.client is None or not self.client.has_token:
            raise KiteAuthError("not logged in to Kite")
        data = await self.client.profile()
        return {
            "user_id": mapping.mask(data.get("user_id")),
            "user_name": data.get("user_shortname") or data.get("user_name"),
            "broker": "ZERODHA",
            "exchanges": list(data.get("exchanges") or []),
            "products": list(data.get("products") or []),
            "order_types": list(data.get("order_types") or []),
        }

    async def logout(self) -> None:
        """Invalidate the session at Kite (best effort) and forget the token."""

        if self.client is not None and self.client.has_token:
            try:
                await self.client.invalidate_session()
            except KiteError as exc:
                logger.warning(
                    "Kite logout call failed (%s); token dropped locally", type(exc).__name__
                )
            self.client.set_access_token(None)
        self.session = None
        if self._expiry_task is not None:
            self._expiry_task.cancel()
            self._expiry_task = None
        self.state = AuthState.LOGGED_OUT if self.configured else AuthState.NOT_CONFIGURED
        logger.info("BROKER_LOGOUT")
        await self._notify(AuthState.LOGGED_OUT)

    async def mark_expired(self, reason: str) -> None:
        if self.state is AuthState.TOKEN_EXPIRED:
            return
        if self.client is not None:
            self.client.set_access_token(None)
        self.state = AuthState.TOKEN_EXPIRED
        self.last_error = reason
        logger.warning("BROKER_AUTH_FAILURE %s; a new Kite login is needed", reason)
        await self._notify(AuthState.TOKEN_EXPIRED)

    def _on_auth_error(self, message: str) -> None:
        """A REST call got 403 / TokenException."""

        if self.state is AuthState.AUTHENTICATED:
            try:
                asyncio.get_running_loop().create_task(
                    self.mark_expired("Kite rejected the access token")
                )
            except RuntimeError:  # no loop (sync context): mark directly
                self.state = AuthState.TOKEN_EXPIRED

    # --- status -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        s = self.session
        return {
            "configured": self.configured,
            "state": self.state.value,
            "authenticated": self.authenticated,
            "user_id": s.user_id if s else None,
            "user_name": s.user_name if s else None,
            "login_time": s.login_time if s else None,
            "expires_at": s.expires_at if s else None,
            "last_validated_at": self.last_validated_at,
            "last_error": self.last_error,
            "redirect_url_configured": bool(self.redirect_url),
        }
