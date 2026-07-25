from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import LoginAttemptLimiter, login_attempt_limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.models.entities import User
from app.repositories.users import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        limiter: LoginAttemptLimiter = login_attempt_limiter,
    ) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.limiter = limiter

    async def register(self, payload: RegisterRequest) -> tuple[User, str]:
        email = normalize_email(str(payload.email))
        if await self.users.get_by_email(email):
            raise AppError("email_exists", "该邮箱已注册", 409)
        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
        )
        self.users.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AppError("email_exists", "该邮箱已注册", 409) from exc
        await self.session.refresh(user)
        return user, create_access_token(user.id, self.settings)

    async def login(self, payload: LoginRequest, client_ip: str = "unknown") -> tuple[User, str]:
        email = normalize_email(str(payload.email))
        retry_after = self.limiter.retry_after(client_ip, email)
        if retry_after:
            raise AppError(
                "login_rate_limited",
                "登录失败次数过多，请稍后重试",
                429,
                {"retry_after": retry_after},
            )
        user = await self.users.get_by_email(email)
        if user is None or not verify_password(payload.password, user.password_hash):
            self.limiter.record_failure(client_ip, email)
            raise AppError("invalid_credentials", "邮箱或密码错误", 401)
        if not user.is_active:
            self.limiter.record_failure(client_ip, email)
            raise AppError("account_disabled", "账号已停用", 403)
        self.limiter.record_success(client_ip, email)
        return user, create_access_token(user.id, self.settings)
