"""Authentication endpoints.

Route handlers stay thin — all logic lives in app.services.auth_service,
per PROJECT_CONTRACT.md section 2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.database.models.user import User
from app.database.session import get_db
from app.schemas.auth import (
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserLoginRequest,
    UserPublic,
    UserRegisterRequest,
)
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    authenticate_user,
    issue_tokens_for_user,
    refresh_access_token,
    register_user,
    revoke_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    try:
        user = await register_user(
            db, email=payload.email, password=payload.password, full_name=payload.full_name
        )
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc

    access_token, refresh_token = await issue_tokens_for_user(
        db,
        user=user,
        settings=settings,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserPublic.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: UserLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    try:
        user = await authenticate_user(db, email=payload.email, password=payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        ) from exc

    access_token, refresh_token = await issue_tokens_for_user(
        db,
        user=user,
        settings=settings,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserPublic.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    try:
        access_token, refresh_token, user = await refresh_access_token(
            db, refresh_token=payload.refresh_token, settings=settings
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from exc

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserPublic.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout(payload: LogoutRequest, db: AsyncSession = Depends(get_db)) -> None:
    await revoke_refresh_token(db, refresh_token=payload.refresh_token)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(current_user)
