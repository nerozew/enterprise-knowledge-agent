from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models.entities import User
from app.repositories.users import UserRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    if token is None:
        raise AppError("authentication_required", "需要登录后访问", 401)
    user_id = decode_access_token(token, settings)
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AppError("unauthorized", "登录凭据无效或账号已停用", 401)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
