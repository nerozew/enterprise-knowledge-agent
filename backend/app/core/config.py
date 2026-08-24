from __future__ import annotations

from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
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
        "mysql+asyncmy://inspiration:change-me@127.0.0.1:3306/inspiration_assistant?charset=utf8mb4"
    )
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=1440, ge=5, le=43200)
    cors_origins: list[str] = ["http://localhost:5173"]

    ai_provider_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_timeout_seconds: float = Field(default=30, ge=1, le=120)

    agent_dispatch_poll_seconds: float = Field(default=1.0, ge=0.1, le=30)
    agent_dispatch_claim_limit: int = Field(default=4, ge=1, le=100)
    agent_dispatch_lease_seconds: int = Field(default=30, ge=10, le=300)
    agent_dispatch_renew_seconds: int = Field(default=10, ge=1, le=120)
    agent_dispatch_max_attempts: int = Field(default=5, ge=1, le=20)
    agent_approval_ttl_seconds: int = Field(default=900, ge=60, le=86400)

    mcp_user_id: str | None = None
    mcp_local_token: str | None = None
    mcp_p9_enabled: bool = False
    mcp_p9_protocol_version: Literal["2025-11-25"] = "2025-11-25"
    mcp_p9_http_mount_path: str = "/api/v1/workspaces/{workspace_id}"
    mcp_p9_stdio_actor_id: UUID | None = None
    mcp_p9_stdio_workspace_id: UUID | None = None
    mcp_p9_stdio_credential: SecretStr | None = Field(default=None, min_length=32)

    redis_url: str | None = None
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_secure: bool = False
    minio_source_bucket: str = "knowledge-source"
    minio_derived_bucket: str = "knowledge-derived"
    milvus_uri: str | None = None
    milvus_token: str | None = None
    external_storage_timeout_seconds: float = Field(default=5, ge=0.1, le=60)

    rag_provider_mode: Literal["REMOTE", "LOCAL", "FIXTURE", "DISABLED"] = "DISABLED"
    rag_embedding_url: str | None = None
    rag_embedding_api_key: str | None = None
    rag_embedding_model: str | None = None
    rag_reranker_url: str | None = None
    rag_reranker_api_key: str | None = None
    rag_reranker_model: str | None = None
    rag_reranker_profile: Literal["OPENAI_COMPATIBLE", "ALIBABA_QWEN3"] = "OPENAI_COMPATIBLE"
    rag_reranker_endpoint: str | None = Field(default=None, max_length=256)
    rag_llm_url: str | None = None
    rag_llm_api_key: str | None = None
    rag_llm_model: str | None = None
    rag_llm_profile: Literal["OPENAI_JSON_SCHEMA", "ALIBABA_QWEN"] = "OPENAI_JSON_SCHEMA"
    rag_sync_ingestion_enabled: bool = False
    rag_max_upload_mib: int = Field(default=50, ge=1, le=200)
    rag_evidence_min_score: float = Field(default=0.015, ge=0, le=1)
    rag_evidence_min_lexical_coverage: float = Field(default=0.2, ge=0, le=1)
    unstructured_pipeline_v2_enabled: bool = False
    rag_pptx_enabled: bool = True
    rag_ocr_enabled: bool = True
    rag_ocr_engine: Literal["RAPIDOCR", "DISABLED"] = "RAPIDOCR"
    rag_ocr_languages: str = Field(default="ch,en", min_length=2, max_length=80)
    rag_ocr_mode_default: Literal["AUTO", "FORCE", "OFF"] = "AUTO"
    rag_ocr_max_pages: int = Field(default=200, ge=1, le=1000)
    rag_ocr_render_dpi: int = Field(default=300, ge=72, le=600)
    rag_ocr_page_timeout_seconds: float = Field(default=45, ge=1, le=300)
    rag_ingestion_lease_seconds: int = Field(default=300, ge=60, le=3600)

    @field_validator("database_url")
    @classmethod
    def require_utf8mb4_for_mysql(cls, value: str) -> str:
        if value.startswith("mysql+") and "charset=utf8mb4" not in value:
            separator = "&" if "?" in value else "?"
            return f"{value}{separator}charset=utf8mb4"
        return value

    @field_validator("rag_reranker_endpoint")
    @classmethod
    def require_relative_provider_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("RAG_RERANKER_ENDPOINT must be a relative URL path")
        endpoint = value.strip()
        if not endpoint:
            return None
        segments = endpoint.split("/")
        if (
            endpoint.startswith("/")
            or "\\" in endpoint
            or "%" in endpoint
            or "://" in endpoint
            or ":" in endpoint
            or "?" in endpoint
            or "#" in endpoint
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ValueError("RAG_RERANKER_ENDPOINT must be a relative URL path")
        return endpoint

    @field_validator("mcp_p9_http_mount_path")
    @classmethod
    def require_workspace_scoped_mcp_mount(cls, value: str) -> str:
        path = value.strip()
        if (
            not path.startswith("/")
            or path.endswith("/")
            or path.count("{workspace_id}") != 1
            or "//" in path
            or ".." in path
            or "://" in path
        ):
            raise ValueError(
                "MCP_P9_HTTP_MOUNT_PATH must be an absolute workspace-scoped path"
            )
        return path

    @model_validator(mode="after")
    def reject_unsafe_jwt_secret(self) -> Settings:
        if self.agent_dispatch_renew_seconds >= self.agent_dispatch_lease_seconds:
            raise ValueError("AGENT_DISPATCH_RENEW_SECONDS must be lower than lease seconds")
        if self.app_env.casefold() in {"prod", "production"} and self.mcp_p9_enabled:
            raise ValueError(
                "MCP_P9_ENABLED cannot be enabled in production before B2 authentication"
            )
        if (
            self.rag_reranker_profile == "ALIBABA_QWEN3"
            and self.rag_reranker_model
            and self.rag_reranker_model != "qwen3-rerank"
        ):
            raise ValueError(
                "RAG_RERANKER_PROFILE=ALIBABA_QWEN3 requires RAG_RERANKER_MODEL=qwen3-rerank"
            )
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
        if self.rag_provider_mode == "FIXTURE" and self.app_env.casefold() != "development":
            raise ValueError("RAG_PROVIDER_MODE=FIXTURE 仅允许 test/development")
        return self

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_provider_url and self.ai_api_key and self.ai_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
