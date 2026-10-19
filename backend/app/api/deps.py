"""Shared FastAPI dependencies for the API layer."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.constants import UserRole
from app.database.models.user import User
from app.database.session import get_db
from app.services.auth_service import InvalidCredentialsError, get_user_from_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await get_user_from_access_token(
            db, access_token=credentials.credentials, settings=settings
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_role(*allowed_roles: UserRole):
    """Dependency factory enforcing a minimum role.

    Usage: `Depends(require_role(UserRole.ADMIN))`. Not yet used by any
    route in this phase (there is nothing role-gated to protect yet),
    but provided now so Phase 3+ routes have a ready-made, tested
    permission check rather than each inventing its own.
    """

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return user

    return _check


async def _user_for_token(token: str, settings: Settings) -> User | None:
    from app.database.session import async_session_factory

    try:
        async with async_session_factory() as db:
            try:
                return await get_user_from_access_token(db, access_token=token, settings=settings)
            except InvalidCredentialsError:
                return None
    except InvalidCredentialsError:
        return None
    except Exception as exc:  # noqa: BLE001 - the database is needed to verify a session
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot verify the session right now (database unavailable).",
        ) from exc


async def read_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """Data endpoints: open in development (the existing behaviour), a valid
    access token required when API_REQUIRE_AUTH is on (default in
    production/staging). Write endpoints keep their own role checks."""

    if not settings.effective_api_require_auth:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if await _user_for_token(credentials.credentials, settings) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def websocket_allowed(token: str | None, settings: Settings) -> bool:
    if not settings.effective_api_require_auth:
        return True
    return bool(token) and await _user_for_token(token or "", settings) is not None
