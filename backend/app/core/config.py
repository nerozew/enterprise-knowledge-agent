from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "日常灵感智能助手"
    app_env: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "mysql+asyncmy://inspiration:change-me@127.0.0.1:3306/"
        "inspiration_assistant?charset=utf8mb4"
    )
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=1440, ge=5, le=43200)
    cors_origins: list[str] = ["http://localhost:5173"]

    ai_provider_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_timeout_seconds: float = Field(default=30, ge=1, le=120)

    mcp_user_id: str | None = None
    mcp_local_token: str | None = None

    @field_validator("database_url")
    @classmethod
    def require_utf8mb4_for_mysql(cls, value: str) -> str:
        if value.startswith("mysql+") and "charset=utf8mb4" not in value:
            separator = "&" if "?" in value else "?"
            return f"{value}{separator}charset=utf8mb4"
        return value

    @model_validator(mode="after")
    def reject_unsafe_jwt_secret(self) -> Settings:
        if self.app_env.casefold() in {"test", "testing"}:
            return self
        normalized = self.jwt_secret.strip().casefold()
        known_placeholders = {
            "",
            "change-me",
            "please-change-me",
            "development-only-change-this-secret-key",
            "请替换为至少32位的随机字符串",
        }
        looks_like_placeholder = any(
            marker in normalized for marker in ("change-me", "replace", "替换", "示例")
        )
        if len(self.jwt_secret) < 32 or normalized in known_placeholders or looks_like_placeholder:
            raise ValueError("非测试环境必须配置至少 32 位且非占位值的 JWT_SECRET")
        return self

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_provider_url and self.ai_api_key and self.ai_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
