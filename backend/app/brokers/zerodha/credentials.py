"""User-entered Zerodha Kite Connect credentials.

A user types their own API key and API secret into the app. They reach this
service over HTTPS through an authenticated endpoint and are:

    validated      format only: Zerodha itself checks the API key when the
                   user logs in and the API secret during the token exchange
                   (there is no official call that validates a secret alone)
    encrypted      AES-256-GCM, bound to the user (app.security.credential_vault);
                   the key is outside the database. No plaintext fallback.
    stored         one row per user (broker_credentials); readable by nobody
                   through the API: responses carry a masked hint only
    applied        to the server's single Kite connection (ZerodhaRuntime).
                   One Zerodha account drives the server at a time: the
                   "active" user's. Another user's credentials are kept and can
                   take over once the active account is not connected.

The official login then runs as before (KiteAuthService): login URL with a
one-time state -> Zerodha login -> backend callback -> checksum token
exchange. The resulting access token is encrypted into the same row so a
restart within the day does not need a new login; it expires at 06:00 IST and
is cleared on expiry, disconnect and removal.

Credentials are never re-entered daily: only the Zerodha login is repeated.
Nothing here can enable live trading; that still needs EXECUTION_MODE,
LIVE_TRADING_ENABLED, an admin's arming and every pre-trade check.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.zerodha.auth import AuthState
from app.brokers.zerodha.exceptions import KiteError
from app.brokers.zerodha.runtime import ZerodhaRuntime
from app.core.logging import get_logger
from app.database.models.broker_credential import BrokerCredential
from app.market_data.base import utcnow
from app.security.credential_vault import CredentialVault, VaultError

logger = get_logger(__name__)

BROKER = "ZERODHA"
API_KEY_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
API_SECRET_RE = re.compile(r"^[A-Za-z0-9]{12,128}$")

SessionFactory = Callable[[], AsyncSession]


class ConnectionStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CREDENTIALS_SAVED = "CREDENTIALS_SAVED"
    NOT_CONNECTED = "NOT_CONNECTED"
    AWAITING_LOGIN = "AWAITING_LOGIN"
    CONNECTED = "CONNECTED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CONNECTION_FAILED = "CONNECTION_FAILED"


LABELS = {
    ConnectionStatus.NOT_CONFIGURED: "Not Configured",
    ConnectionStatus.CREDENTIALS_SAVED: "Credentials Saved",
    ConnectionStatus.NOT_CONNECTED: "Not Connected",
    ConnectionStatus.AWAITING_LOGIN: "Awaiting Zerodha Login",
    ConnectionStatus.CONNECTED: "Connected",
    ConnectionStatus.SESSION_EXPIRED: "Session Expired",
    ConnectionStatus.CONNECTION_FAILED: "Connection Failed",
}


class CredentialError(Exception):
    """A request that cannot be honoured; `status_code` is the HTTP answer.
    Messages never contain a credential."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def validate_credentials(api_key: Any, api_secret: Any) -> tuple[str, str]:
    """Format checks with messages that never echo the values."""

    if not isinstance(api_key, str) or not api_key.strip():
        raise CredentialError(422, "Enter your API key.")
    if not isinstance(api_secret, str) or not api_secret.strip():
        raise CredentialError(422, "Enter your API secret.")
    key, secret = api_key.strip(), api_secret.strip()
    if not API_KEY_RE.fullmatch(key):
        raise CredentialError(422, "The API key should be 6-64 letters or digits, no spaces.")
    if not API_SECRET_RE.fullmatch(secret):
        raise CredentialError(422, "The API secret should be 12-128 letters or digits, no spaces.")
    if key == secret:
        raise CredentialError(422, "The API key and the API secret must be different.")
    return key, secret


def _explain(error: str | None) -> str:
    """A plain-language reason (the raw text never contains a credential)."""

    if not error:
        return "Zerodha did not complete the connection."
    if "token exchange" in error:
        return (
            "Zerodha rejected the token exchange. Check that the API secret belongs to "
            "this API key, then connect again."
        )
    if "login status" in error:
        return "The Zerodha login was cancelled or not authorised. Connect again to retry."
    if "state" in error:
        return "The login link expired or was not started here. Connect again."
    return error


def _hint(api_key: str) -> str:
    return "••••" + api_key[-4:]


class BrokerCredentialService:
    def __init__(
        self,
        runtime: ZerodhaRuntime,
        session_factory: SessionFactory,
        vault: CredentialVault | None,
        *,
        vault_problem: str | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.runtime = runtime
        self._session_factory = session_factory
        self.vault = vault
        self.vault_problem = vault_problem
        self._clock = clock
        runtime.auth.add_listener(self._on_auth_state)

    def __repr__(self) -> str:
        return f"BrokerCredentialService(storage={'on' if self.vault else 'off'})"

    # --- helpers ---------------------------------------------------------------

    @property
    def storage_available(self) -> bool:
        return self.vault is not None

    @property
    def environment_managed(self) -> bool:
        return self.runtime.credentials_source == "ENVIRONMENT"

    def _require_vault(self) -> CredentialVault:
        if self.vault is None:
            raise CredentialError(
                503,
                "Secure credential storage is not set up on the server "
                "(CREDENTIALS_ENCRYPTION_KEY). Nothing was saved.",
            )
        return self.vault

    def _ctx(self, user_id: uuid.UUID, field: str) -> bytes:
        return CredentialVault.context(str(user_id), BROKER, field)

    async def _row(self, db: AsyncSession, user_id: uuid.UUID) -> BrokerCredential | None:
        result = await db.execute(
            select(BrokerCredential).where(
                BrokerCredential.user_id == user_id, BrokerCredential.broker == BROKER
            )
        )
        return result.scalar_one_or_none()

    def _decrypt(self, row: BrokerCredential) -> tuple[str, str]:
        vault = self._require_vault()
        try:
            key = vault.decrypt(row.api_key_encrypted, context=self._ctx(row.user_id, "api_key"))
            secret = vault.decrypt(
                row.api_secret_encrypted, context=self._ctx(row.user_id, "api_secret")
            )
        except VaultError as exc:
            raise CredentialError(
                409,
                "The saved credentials cannot be decrypted with the server's current key. "
                "Remove them and enter them again.",
            ) from exc
        return key, secret

    def _owned_by(self, user_id: uuid.UUID) -> bool:
        return self.runtime.owner_user_id == user_id

    def _other_owner_connected(self, user_id: uuid.UUID) -> bool:
        owner = self.runtime.owner_user_id
        return owner is not None and owner != user_id and self.runtime.auth.authenticated

    async def _activate(self, db: AsyncSession, row: BrokerCredential) -> None:
        """Make this user's credentials drive the server's Kite connection."""

        if self._owned_by(row.user_id) and self.runtime.configured:
            return
        if self._other_owner_connected(row.user_id):
            raise CredentialError(
                409,
                "Another account's Zerodha session is connected on this server. "
                "It must disconnect before these credentials can be used.",
            )
        key, secret = self._decrypt(row)
        await db.execute(
            update(BrokerCredential)
            .where(BrokerCredential.broker == BROKER, BrokerCredential.id != row.id)
            .values(is_active=False)
        )
        row.is_active = True
        await db.commit()
        await self.runtime.apply_credentials(key, secret, owner_user_id=row.user_id)
        logger.info("Zerodha credentials of user %s now drive the Kite connection", row.user_id)

    # --- start-up ---------------------------------------------------------------

    async def load_active(self) -> bool:
        """At start-up: apply the active user's saved credentials (unless the
        environment provides credentials, which then take precedence)."""

        if self.environment_managed or self.vault is None:
            return False
        async with self._session_factory() as db:
            result = await db.execute(
                select(BrokerCredential)
                .where(BrokerCredential.broker == BROKER, BrokerCredential.is_active.is_(True))
                .order_by(BrokerCredential.updated_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            try:
                key, secret = self._decrypt(row)
            except CredentialError:
                logger.warning("Saved Zerodha credentials could not be decrypted: not applied")
                row.last_status = ConnectionStatus.CONNECTION_FAILED.value
                row.last_error = "cannot be decrypted with the current server key"
                await db.commit()
                return False
        await self.runtime.apply_credentials(key, secret, owner_user_id=row.user_id)
        logger.info("Saved Zerodha credentials applied (user %s)", row.user_id)
        return True

    async def restore_session(self) -> AuthState | None:
        """Re-use today's saved (encrypted) access token, if still valid."""

        owner = self.runtime.owner_user_id
        if owner is None or self.vault is None:
            return None
        async with self._session_factory() as db:
            row = await self._row(db, owner)
            if row is None or not row.access_token_encrypted:
                return None
            try:
                token = self.vault.decrypt(
                    row.access_token_encrypted, context=self._ctx(owner, "access_token")
                )
            except VaultError:
                row.access_token_encrypted = None
                await db.commit()
                return None
            expires = row.access_token_expires_at
        state = await self.runtime.auth.restore_session(token, expires)
        if state is not AuthState.AUTHENTICATED:
            await self._clear_token(owner, ConnectionStatus.SESSION_EXPIRED)
        return state

    # --- auth events -> stored token / status -------------------------------------

    async def _on_auth_state(self, state: AuthState) -> None:
        owner = self.runtime.owner_user_id
        if owner is None or self.vault is None:
            return
        if state is AuthState.AUTHENTICATED:
            token = self.runtime.auth.current_access_token()
            session = self.runtime.auth.session
            async with self._session_factory() as db:
                row = await self._row(db, owner)
                if row is None:
                    return
                if token:
                    row.access_token_encrypted = self.vault.encrypt(
                        token, context=self._ctx(owner, "access_token")
                    )
                    row.access_token_expires_at = session.expires_at if session else None
                row.last_status = ConnectionStatus.CONNECTED.value
                row.last_error = None
                row.last_connected_at = self._clock()
                await db.commit()
        elif state is AuthState.TOKEN_EXPIRED:
            await self._clear_token(owner, ConnectionStatus.SESSION_EXPIRED)
        elif state is AuthState.LOGGED_OUT:
            await self._clear_token(owner, ConnectionStatus.NOT_CONNECTED)

    async def _clear_token(self, user_id: uuid.UUID, status: ConnectionStatus) -> None:
        async with self._session_factory() as db:
            row = await self._row(db, user_id)
            if row is None:
                return
            row.access_token_encrypted = None
            row.access_token_expires_at = None
            row.last_status = status.value
            if status is ConnectionStatus.SESSION_EXPIRED:
                row.last_error = self.runtime.auth.last_error or "the Kite session expired"
            await db.commit()

    # --- status -------------------------------------------------------------------

    def _live_status(self, row: BrokerCredential) -> tuple[ConnectionStatus, str | None]:
        auth = self.runtime.auth
        if auth.state is AuthState.AUTHENTICATED and auth.authenticated:
            return ConnectionStatus.CONNECTED, None
        if auth.state is AuthState.AUTHENTICATING or auth.has_pending_login():
            return ConnectionStatus.AWAITING_LOGIN, None
        if auth.state is AuthState.TOKEN_EXPIRED or (
            auth.state is AuthState.AUTHENTICATED and not auth.authenticated
        ):
            return ConnectionStatus.SESSION_EXPIRED, auth.last_error
        if auth.state is AuthState.AUTH_ERROR:
            return ConnectionStatus.CONNECTION_FAILED, _explain(auth.last_error)
        if row.last_connected_at is None and row.last_status in (
            ConnectionStatus.CREDENTIALS_SAVED.value,
            ConnectionStatus.AWAITING_LOGIN.value,
        ):
            return ConnectionStatus.CREDENTIALS_SAVED, None
        if row.last_status == ConnectionStatus.SESSION_EXPIRED.value:
            return ConnectionStatus.SESSION_EXPIRED, row.last_error
        return ConnectionStatus.NOT_CONNECTED, None

    def _body(
        self,
        status: ConnectionStatus,
        *,
        row: BrokerCredential | None,
        detail: str | None = None,
        source: str = "USER",
    ) -> dict[str, Any]:
        auth = self.runtime.auth
        active = row is not None and self._owned_by(row.user_id)
        session = auth.session if active or source == "ENVIRONMENT" else None
        return {
            "broker": BROKER,
            "status": status.value,
            "status_label": LABELS[status],
            "detail": detail,
            "configured": row is not None or source == "ENVIRONMENT",
            "credentials_source": source,
            "api_key_hint": row.api_key_hint if row is not None else None,
            "api_secret": "Configured" if row is not None else None,
            "active_on_server": active,
            "connected": status is ConnectionStatus.CONNECTED,
            "user_id": (
                session.user_id if session and status is ConnectionStatus.CONNECTED else None
            ),
            "user_name": (
                session.user_name if session and status is ConnectionStatus.CONNECTED else None
            ),
            "session_expires_at": (
                session.expires_at if session and status is ConnectionStatus.CONNECTED else None
            ),
            "last_validated_at": auth.last_validated_at if active else None,
            "last_connected_at": row.last_connected_at if row is not None else None,
            "updated_at": row.updated_at if row is not None else None,
            "storage": "ENCRYPTED" if self.vault is not None else "UNAVAILABLE",
            "storage_problem": None if self.vault is not None else self.vault_problem,
            "redirect_url_configured": bool(auth.redirect_url),
            "paper_trading_available": True,
            "live_trading_note": (
                "Live orders stay disabled unless the server enables live trading, an "
                "admin arms it, Zerodha is connected and every pre-trade check passes."
            ),
        }

    async def status(self, user_id: uuid.UUID) -> dict[str, Any]:
        if self.environment_managed:
            auth = self.runtime.auth
            if auth.authenticated:
                st = ConnectionStatus.CONNECTED
            elif auth.state is AuthState.TOKEN_EXPIRED:
                st = ConnectionStatus.SESSION_EXPIRED
            elif auth.state is AuthState.AUTH_ERROR:
                st = ConnectionStatus.CONNECTION_FAILED
            else:
                st = ConnectionStatus.NOT_CONNECTED
            return self._body(
                st,
                row=None,
                source="ENVIRONMENT",
                detail=(
                    "This server takes its Zerodha credentials from its own configuration. "
                    "Remove KITE_API_KEY / KITE_API_SECRET there to manage them in the app."
                ),
            )
        async with self._session_factory() as db:
            row = await self._row(db, user_id)
        if row is None:
            return self._body(ConnectionStatus.NOT_CONFIGURED, row=None)
        if self._owned_by(user_id):
            st, detail = self._live_status(row)
            return self._body(st, row=row, detail=detail)
        detail = None
        if self._other_owner_connected(user_id):
            detail = "Another account's Zerodha session is in use on this server."
        st = (
            ConnectionStatus.CREDENTIALS_SAVED
            if row.last_connected_at is None
            else ConnectionStatus.NOT_CONNECTED
        )
        if row.last_status == ConnectionStatus.CONNECTION_FAILED.value:
            st, detail = ConnectionStatus.CONNECTION_FAILED, row.last_error
        return self._body(st, row=row, detail=detail)

    # --- actions ------------------------------------------------------------------

    async def save(self, user_id: uuid.UUID, api_key: Any, api_secret: Any) -> dict[str, Any]:
        if self.environment_managed:
            raise CredentialError(
                409,
                "This server takes its Zerodha credentials from its configuration; "
                "they cannot be changed from the app.",
            )
        key, secret = validate_credentials(api_key, api_secret)
        vault = self._require_vault()
        replacing_active = self._owned_by(user_id)
        if replacing_active and self.runtime.auth.client is not None:
            if self.runtime.auth.client.has_token:
                await self.runtime.auth.logout()  # end the old key's session at Kite
        async with self._session_factory() as db:
            row = await self._row(db, user_id)
            if row is None:
                row = BrokerCredential(user_id=user_id, broker=BROKER)
                db.add(row)
            row.api_key_encrypted = vault.encrypt(key, context=self._ctx(user_id, "api_key"))
            row.api_secret_encrypted = vault.encrypt(
                secret, context=self._ctx(user_id, "api_secret")
            )
            row.api_key_hint = _hint(key)
            row.key_id = vault.key_id
            row.access_token_encrypted = None  # a token belongs to the old key
            row.access_token_expires_at = None
            row.last_status = ConnectionStatus.CREDENTIALS_SAVED.value
            row.last_error = None
            row.last_connected_at = None
            await db.commit()
            await db.refresh(row)
            logger.info("Zerodha credentials saved for user %s (key %s)", user_id, row.api_key_hint)
            if replacing_active:
                await self.runtime.apply_credentials(key, secret, owner_user_id=user_id)
            elif not self._other_owner_connected(user_id):
                await self._activate(db, row)
        return await self.status(user_id)

    async def remove(self, user_id: uuid.UUID) -> dict[str, Any]:
        if self.environment_managed:
            raise CredentialError(409, "This server's Zerodha credentials are not managed here.")
        async with self._session_factory() as db:
            row = await self._row(db, user_id)
            if row is None:
                raise CredentialError(404, "No Zerodha credentials are saved for your account.")
            if self._owned_by(user_id):
                if self.runtime.auth.client is not None and self.runtime.auth.client.has_token:
                    await self.runtime.auth.logout()  # invalidates the token at Kite
                await self.runtime.apply_credentials(None, None)
            await db.delete(row)
            await db.commit()
        logger.info("Zerodha credentials removed for user %s", user_id)
        return await self.status(user_id)

    async def login_url(self, user_id: uuid.UUID) -> str:
        if self.environment_managed:
            return self.runtime.auth.get_login_url()
        async with self._session_factory() as db:
            row = await self._row(db, user_id)
            if row is None:
                raise CredentialError(409, "Save your API key and API secret first.")
            await self._activate(db, row)
            row.last_status = ConnectionStatus.AWAITING_LOGIN.value
            row.last_error = None
            await db.commit()
        if not self.runtime.auth.redirect_url:
            raise CredentialError(
                409,
                "The server has no Kite redirect URL (KITE_REDIRECT_URL); the login "
                "cannot return to it.",
            )
        return self.runtime.auth.get_login_url()

    async def disconnect(self, user_id: uuid.UUID) -> dict[str, Any]:
        if self.environment_managed or self._owned_by(user_id):
            if self.runtime.auth.client is not None and self.runtime.auth.client.has_token:
                await self.runtime.auth.logout()
            else:
                self.runtime.auth.cancel_pending_login()
        async with self._session_factory() as db:
            row = await self._row(db, user_id)
            if row is not None and row.last_connected_at is not None:
                row.last_status = ConnectionStatus.NOT_CONNECTED.value
                await db.commit()
        return await self.status(user_id)

    async def test(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Verify what can be verified now, and say plainly what cannot."""

        if not self.environment_managed:
            async with self._session_factory() as db:
                row = await self._row(db, user_id)
            if row is None:
                raise CredentialError(409, "Save your API key and API secret first.")
            self._decrypt(row)  # readable with the current key
        auth = self.runtime.auth
        if (self.environment_managed or self._owned_by(user_id)) and auth.client is not None:
            if auth.client.has_token:
                try:
                    await auth.validate_session()
                except KiteError as exc:
                    auth.last_error = f"check failed: {type(exc).__name__}"
                status = await self.status(user_id)
                ok = status["status"] == ConnectionStatus.CONNECTED.value
                return {
                    "ok": ok,
                    "verified": True,
                    "message": (
                        "Zerodha accepted the session."
                        if ok
                        else "Zerodha did not accept the session. Reconnect to Zerodha."
                    ),
                    "status": status,
                }
        status = await self.status(user_id)
        return {
            "ok": True,
            "verified": False,
            "message": (
                "Credentials are saved and readable on the server. Zerodha checks the API "
                "key when you log in and the API secret during the token exchange, so "
                "connect to Zerodha to verify them."
            ),
            "status": status,
        }


__all__ = [
    "BrokerCredentialService",
    "ConnectionStatus",
    "CredentialError",
    "LABELS",
    "validate_credentials",
]
