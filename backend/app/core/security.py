"""Password hashing and JWT helpers.

Per PROJECT_CONTRACT.md section 8: passwords are hashed with a modern,
salted algorithm and never stored or logged in plaintext. JWTs are
signed with app.core.config.Settings.jwt_secret, which must come from
the environment — never hardcoded.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum

import jwt
from passlib.context import CryptContext

from app.core.config import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password. Never log or persist the input."""

    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored hash."""

    return _pwd_context.verify(plain_password, hashed_password)


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


def create_access_token(*, user_id: uuid.UUID, settings: Settings) -> str:
    return _create_token(
        user_id=user_id,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        settings=settings,
    )


def create_refresh_token(*, user_id: uuid.UUID, settings: Settings) -> str:
    return _create_token(
        user_id=user_id,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(minutes=settings.refresh_token_expire_minutes),
        settings=settings,
    )


def _create_token(
    *, user_id: uuid.UUID, token_type: TokenType, expires_delta: timedelta, settings: Settings
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        # A random jti lets the refresh token be uniquely identified/hashed
        # for storage (see app.database.models.session.Session) without
        # the hash depending only on user_id + timestamps.
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class InvalidTokenError(Exception):
    """Raised when a JWT is malformed, expired, or the wrong type."""


def decode_token(token: str, *, expected_type: TokenType, settings: Settings) -> dict:
    """Decode and validate a JWT, returning its payload.

    Raises InvalidTokenError for any failure — callers should not need to
    know about PyJWT's specific exception hierarchy.
    """

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Token is invalid or expired.") from exc

    if payload.get("type") != expected_type.value:
        raise InvalidTokenError(f"Expected a {expected_type.value} token.")

    return payload


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage (see Session.refresh_token_hash).

    SHA-256 (not bcrypt) is appropriate here: the input is already a
    high-entropy, fixed-format JWT rather than a low-entropy user
    password, so a fast, deterministic hash used purely for exact-match
    lookup is the right tool — bcrypt's per-hash salt would make lookup
    by value impossible.
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
