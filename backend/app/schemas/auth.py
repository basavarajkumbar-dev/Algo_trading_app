"""Auth request/response schemas.

Kept separate from app.database.models.user.User: the ORM model is an
internal persistence detail (it has hashed_password), while these
schemas define the public API contract.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.constants import UserRole
from app.schemas.common import Email


class UserRegisterRequest(BaseModel):
    email: Email
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserLoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserPublic(BaseModel):
    id: uuid.UUID
    email: Email
    full_name: str | None
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPublic
