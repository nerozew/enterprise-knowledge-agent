from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.common import APIModel


class RegisterRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(APIModel):
    id: UUID
    email: EmailStr
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AuthResponse(APIModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

