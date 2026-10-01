"""Authentication service.

Holds the business logic behind /auth/*; API routes stay thin and call
into this module, per PROJECT_CONTRACT.md section 2.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.constants import TradingMode
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.database.models.risk_config import RiskConfig
from app.database.models.session import Session as SessionModel
from app.database.models.trading_settings import TradingSettings
from app.database.models.user import User


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def register_user(
    db: AsyncSession, *, email: str, password: str, full_name: str | None
) -> User:
    """Create a new user plus its default settings rows.

    Every user gets a TradingSettings row defaulting to PAPER mode and a
    RiskConfig row with the platform's default risk parameters — per
    PROJECT_CONTRACT.md section 9, a user must never end up without an
    explicit, safe trading-mode default.
    """

    existing = await get_user_by_email(db, email)
    if existing is not None:
        raise EmailAlreadyRegisteredError(email)

    user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
    db.add(user)
    await db.flush()  # populate user.id before using it below

    db.add(
        TradingSettings(
            user_id=user.id,
            trading_mode=TradingMode.PAPER,
        )
    )
    db.add(RiskConfig(user_id=user.id))

    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()
    return user


async def issue_tokens_for_user(
    db: AsyncSession,
    *,
    user: User,
    settings: Settings,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[str, str]:
    """Create a new access/refresh token pair and persist the refresh
    token's hash as a Session row."""

    access_token = create_access_token(user_id=user.id, settings=settings)
    refresh_token = create_refresh_token(user_id=user.id, settings=settings)

    expires_at = datetime.now(UTC) + timedelta(minutes=settings.refresh_token_expire_minutes)
    db.add(
        SessionModel(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
    )
    await db.commit()

    return access_token, refresh_token


async def refresh_access_token(
    db: AsyncSession, *, refresh_token: str, settings: Settings
) -> tuple[str, str, User]:
    """Validate a refresh token and rotate it.

    Rotation (revoke old session, issue a brand-new refresh token) means
    a stolen, already-used refresh token cannot be replayed indefinitely.
    """

    try:
        payload = decode_token(refresh_token, expected_type=TokenType.REFRESH, settings=settings)
    except InvalidTokenError as exc:
        raise InvalidCredentialsError() from exc

    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(
        select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
    )
    session_row = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if session_row is None or session_row.revoked_at is not None or session_row.expires_at < now:
        raise InvalidCredentialsError()

    user = await get_user_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise InvalidCredentialsError()

    session_row.revoked_at = now
    await db.commit()

    new_access_token, new_refresh_token = await issue_tokens_for_user(
        db,
        user=user,
        settings=settings,
        user_agent=session_row.user_agent,
        ip_address=session_row.ip_address,
    )
    return new_access_token, new_refresh_token, user


async def revoke_refresh_token(db: AsyncSession, *, refresh_token: str) -> None:
    """Revoke a session by its refresh token (logout).

    Silently succeeds if the token doesn't match a known session, since
    the end state the caller wants (no valid session for this token) is
    already true.
    """

    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(
        select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
    )
    session_row = result.scalar_one_or_none()
    if session_row is not None and session_row.revoked_at is None:
        session_row.revoked_at = datetime.now(UTC)
        await db.commit()


async def get_user_from_access_token(
    db: AsyncSession, *, access_token: str, settings: Settings
) -> User:
    try:
        payload = decode_token(access_token, expected_type=TokenType.ACCESS, settings=settings)
    except InvalidTokenError as exc:
        raise InvalidCredentialsError() from exc

    user = await get_user_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise InvalidCredentialsError()
    return user
