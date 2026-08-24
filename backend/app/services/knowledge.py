from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import PurePath
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.knowledge.adapters import (
    CacheStore,
    FakeObjectStore,
    FakeVectorStore,
    MilvusVectorStore,
    MinioObjectStore,
    ObjectStore,
    RedisCacheStore,
    VectorRecord,
    VectorStore,
)
from app.knowledge.chunking import ParentChildChunker
from app.knowledge.errors import KnowledgeError, ProviderUnavailableError
from app.knowledge.evidence import EvidenceDecision, EvidenceGate, EvidenceGateConfig
from app.knowledge.parsers import DoclingParserProvider, DocumentParser, ParserLimits
from app.knowledge.providers import (
    DisabledEmbeddingProvider,
    EvidenceItem,
    FixtureEmbeddingProvider,
    FixtureLLMProvider,
    LexicalRerankProvider,
    LLMJSONProfile,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLMProvider,
    OpenAICompatibleRerankProvider,
    ProviderMode,
    RerankAPIProfile,
    UnconfiguredEmbeddingProvider,
)
from app.knowledge.retrieval import (
    FusionMode,
    HybridRetriever,
    RetrievalConfig,
    RetrievalMode,
)
from app.knowledge.types import (
    ChunkKind,
    FusionScoring,
    IndexedChunk,
    KnowledgeChunk,
    OCRMode,
    RetrievalResult,
    RetrievalScoring,
    ScoreAvailability,
    ScoreContribution,
    ScoreDetails,
    ScoredHit,
    ScoreStage,
    SourceBlock,
)
from app.models.base import utc_now
from app.models.entities import IdempotencyRecord, MemberRole
from app.models.knowledge import (
    ChunkSourceSpan,
    CompensationTask,
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionArtifact,
    DocumentVersionStatus,
    IngestionJob,
    IngestionJobAttempt,
    IngestionStatus,
    OutboxEvent,
)
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.workspaces import IdempotencyRepository
from app.schemas.knowledge import (
    OCRMode as OCRModeValue,
)
from app.schemas.knowledge import (
    SearchHit,
    SearchRequest,
    SearchResponse,
    SourceSpanResolved,
    SourceSpanSummary,
)
from app.services.workspaces import (
    WorkspaceAccessService,
    add_audit,
    canonical_request_hash,
    hash_idempotency_key,
    validate_idempotency_key,
)

_OBJECT_STORES: dict[str, ObjectStore] = {}
_VECTOR_STORES: dict[str, VectorStore] = {}
_CACHE_STORES: dict[str, CacheStore] = {}
_PROVIDERS: dict[str, Any] = {}


class _OutboxLeaseLost(RuntimeError):
    """Raised when another worker has taken ownership of an ingestion event."""


@dataclass(frozen=True, slots=True)
class _OutboxLease:
    event_id: UUID
    owner: str
    attempt: int


def _object_store(settings: Settings) -> ObjectStore:
    key = settings.minio_endpoint or f"fake:{settings.app_env}"
    if key not in _OBJECT_STORES:
        if (
            settings.minio_endpoint
            and settings.minio_access_key
            and settings.minio_secret_key
        ):
            _OBJECT_STORES[key] = MinioObjectStore(
                settings.minio_endpoint,
                settings.minio_access_key,
                settings.minio_secret_key,
                secure=settings.minio_secure,
            )
        elif settings.app_env.casefold() in {"test", "testing", "development"}:
            _OBJECT_STORES[key] = FakeObjectStore()
        else:
            raise AppError("object_store_unavailable", "对象存储未配置", 503)
    return _OBJECT_STORES[key]


def _vector_store(settings: Settings) -> VectorStore:
    key = settings.milvus_uri or f"fake:{settings.app_env}"
    if key not in _VECTOR_STORES:
        if settings.milvus_uri:
            _VECTOR_STORES[key] = MilvusVectorStore(
                settings.milvus_uri,
                token=settings.milvus_token,
                timeout=settings.external_storage_timeout_seconds,
            )
        elif settings.app_env.casefold() in {"test", "testing", "development"}:
            _VECTOR_STORES[key] = FakeVectorStore()
        else:
            raise AppError("vector_store_unavailable", "向量存储未配置", 503)
    return _VECTOR_STORES[key]


def _cache_store(settings: Settings) -> CacheStore:
    key = settings.redis_url or f"fake:{settings.app_env}"
    if key not in _CACHE_STORES:
        if settings.redis_url:
            _CACHE_STORES[key] = RedisCacheStore(
                settings.redis_url,
                socket_timeout=settings.external_storage_timeout_seconds,
            )
        elif settings.app_env.casefold() in {"test", "testing", "development"}:
            from app.knowledge.adapters import FakeCacheStore

            _CACHE_STORES[key] = FakeCacheStore()
        else:
            raise AppError("cache_unavailable", "Redis 未配置", 503)
    return _CACHE_STORES[key]


async def close_knowledge_adapters() -> None:
    for object_adapter in _OBJECT_STORES.values():
        await object_adapter.close()
    for vector_adapter in _VECTOR_STORES.values():
        await vector_adapter.close()
    for cache_adapter in _CACHE_STORES.values():
        await cache_adapter.close()
    _OBJECT_STORES.clear()
    _VECTOR_STORES.clear()
    _CACHE_STORES.clear()
    for provider in _PROVIDERS.values():
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
    _PROVIDERS.clear()


async def knowledge_capabilities(settings: Settings) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, factory in (
        ("object_store", _object_store),
        ("vector_store", _vector_store),
        ("redis", _cache_store),
    ):
        try:
            adapter = factory(settings)
            result[name] = {
                "configured": True,
                "healthy": await adapter.health(),
                "mode": "FAKE"
                if adapter.__class__.__name__.startswith("Fake")
                else "EXTERNAL",
            }
        except (AppError, KnowledgeError) as exc:
            result[name] = {"configured": False, "healthy": False, "error": str(exc)}
    mode = ProviderMode(settings.rag_provider_mode)
    key_ready = mode is ProviderMode.LOCAL
    embedding_configured = bool(
        settings.rag_embedding_url
        and settings.rag_embedding_model
        and (key_ready or settings.rag_embedding_api_key)
    )
    reranker_configured = bool(
        settings.rag_reranker_url
        and settings.rag_reranker_model
        and (key_ready or settings.rag_reranker_api_key)
    )
    llm_configured = bool(
        settings.rag_llm_url
        and settings.rag_llm_model
        and (key_ready or settings.rag_llm_api_key)
    )
    provider_configured = mode is ProviderMode.FIXTURE or (
        mode in {ProviderMode.REMOTE, ProviderMode.LOCAL}
        and (
            embedding_configured
            or reranker_configured
            or llm_configured
        )
    )
    if mode is ProviderMode.FIXTURE:
        provider_health: bool | None = True
        provider_checked = True
        provider_status = "HEALTHY"
    elif mode in {ProviderMode.REMOTE, ProviderMode.LOCAL} and provider_configured:
        provider_health = False
        provider_checked = False
        provider_status = "UNKNOWN"
    elif mode is ProviderMode.DISABLED:
        provider_health = False
        provider_checked = True
        provider_status = "DISABLED"
    else:
        provider_health = False
        provider_checked = True
        provider_status = "UNCONFIGURED"
    result["provider"] = {
        "configured": provider_configured,
        "healthy": provider_health,
        "checked": provider_checked,
        "status": provider_status,
        "mode": settings.rag_provider_mode,
        "components": {
            "embedding": embedding_configured,
            "reranker": reranker_configured,
            "llm": llm_configured,
        },
        "profiles": {
            "reranker": settings.rag_reranker_profile,
            "llm": settings.rag_llm_profile,
        },
    }
    return result


def _embedding(settings: Settings):
    mode = ProviderMode(settings.rag_provider_mode)
    if mode is ProviderMode.FIXTURE:
        return FixtureEmbeddingProvider()
    if mode is ProviderMode.DISABLED:
        return DisabledEmbeddingProvider()
    if (
        settings.rag_embedding_url
        and settings.rag_embedding_model
        and (mode is ProviderMode.LOCAL or settings.rag_embedding_api_key)
    ):
        key = f"embedding:{mode}:{settings.rag_embedding_url}:{settings.rag_embedding_model}"
        if key not in _PROVIDERS:
            _PROVIDERS[key] = OpenAICompatibleEmbeddingProvider(
                base_url=settings.rag_embedding_url,
                model=settings.rag_embedding_model,
                mode=mode,
                api_key=settings.rag_embedding_api_key,
                timeout=settings.ai_timeout_seconds,
            )
        return _PROVIDERS[key]
    return UnconfiguredEmbeddingProvider(
        mode, settings.rag_embedding_model or f"{mode.value.lower()}-unconfigured"
    )


def _reranker(settings: Settings):
    mode = ProviderMode(settings.rag_provider_mode)
    if mode is ProviderMode.FIXTURE:
        return LexicalRerankProvider()
    if mode in {ProviderMode.REMOTE, ProviderMode.LOCAL} and (
        settings.rag_reranker_url
        and settings.rag_reranker_model
        and (mode is ProviderMode.LOCAL or settings.rag_reranker_api_key)
    ):
        key = (
            f"rerank:{mode}:{settings.rag_reranker_url}:"
            f"{settings.rag_reranker_model}:{settings.rag_reranker_profile}:"
            f"{settings.rag_reranker_endpoint}"
        )
        if key not in _PROVIDERS:
            _PROVIDERS[key] = OpenAICompatibleRerankProvider(
                base_url=settings.rag_reranker_url,
                model=settings.rag_reranker_model,
                mode=mode,
                api_key=settings.rag_reranker_api_key,
                timeout=settings.ai_timeout_seconds,
                api_profile=RerankAPIProfile(settings.rag_reranker_profile),
                endpoint=settings.rag_reranker_endpoint,
            )
        return _PROVIDERS[key]
    return None


def _llm(settings: Settings):
    mode = ProviderMode(settings.rag_provider_mode)
    if mode is ProviderMode.FIXTURE:
        return FixtureLLMProvider()
    if mode in {ProviderMode.REMOTE, ProviderMode.LOCAL} and (
        settings.rag_llm_url
        and settings.rag_llm_model
        and (mode is ProviderMode.LOCAL or settings.rag_llm_api_key)
    ):
        key = (
            f"llm:{mode}:{settings.rag_llm_url}:{settings.rag_llm_model}:"
            f"{settings.rag_llm_profile}"
        )
        if key not in _PROVIDERS:
            _PROVIDERS[key] = OpenAICompatibleLLMProvider(
                base_url=settings.rag_llm_url,
                model=settings.rag_llm_model,
                mode=mode,
                api_key=settings.rag_llm_api_key,
                timeout=settings.ai_timeout_seconds,
                json_profile=LLMJSONProfile(settings.rag_llm_profile),
            )
        return _PROVIDERS[key]
    return None


def _job_retryable(job: IngestionJob) -> bool:
    return job.status in {
        IngestionStatus.FAILED.value,
        IngestionStatus.RETRY_WAIT.value,
        IngestionStatus.CANCELLED.value,
    }


def document_snapshot(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "workspace_id": document.workspace_id,
        "title": document.title,
        "status": document.status,
        "version": document.version,
        "published_version_id": document.published_version_id,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def version_snapshot(version: DocumentVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "document_id": version.document_id,
        "format": version.format,
        "mime_type": version.mime_type,
        "file_name": version.file_name,
        "file_size": version.file_size,
        "file_sha256": version.file_sha256,
        "status": version.status,
        "parser_profile": version.parser_profile,
        "chunk_profile": version.chunk_profile,
        "index_profile": version.index_profile,
        "structure_schema_version": version.structure_schema_version,
        "layout_profile": version.layout_profile,
        "ocr_profile": version.ocr_profile,
        "page_count": version.page_count,
        "slide_count": version.slide_count,
        "ocr_page_count": version.ocr_page_count,
        "parse_warnings": version.parse_warnings_json or [],
        "created_at": version.created_at,
        "published_at": version.published_at,
    }


def job_snapshot(job: IngestionJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "workspace_id": job.workspace_id,
        "document_id": job.document_id,
        "document_version_id": job.document_version_id,
        "status": job.status,
        "stage": job.stage,
        "stage_detail": job.stage_detail,
        "pages": job.pages_json or {},
        "warnings": job.warnings_json or [],
        "progress": job.progress,
        "attempt_count": job.attempt_count,
        "retryable": _job_retryable(job),
        "error_code": job.error_code,
        "error_message": job.error_message,
        "version": job.version,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


class KnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: UUID,
        workspace_id: UUID,
        settings: Settings,
        request_id: str | None = None,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.workspace_id = workspace_id
        self.settings = settings
        self.request_id = request_id
        self.repo = KnowledgeRepository(session)
        self.access = WorkspaceAccessService(session, actor_id)
        self.idempotency = IdempotencyRepository(session)

    async def _idempotency_replay(
        self,
        *,
        scope: str,
        key: str | None,
        request_value: dict[str, Any],
    ) -> tuple[str, str, IdempotencyRecord | None]:
        validated = validate_idempotency_key(key)
        key_hash = hash_idempotency_key(validated)
        request_hash = canonical_request_hash(request_value)
        record = await self.idempotency.get(
            self.workspace_id, self.actor_id, scope, key_hash
        )
        if record and record.request_hash != request_hash:
            raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
        return key_hash, request_hash, record

    def _record_idempotency(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: UUID,
        response_status: int,
        response_json: dict[str, Any],
    ) -> None:
        self.idempotency.add(
            IdempotencyRecord(
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                status="SUCCEEDED",
                response_status=response_status,
                response_json=jsonable_encoder(response_json),
                resource_type=resource_type,
                resource_id=resource_id,
                operation_id=resource_id,
                expires_at=utc_now() + timedelta(days=7),
            )
        )

    async def list(
        self,
        *,
        include_deleted: bool,
        page: int,
        page_size: int,
        q: str | None = None,
        source_format: str | None = None,
        status: str | None = None,
        version_status: str | None = None,
    ) -> tuple[list[Document], int]:
        _, member = await self.access.require(self.workspace_id)
        if (
            member.role == MemberRole.VIEWER.value
            and version_status not in {None, DocumentVersionStatus.PUBLISHED.value}
        ):
            raise AppError("permission_denied", "当前角色无权执行此操作", 403)
        return await self.repo.list_documents(
            self.workspace_id,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
            published_only=member.role == MemberRole.VIEWER.value,
            q=q,
            source_format=source_format,
            status=status,
            version_status=version_status,
        )

    async def get(self, document_id: UUID, *, include_deleted: bool = False) -> Document:
        _, member = await self.access.require(self.workspace_id)
        document = await self.repo.get_document(self.workspace_id, document_id)
        if document is None or (
            document.status == DocumentStatus.DELETED.value and not include_deleted
        ) or (
            member.role == MemberRole.VIEWER.value
            and (
                document.published_version_id is None
                or document.status != DocumentStatus.ACTIVE.value
            )
        ):
            raise AppError("resource_not_found", "资源不存在", 404)
        return document

    async def versions(self, document_id: UUID) -> tuple[DocumentVersion, ...]:
        _, member = await self.access.require(self.workspace_id)
        document = await self.get(document_id, include_deleted=True)
        return tuple(
            await self.repo.list_versions(
                self.workspace_id,
                document_id,
                published_version_id=(
                    document.published_version_id
                    if member.role == MemberRole.VIEWER.value
                    else None
                ),
            )
        )

    async def resolve_source_span(
        self,
        document_id: UUID,
        version_id: UUID,
        source_span_id: str,
    ) -> SourceSpanResolved:
        _, member = await self.access.require(self.workspace_id)
        resolved = await self.repo.resolve_source_span(
            self.workspace_id,
            document_id,
            version_id,
            source_span_id,
            published_only=member.role == MemberRole.VIEWER.value,
        )
        if resolved is None:
            raise AppError("resource_not_found", "来源片段不存在", 404)
        span, version = resolved
        return SourceSpanResolved(
            workspace_id=span.workspace_id,
            document_id=span.document_id,
            document_version_id=span.document_version_id,
            chunk_id=span.chunk_id,
            source_span_id=span.source_span_id,
            block_ref=span.block_ref,
            page_no=span.page_no,
            slide_no=span.slide_no,
            bbox=span.bbox_json,
            block_char_start=span.block_char_start,
            block_char_end=span.block_char_end,
            excerpt=span.excerpt_text,
            excerpt_sha256=span.excerpt_sha256,
            source_sha256=version.file_sha256,
        )

    async def upload(
        self,
        *,
        filename: str,
        mime_type: str | None,
        content: bytes,
        title: str | None,
        duplicate_policy: str,
        idempotency_key: str | None,
        ocr_mode: OCRModeValue | None = None,
        document_id: UUID | None = None,
    ) -> tuple[Document, DocumentVersion, IngestionJob, bool]:
        key = validate_idempotency_key(idempotency_key)
        _, member = await self.access.require(
            self.workspace_id, minimum_role=MemberRole.EDITOR
        )
        if duplicate_policy not in {"REUSE_EXISTING", "CREATE_NEW"}:
            raise AppError("validation_error", "duplicate_policy 无效", 422)
        safe_name = PurePath(filename).name
        if safe_name != filename or not safe_name:
            raise AppError("unsafe_document", "文件名不安全", 422)
        max_bytes = self.settings.rag_max_upload_mib * 1024 * 1024
        if not content or len(content) > max_bytes:
            raise AppError("document_too_large", "文件大小超出限制", 413)
        file_hash = hashlib.sha256(content).hexdigest()
        effective_ocr_mode = ocr_mode or self.settings.rag_ocr_mode_default
        source_format = _format_from_name(safe_name)
        if source_format == "UNSUPPORTED":
            raise AppError("unsupported_document", "仅支持 PDF、DOCX、PPTX、Markdown 和 TXT", 422)
        if source_format == "PPTX" and not self.settings.rag_pptx_enabled:
            raise AppError("unsupported_document", "PPTX 摄取功能未启用", 422)
        if effective_ocr_mode == "FORCE" and not self.settings.rag_ocr_enabled:
            raise AppError("ocr_dependency_unavailable", "OCR 功能未启用", 422)
        if effective_ocr_mode == "AUTO" and not self.settings.rag_ocr_enabled:
            effective_ocr_mode = "OFF"
        request_hash = canonical_request_hash(
            {
                "file_sha256": file_hash,
                "title": title,
                "duplicate_policy": duplicate_policy,
                "document_id": document_id,
                "ocr_mode": effective_ocr_mode,
            }
        )
        scope = f"document.upload:{document_id or 'new'}"
        key_hash = hash_idempotency_key(key)
        record = await self.idempotency.get(
            self.workspace_id, self.actor_id, scope, key_hash
        )
        if record:
            if record.request_hash != request_hash:
                raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
            job = await self.repo.get_job(self.workspace_id, record.resource_id)
            if job is None:
                raise AppError("resource_not_found", "资源不存在", 404)
            version = await self.repo.get_version(
                self.workspace_id, job.document_version_id
            )
            document = await self.repo.get_document(self.workspace_id, job.document_id)
            assert version is not None and document is not None
            return document, version, job, True

        if duplicate_policy == "REUSE_EXISTING":
            existing = await self.repo.get_version_by_hash(self.workspace_id, file_hash)
            if existing:
                document = await self.repo.get_document(
                    self.workspace_id, existing.document_id
                )
                job = await self.repo.latest_job_for_version(
                    self.workspace_id, existing.id
                )
                assert document is not None and job is not None
                self.idempotency.add(
                    IdempotencyRecord(
                        workspace_id=self.workspace_id,
                        actor_id=self.actor_id,
                        scope=scope,
                        key_hash=key_hash,
                        request_hash=request_hash,
                        status="SUCCEEDED",
                        response_status=202,
                        resource_type="ingestion_job",
                        resource_id=job.id,
                        operation_id=job.id,
                        expires_at=utc_now() + timedelta(days=7),
                    )
                )
                add_audit(
                    self.session,
                    workspace_id=self.workspace_id,
                    actor_id=self.actor_id,
                    actor_role=member.role,
                    action="document.upload.reused",
                    target_type="document",
                    target_id=document.id,
                    request_id=self.request_id,
                    metadata={"file_sha256": file_hash, "job_id": str(job.id)},
                )
                await self.session.commit()
                return document, existing, job, True

        if document_id is None:
            document = Document(
                id=uuid4(),
                workspace_id=self.workspace_id,
                created_by_user_id=self.actor_id,
                title=(title or PurePath(safe_name).stem)[:240],
            )
            self.repo.add(document)
            await self.session.flush()
        else:
            document = await self.repo.get_document(
                self.workspace_id, document_id, for_update=True
            )
            if document is None or document.status == DocumentStatus.DELETED.value:
                raise AppError("resource_not_found", "资源不存在", 404)

        version_id = uuid4()
        extension = PurePath(safe_name).suffix.lower().lstrip(".")
        object_key = (
            f"workspaces/{self.workspace_id}/documents/{document.id}/versions/"
            f"{version_id}/source/{file_hash}.{extension}"
        )
        staging_key = f"staging/{self.workspace_id}/{uuid4()}/{file_hash}.{extension}"
        store = _object_store(self.settings)
        try:
            await store.put(
                self.settings.minio_derived_bucket,
                staging_key,
                content,
                (mime_type or "application/octet-stream").split(";", 1)[0],
            )
        except Exception as exc:
            raise AppError("object_store_unavailable", "暂存上传失败", 503) from exc
        version = DocumentVersion(
            id=version_id,
            workspace_id=self.workspace_id,
            document_id=document.id,
            created_by_user_id=self.actor_id,
            format=source_format,
            mime_type=(mime_type or "application/octet-stream").split(";", 1)[0],
            file_name=safe_name,
            file_size=len(content),
            file_sha256=file_hash,
            object_key=object_key,
            status=DocumentVersionStatus.PUBLISHING.value,
            index_profile=_embedding(self.settings).profile,
            structure_schema_version=(2 if self.settings.unstructured_pipeline_v2_enabled else 1),
            parser_profile=(
                "layout-document-v2"
                if self.settings.unstructured_pipeline_v2_enabled
                else "structure-v1"
            ),
            chunk_profile=(
                "parent-child-v2"
                if self.settings.unstructured_pipeline_v2_enabled
                else "parent-child-v1"
            ),
        )
        job = IngestionJob(
            workspace_id=self.workspace_id,
            document_id=document.id,
            document_version_id=version.id,
            requested_by_user_id=self.actor_id,
        )
        self.repo.add(version)
        self.repo.add(job)
        await self.session.flush()
        outbox = OutboxEvent(
            workspace_id=self.workspace_id,
            event_type="knowledge.source.promote",
            aggregate_type="document_version",
            aggregate_id=version.id,
            dedupe_hash=hashlib.sha256(
                f"source:{self.workspace_id}:{version.id}:{file_hash}".encode()
            ).hexdigest(),
            object_key=object_key,
            config_version="p2-p4-v1",
            payload_json={
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "file_sha256": file_hash,
                "staging_bucket": self.settings.minio_derived_bucket,
                "staging_key": staging_key,
                "target_bucket": self.settings.minio_source_bucket,
                "ocr_mode": effective_ocr_mode,
            },
        )
        self.repo.add(outbox)
        self.idempotency.add(
            IdempotencyRecord(
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                status="IN_PROGRESS",
                response_status=202,
                resource_type="ingestion_job",
                resource_id=job.id,
                operation_id=job.id,
                expires_at=utc_now() + timedelta(days=7),
            )
        )
        add_audit(
            self.session,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="document.upload.accepted",
            target_type="document",
            target_id=document.id,
            request_id=self.request_id,
            metadata={
                "version_id": str(version.id),
                "job_id": str(job.id),
                "format": version.format,
                "file_size": len(content),
            },
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            await store.delete(self.settings.minio_derived_bucket, staging_key)
            raise AppError("document_upload_conflict", "文档上传发生并发冲突", 409) from exc
        created_document_id = document.id
        created_job_id = job.id
        if self.settings.rag_sync_ingestion_enabled:
            await self.process_outbox_event(outbox.id)
        refreshed_document = await self.repo.get_document(
            self.workspace_id, created_document_id
        )
        refreshed_version = await self.repo.get_version(self.workspace_id, version_id)
        refreshed_job = await self.repo.get_job(self.workspace_id, created_job_id)
        assert (
            refreshed_document is not None
            and refreshed_version is not None
            and refreshed_job is not None
        )
        return refreshed_document, refreshed_version, refreshed_job, False

    async def _process(
        self,
        job: IngestionJob,
        version: DocumentVersion,
        content: bytes,
        *,
        ocr_mode: str = "AUTO",
        outbox_lease: _OutboxLease | None = None,
    ) -> None:
        job_id = job.id
        version_id = version.id
        manifest_key: str | None = None
        attempt = IngestionJobAttempt(
            workspace_id=self.workspace_id,
            job_id=job.id,
            attempt_no=job.attempt_count + 1,
        )
        self.repo.add(attempt)
        job.attempt_count += 1
        try:
            job.status = job.stage = IngestionStatus.PARSING.value
            job.stage_detail = "OCR" if ocr_mode != "OFF" else "NATIVE_TEXT"
            job.progress = 15
            job.version += 1
            job.lease_expires_at = utc_now() + timedelta(
                seconds=self.settings.rag_ingestion_lease_seconds
            )
            await self.session.commit()
            limits = ParserLimits(
                max_upload_bytes=self.settings.rag_max_upload_mib * 1024 * 1024,
                max_pdf_pages=self.settings.rag_ocr_max_pages,
            )
            if self.settings.unstructured_pipeline_v2_enabled:
                parser = DoclingParserProvider(limits=limits)
                layout = await self._await_with_outbox_lease(
                    asyncio.to_thread(
                        parser.parse_layout,
                        filename=version.file_name,
                        content=content,
                        mime_type=version.mime_type,
                        ocr_mode=OCRMode(ocr_mode),
                    ),
                    timeout=max(
                        30.0,
                        self.settings.rag_ocr_page_timeout_seconds
                        * self.settings.rag_ocr_max_pages,
                    ),
                    lease=outbox_lease,
                )
                parsed = layout.to_parsed_document()
            else:
                parser_v1 = DocumentParser(limits)
                parsed = await asyncio.to_thread(
                    parser_v1.parse,
                    filename=version.file_name,
                    content=content,
                    mime_type=version.mime_type,
                )
            version.mime_type = parsed.mime_type
            version.format = parsed.format.value.upper()
            version.parser_profile = parsed.parser_profile
            version.structure_schema_version = 2 if parsed.layout else 1
            version.layout_profile = parsed.parser_profile if parsed.layout else None
            version.ocr_profile = parsed.ocr_profile
            version.page_count = parsed.page_count
            version.slide_count = parsed.slide_count
            version.ocr_page_count = parsed.ocr_page_count
            warnings = [
                {"code": "parser_warning", "message": warning[:240]}
                for warning in parsed.warnings
            ]
            version.parse_warnings_json = warnings
            total_pages = parsed.page_count or parsed.slide_count or 0
            job.pages_json = {
                "total": total_pages,
                "ocr_required": parsed.ocr_page_count,
                "ocr_completed": parsed.ocr_page_count,
            }
            job.warnings_json = warnings

            manifest = json.dumps(
                parsed.to_manifest_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            manifest_sha = hashlib.sha256(manifest).hexdigest()
            manifest_key = (
                f"workspaces/{self.workspace_id}/documents/{version.document_id}/"
                f"versions/{version.id}/derived/layout-v{version.structure_schema_version}.json"
            )
            store = _object_store(self.settings)
            await self._await_with_outbox_lease(
                store.put(
                    self.settings.minio_derived_bucket,
                    manifest_key,
                    manifest,
                    "application/json",
                ),
                timeout=self.settings.external_storage_timeout_seconds,
                lease=outbox_lease,
            )
            version.derived_manifest_key = manifest_key
            version.derived_manifest_sha256 = manifest_sha

            job.status = job.stage = IngestionStatus.CHUNKING.value
            job.stage_detail = "PARENT_CHILD"
            job.progress = 40
            chunks = ParentChildChunker().chunk(
                parsed,
                document_id=str(version.document_id),
                document_version_id=str(version.id),
            )
            old_artifacts = await self.repo.delete_artifacts_for_version(
                self.workspace_id, version.id
            )
            for artifact in old_artifacts:
                if artifact.object_key != manifest_key:
                    self.repo.add(
                        CompensationTask(
                            workspace_id=self.workspace_id,
                            task_type="DELETE_DERIVED_OBJECT",
                            resource_type="document_version_artifact",
                            resource_id=version.id,
                            object_key=artifact.object_key,
                        )
                    )
            await self.repo.delete_chunks_for_version(self.workspace_id, version.id)
            mapping: dict[str, UUID] = {}
            for chunk in chunks:
                if chunk.kind is ChunkKind.PARENT:
                    mapping[chunk.chunk_id] = UUID(chunk.chunk_id)
            chunk_rows: list[DocumentChunk] = []
            for chunk in chunks:
                row = _chunk_row(self.workspace_id, chunk, mapping)
                self.repo.add(row)
                chunk_rows.append(row)
            self.repo.add(
                DocumentVersionArtifact(
                    workspace_id=self.workspace_id,
                    document_version_id=version.id,
                    artifact_type="LAYOUT_JSON",
                    locator_key="document",
                    object_key=manifest_key,
                    sha256=manifest_sha,
                    byte_size=len(manifest),
                    schema_version=version.structure_schema_version,
                )
            )
            for span in _chunk_source_span_rows(
                self.workspace_id,
                version,
                parsed.blocks,
                chunks,
            ):
                self.repo.add(span)

            job.status = job.stage = IngestionStatus.STORING.value
            job.stage_detail = "EMBEDDING"
            job.progress = 60
            job.lease_expires_at = utc_now() + timedelta(
                seconds=self.settings.rag_ingestion_lease_seconds
            )
            await self.session.flush()
            child_chunks = [chunk for chunk in chunks if chunk.kind is ChunkKind.CHILD]
            embedding = _embedding(self.settings)
            if self.settings.rag_provider_mode == "DISABLED":
                vectors = [[0.0] * 128 for _ in child_chunks]
            else:
                vectors = await self._await_with_outbox_lease(
                    embedding.embed_documents([chunk.text for chunk in child_chunks]),
                    timeout=max(30.0, self.settings.ai_timeout_seconds),
                    lease=outbox_lease,
                )
            vector_store = _vector_store(self.settings)
            await self._await_with_outbox_lease(
                vector_store.ensure_schema(
                    _profile_hash(version.index_profile),
                    len(vectors[0]) if vectors else 128,
                ),
                timeout=self.settings.external_storage_timeout_seconds,
                lease=outbox_lease,
            )
            records = [
                VectorRecord(
                    chunk_id=chunk.chunk_id,
                    workspace_id=str(self.workspace_id),
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    parent_id=chunk.parent_id,
                    text=chunk.text,
                    dense_vector=tuple(vector),
                    active=False,
                    source_format=version.format,
                    page=chunk.page,
                    created_at_ms=int(version.created_at.timestamp() * 1000),
                )
                for chunk, vector in zip(child_chunks, vectors, strict=True)
            ]
            await self._await_with_outbox_lease(
                vector_store.upsert(_profile_hash(version.index_profile), records),
                timeout=self.settings.external_storage_timeout_seconds,
                lease=outbox_lease,
            )
            healthy = await self._await_with_outbox_lease(
                vector_store.health(),
                timeout=self.settings.external_storage_timeout_seconds,
                lease=outbox_lease,
            )
            if not healthy:
                raise ProviderUnavailableError("vector verification failed")
            await self._await_with_outbox_lease(
                vector_store.activate_version(
                    _profile_hash(version.index_profile),
                    str(self.workspace_id),
                    str(version.id),
                ),
                timeout=self.settings.external_storage_timeout_seconds,
                lease=outbox_lease,
            )

            job.status = job.stage = IngestionStatus.INDEXING.value
            job.stage_detail = "ACTIVATING_VERSION"
            job.progress = 85
            await self._assert_outbox_lease(outbox_lease)
            document = await self.repo.get_document(
                self.workspace_id, version.document_id, for_update=True
            )
            assert document is not None
            previous_id = document.published_version_id
            if previous_id and previous_id != version.id:
                previous = await self.repo.get_version(self.workspace_id, previous_id)
                if previous:
                    previous.status = DocumentVersionStatus.SUPERSEDED.value
                    self.repo.add(
                        OutboxEvent(
                            workspace_id=self.workspace_id,
                            event_type="knowledge.vector.deactivate",
                            aggregate_type="document_version",
                            aggregate_id=previous.id,
                            dedupe_hash=hashlib.sha256(
                                f"deactivate:{self.workspace_id}:{previous.id}".encode()
                            ).hexdigest(),
                            config_version="p2-p4-v1",
                            payload_json={
                                "document_version_id": str(previous.id),
                                "index_profile": previous.index_profile,
                            },
                        )
                    )
            now = utc_now()
            document.published_version_id = version.id
            document.version += 1
            document.updated_at = now
            version.status = DocumentVersionStatus.PUBLISHED.value
            version.published_at = now
            job.status = job.stage = IngestionStatus.SUCCEEDED.value
            job.stage_detail = "COMPLETE"
            job.progress = 100
            job.lease_owner = None
            job.lease_expires_at = None
            job.version += 1
            job.updated_at = now
            attempt.outcome = IngestionStatus.SUCCEEDED.value
            attempt.ended_at = now
            for record in chunk_rows:
                record.indexed = record.kind == "CHILD"
            await self.session.execute(
                update(IdempotencyRecord)
                .where(
                    IdempotencyRecord.workspace_id == self.workspace_id,
                    IdempotencyRecord.operation_id == job.id,
                )
                .values(status="SUCCEEDED")
            )
            await self._assert_outbox_lease(outbox_lease)
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, _OutboxLeaseLost):
                raise
            failed_job = await self.repo.get_job(self.workspace_id, job_id)
            failed_version = await self.repo.get_version(self.workspace_id, version_id)
            if failed_job is not None and failed_version is not None:
                await self._fail_ingestion(
                    failed_job,
                    failed_version,
                    exc,
                    derived_object_key=manifest_key,
                )
            raise

    async def _assert_outbox_lease(
        self, lease: _OutboxLease | None
    ) -> None:
        if lease is None:
            return
        result = await self.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == lease.event_id,
                OutboxEvent.workspace_id == self.workspace_id,
                OutboxEvent.status == "PROCESSING",
                OutboxEvent.lease_owner == lease.owner,
                OutboxEvent.attempts == lease.attempt,
            )
            .values(
                lease_expires_at=utc_now()
                + timedelta(seconds=self.settings.rag_ingestion_lease_seconds)
            )
        )
        if getattr(result, "rowcount", 0) != 1:
            await self.session.rollback()
            raise _OutboxLeaseLost("outbox ingestion lease was lost")
        await self.session.commit()

    async def _await_with_outbox_lease(
        self,
        awaitable: Any,
        *,
        lease: _OutboxLease | None,
        timeout: float,
        heartbeat_interval: float | None = None,
    ) -> Any:
        """Await an ingestion stage while renewing the claimed Outbox lease.

        The loop owns the heartbeat, so completion, cancellation and exceptions
        cannot leave a detached renewal task running.
        """

        task = asyncio.create_task(awaitable)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        interval = heartbeat_interval or max(
            1.0, min(30.0, self.settings.rag_ingestion_lease_seconds / 3)
        )
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("document parsing exceeded the configured timeout")
                done, _ = await asyncio.wait({task}, timeout=min(interval, remaining))
                if task in done:
                    result = task.result()
                    await self._assert_outbox_lease(lease)
                    return result
                await self._assert_outbox_lease(lease)
        finally:
            if not task.done():
                task.cancel()

    async def _fail_ingestion(
        self,
        job: IngestionJob,
        version: DocumentVersion,
        exc: Exception,
        *,
        derived_object_key: str | None,
    ) -> None:
        retryable = isinstance(exc, (ProviderUnavailableError, TimeoutError))
        safe_code = (
            "ingestion_provider_unavailable"
            if isinstance(exc, ProviderUnavailableError)
            else "ingestion_timeout"
            if isinstance(exc, TimeoutError)
            else "unsafe_document"
            if isinstance(exc, KnowledgeError)
            else "ingestion_dependency_failed"
        )
        safe_message = str(exc)[:255] if isinstance(exc, KnowledgeError) else "摄取依赖失败"
        job.status = job.stage = (
            IngestionStatus.RETRY_WAIT.value if retryable else IngestionStatus.FAILED.value
        )
        job.error_code = safe_code
        job.error_message = safe_message
        job.stage_detail = "FAILED"
        job.lease_owner = None
        job.lease_expires_at = None
        job.version += 1
        job.updated_at = utc_now()
        if not retryable:
            version.status = DocumentVersionStatus.FAILED.value
        # The immutable canonical source remains available for an explicit retry.
        # Only rebuildable derived artifacts and inactive vectors are compensated.
        if not retryable and derived_object_key:
            self.repo.add(
                CompensationTask(
                    workspace_id=self.workspace_id,
                    task_type="DELETE_DERIVED_OBJECT",
                    resource_type="document_version_artifact",
                    resource_id=version.id,
                    object_key=derived_object_key,
                )
            )
        if not retryable:
            self.repo.add(
                CompensationTask(
                    workspace_id=self.workspace_id,
                    task_type="DEACTIVATE_VECTORS",
                    resource_type="document_version",
                    resource_id=version.id,
                )
            )
        await self.session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.workspace_id == self.workspace_id,
                IdempotencyRecord.operation_id == job.id,
            )
            .values(status="IN_PROGRESS" if retryable else "FAILED")
        )
        await self.session.commit()

    async def process_outbox_event(self, event_id: UUID) -> bool:
        worker_id = f"inprocess:{uuid4()}"
        now = utc_now()
        event = await self.session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        if event is None:
            return False
        if event.status == "SUCCEEDED":
            return True
        if (
            event.status == "PROCESSING"
            and event.lease_expires_at is not None
            and event.lease_expires_at > now
        ):
            return False
        event.status = "PROCESSING"
        event.attempts += 1
        event.lease_owner = worker_id
        event.lease_expires_at = now + timedelta(
            seconds=self.settings.rag_ingestion_lease_seconds
        )
        await self.session.commit()
        lease = _OutboxLease(event.id, worker_id, event.attempts)
        try:
            if event.event_type == "knowledge.source.promote":
                payload = event.payload_json
                store = _object_store(self.settings)
                version = await self.repo.get_version(
                    self.workspace_id, UUID(str(payload["document_version_id"]))
                )
                if version is None:
                    raise KnowledgeError("document version disappeared")
                target_bucket = str(payload["target_bucket"])
                target_key = event.object_key or version.object_key
                expected_hash = str(payload["file_sha256"])
                content: bytes | None = None
                try:
                    promoted = await self._await_with_outbox_lease(
                        store.get(target_bucket, target_key),
                        timeout=self.settings.external_storage_timeout_seconds,
                        lease=lease,
                    )
                    if hashlib.sha256(promoted).hexdigest() == expected_hash:
                        content = promoted
                except _OutboxLeaseLost:
                    raise
                except Exception:
                    # A missing target is expected before the first promotion.
                    pass
                if content is None:
                    content = await self._await_with_outbox_lease(
                        store.get(
                            str(payload["staging_bucket"]),
                            str(payload["staging_key"]),
                        ),
                        timeout=self.settings.external_storage_timeout_seconds,
                        lease=lease,
                    )
                    if hashlib.sha256(content).hexdigest() != expected_hash:
                        raise KnowledgeError("staging object hash mismatch")
                    await self._await_with_outbox_lease(
                        store.put(
                            target_bucket,
                            target_key,
                            content,
                            version.mime_type,
                        ),
                        timeout=self.settings.external_storage_timeout_seconds,
                        lease=lease,
                    )
                    promoted = await self._await_with_outbox_lease(
                        store.get(target_bucket, target_key),
                        timeout=self.settings.external_storage_timeout_seconds,
                        lease=lease,
                    )
                    if hashlib.sha256(promoted).hexdigest() != expected_hash:
                        raise KnowledgeError("promoted object hash mismatch")
                await self._await_with_outbox_lease(
                    store.delete(
                        str(payload["staging_bucket"]), str(payload["staging_key"])
                    ),
                    timeout=self.settings.external_storage_timeout_seconds,
                    lease=lease,
                )
                job = await self.repo.latest_job_for_version(
                    self.workspace_id, version.id
                )
                if job is None:
                    raise KnowledgeError("ingestion job disappeared")
                if not (
                    job.status == IngestionStatus.SUCCEEDED.value
                    and version.status == DocumentVersionStatus.PUBLISHED.value
                ):
                    await self._process(
                        job,
                        version,
                        content,
                        ocr_mode=str(payload.get("ocr_mode", "AUTO")),
                        outbox_lease=lease,
                    )
            elif event.event_type == "knowledge.ingestion.process":
                version = await self.repo.get_version(
                    self.workspace_id, event.aggregate_id
                )
                if version is None:
                    raise KnowledgeError("document version disappeared")
                content = await self._await_with_outbox_lease(
                    _object_store(self.settings).get(
                        self.settings.minio_source_bucket, version.object_key
                    ),
                    timeout=self.settings.external_storage_timeout_seconds,
                    lease=lease,
                )
                job = await self.repo.latest_job_for_version(
                    self.workspace_id, version.id
                )
                if job is None:
                    raise KnowledgeError("ingestion job disappeared")
                if not (
                    job.status == IngestionStatus.SUCCEEDED.value
                    and version.status == DocumentVersionStatus.PUBLISHED.value
                ):
                    await self._process(
                        job,
                        version,
                        content,
                        ocr_mode=str(event.payload_json.get("ocr_mode", "AUTO")),
                        outbox_lease=lease,
                    )
            elif event.event_type == "knowledge.vector.deactivate":
                version = await self.repo.get_version(
                    self.workspace_id, event.aggregate_id
                )
                if version is not None:
                    await self._await_with_outbox_lease(
                        _vector_store(self.settings).deactivate_version(
                            _profile_hash(version.index_profile),
                            str(self.workspace_id),
                            str(version.id),
                        ),
                        timeout=self.settings.external_storage_timeout_seconds,
                        lease=lease,
                    )
            else:
                raise KnowledgeError(f"unsupported outbox event: {event.event_type}")
            completed = await self.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.workspace_id == self.workspace_id,
                    OutboxEvent.status == "PROCESSING",
                    OutboxEvent.lease_owner == lease.owner,
                    OutboxEvent.attempts == lease.attempt,
                )
                .values(
                    status="SUCCEEDED",
                    processed_at=utc_now(),
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=None,
                )
            )
            if getattr(completed, "rowcount", 0) != 1:
                await self.session.rollback()
                return False
            await self.session.commit()
            return True
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, _OutboxLeaseLost):
                return False
            failed = await self.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.workspace_id == self.workspace_id,
                    OutboxEvent.status == "PROCESSING",
                    OutboxEvent.lease_owner == lease.owner,
                    OutboxEvent.attempts == lease.attempt,
                )
                .values(
                    status=(
                        "RETRY_WAIT"
                        if isinstance(exc, (ProviderUnavailableError, TimeoutError))
                        and lease.attempt < 5
                        else "DEAD"
                    ),
                    available_at=utc_now()
                    + timedelta(seconds=min(300, 2**lease.attempt)),
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=(
                        "knowledge_error"
                        if isinstance(exc, KnowledgeError)
                        else "dependency_error"
                    ),
                )
            )
            if getattr(failed, "rowcount", 0) == 1:
                await self.session.commit()
            else:
                await self.session.rollback()
            return False

    async def process_compensation_task(self, task_id: UUID) -> bool:
        now = utc_now()
        task = await self.session.scalar(
            select(CompensationTask)
            .where(
                CompensationTask.id == task_id,
                CompensationTask.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        if task is None:
            return False
        if task.status == "SUCCEEDED":
            return True
        if (
            task.status == "PROCESSING"
            and task.lease_expires_at is not None
            and task.lease_expires_at > now
        ):
            return False
        task.status = "PROCESSING"
        task.attempts += 1
        task.lease_owner = f"inprocess:{uuid4()}"
        task.lease_expires_at = now + timedelta(seconds=60)
        await self.session.commit()
        try:
            if task.resource_type in {
                "document_version",
                "document_version_artifact",
            }:
                current_version = await self.repo.get_version(
                    self.workspace_id, task.resource_id
                )
                if (
                    current_version is not None
                    and current_version.status == DocumentVersionStatus.PUBLISHED.value
                ):
                    task.status = "SUCCEEDED"
                    task.completed_at = utc_now()
                    task.error_message = None
                    task.lease_owner = None
                    task.lease_expires_at = None
                    await self.session.commit()
                    return True
            if task.task_type == "DELETE_OBJECT" and task.object_key:
                await _object_store(self.settings).delete(
                    self.settings.minio_source_bucket, task.object_key
                )
            elif task.task_type == "DELETE_DERIVED_OBJECT" and task.object_key:
                await _object_store(self.settings).delete(
                    self.settings.minio_derived_bucket, task.object_key
                )
            elif task.task_type == "DEACTIVATE_VECTORS":
                version = await self.repo.get_version(
                    self.workspace_id, task.resource_id
                )
                if version:
                    await _vector_store(self.settings).deactivate_version(
                        _profile_hash(version.index_profile),
                        str(self.workspace_id),
                        str(version.id),
                    )
            else:
                raise KnowledgeError(f"unsupported compensation: {task.task_type}")
            task = await self.session.scalar(
                select(CompensationTask).where(CompensationTask.id == task_id)
            )
            assert task is not None
            task.status = "SUCCEEDED"
            task.completed_at = utc_now()
            task.lease_owner = None
            task.lease_expires_at = None
            await self.session.commit()
            return True
        except Exception as exc:
            await self.session.rollback()
            task = await self.session.scalar(
                select(CompensationTask).where(CompensationTask.id == task_id)
            )
            if task:
                task.status = "FAILED" if task.attempts >= 5 else "RETRY_WAIT"
                task.error_message = (
                    str(exc)[:255]
                    if isinstance(exc, KnowledgeError)
                    else "补偿依赖失败"
                )
                task.lease_owner = None
                task.lease_expires_at = None
                await self.session.commit()
            return False

    async def get_job(self, job_id: UUID) -> IngestionJob:
        await self.access.require(
            self.workspace_id, minimum_role=MemberRole.EDITOR
        )
        job = await self.repo.get_job(self.workspace_id, job_id)
        if job is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        return job

    async def retry(
        self, job_id: UUID, *, idempotency_key: str | None
    ) -> IngestionJob:
        await self.access.require(self.workspace_id, minimum_role=MemberRole.EDITOR)
        scope = f"ingestion.retry:{job_id}"
        key_hash, request_hash, replay = await self._idempotency_replay(
            scope=scope,
            key=idempotency_key,
            request_value={"job_id": job_id},
        )
        if replay:
            replay_job = await self.repo.get_job(self.workspace_id, replay.resource_id)
            if replay_job is None:
                raise AppError("resource_not_found", "资源不存在", 404)
            return replay_job
        previous = await self.repo.get_job(self.workspace_id, job_id)
        if previous is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if not _job_retryable(previous):
            raise AppError("job_not_retryable", "当前任务状态不可重试", 409)
        version = await self.repo.get_version(
            self.workspace_id, previous.document_version_id
        )
        assert version is not None
        new_job = IngestionJob(
            workspace_id=self.workspace_id,
            document_id=previous.document_id,
            document_version_id=previous.document_version_id,
            requested_by_user_id=self.actor_id,
            previous_job_id=previous.id,
        )
        version.status = DocumentVersionStatus.PUBLISHING.value
        self.repo.add(new_job)
        await self.session.flush()
        retry_event = OutboxEvent(
            workspace_id=self.workspace_id,
            event_type="knowledge.ingestion.process",
            aggregate_type="document_version",
            aggregate_id=version.id,
            dedupe_hash=hashlib.sha256(
                f"retry:{self.workspace_id}:{new_job.id}".encode()
            ).hexdigest(),
            object_key=version.object_key,
            config_version="p2-p4-v1",
            payload_json={
                "document_version_id": str(version.id),
                "job_id": str(new_job.id),
                "ocr_mode": self.settings.rag_ocr_mode_default,
            },
        )
        new_job_id = new_job.id
        self.repo.add(retry_event)
        self._record_idempotency(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            resource_type="ingestion_job",
            resource_id=new_job.id,
            response_status=202,
            response_json=job_snapshot(new_job),
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.idempotency.get(
                self.workspace_id, self.actor_id, scope, key_hash
            )
            if winner and winner.request_hash == request_hash:
                winner_job = await self.repo.get_job(
                    self.workspace_id, winner.resource_id
                )
                if winner_job:
                    return winner_job
            raise AppError("idempotency_conflict", "并发重试发生冲突", 409) from exc
        if self.settings.rag_sync_ingestion_enabled:
            await self.process_outbox_event(retry_event.id)
        refreshed = await self.repo.get_job(self.workspace_id, new_job_id)
        assert refreshed is not None
        return refreshed

    async def cancel(
        self, job_id: UUID, *, expected_version: int, idempotency_key: str | None
    ) -> IngestionJob:
        await self.access.require(self.workspace_id, minimum_role=MemberRole.EDITOR)
        scope = f"ingestion.cancel:{job_id}"
        key_hash, request_hash, replay = await self._idempotency_replay(
            scope=scope,
            key=idempotency_key,
            request_value={"job_id": job_id, "version": expected_version},
        )
        if replay:
            replay_job = await self.repo.get_job(self.workspace_id, replay.resource_id)
            if replay_job is None:
                raise AppError("resource_not_found", "资源不存在", 404)
            return replay_job
        job = await self.repo.get_job(self.workspace_id, job_id, for_update=True)
        if job is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if job.version != expected_version:
            raise AppError("version_conflict", "任务已被其他操作更新", 409)
        if job.status in {
            IngestionStatus.SUCCEEDED.value,
            IngestionStatus.FAILED.value,
            IngestionStatus.CANCELLED.value,
        }:
            raise AppError("job_not_cancellable", "当前任务状态不可取消", 409)
        job.status = job.stage = IngestionStatus.CANCELLED.value
        job.version += 1
        job.updated_at = utc_now()
        self._record_idempotency(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            resource_type="ingestion_job",
            resource_id=job.id,
            response_status=200,
            response_json=job_snapshot(job),
        )
        await self.session.commit()
        return job

    async def update_document(
        self,
        document_id: UUID,
        *,
        title: str,
        expected_version: int,
        idempotency_key: str | None,
    ) -> Document:
        _, member = await self.access.require(
            self.workspace_id, minimum_role=MemberRole.EDITOR
        )
        scope = f"document.update:{document_id}"
        key_hash, request_hash, replay = await self._idempotency_replay(
            scope=scope,
            key=idempotency_key,
            request_value={
                "document_id": document_id,
                "title": title,
                "version": expected_version,
            },
        )
        if replay:
            replay_document = await self.repo.get_document(
                self.workspace_id, replay.resource_id
            )
            if replay_document is None:
                raise AppError("resource_not_found", "资源不存在", 404)
            return replay_document
        document = await self.repo.get_document(
            self.workspace_id, document_id, for_update=True
        )
        if document is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if document.version != expected_version:
            raise AppError("version_conflict", "文档已被其他操作更新", 409)
        document.title = title
        document.version += 1
        document.updated_at = utc_now()
        add_audit(
            self.session,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="document.updated",
            target_type="document",
            target_id=document.id,
            request_id=self.request_id,
            metadata={"to_version": document.version},
        )
        self._record_idempotency(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            resource_type="document",
            resource_id=document.id,
            response_status=200,
            response_json=document_snapshot(document),
        )
        await self.session.commit()
        return document

    async def delete_document(
        self,
        document_id: UUID,
        *,
        expected_version: int,
        idempotency_key: str | None,
    ) -> Document:
        _, member = await self.access.require(
            self.workspace_id, minimum_role=MemberRole.ADMIN
        )
        scope = f"document.delete:{document_id}"
        key_hash, request_hash, replay = await self._idempotency_replay(
            scope=scope,
            key=idempotency_key,
            request_value={"document_id": document_id, "version": expected_version},
        )
        if replay:
            replay_document = await self.repo.get_document(
                self.workspace_id, replay.resource_id
            )
            if replay_document is None:
                raise AppError("resource_not_found", "资源不存在", 404)
            return replay_document
        document = await self.repo.get_document(
            self.workspace_id, document_id, for_update=True
        )
        if document is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if document.version != expected_version:
            raise AppError("version_conflict", "文档已被其他操作更新", 409)
        document.status = DocumentStatus.DELETED.value
        document.deleted_at = utc_now()
        document.version += 1
        add_audit(
            self.session,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="document.deleted",
            target_type="document",
            target_id=document.id,
            request_id=self.request_id,
            metadata={"to_version": document.version},
        )
        self._record_idempotency(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            resource_type="document",
            resource_id=document.id,
            response_status=200,
            response_json=document_snapshot(document),
        )
        await self.session.commit()
        return document

    async def _milvus_retrieve(
        self,
        payload: SearchRequest,
        embedding,
        reranker,
        llm,
        *,
        use_rerank: bool,
        use_rewrite: bool,
    ) -> tuple[
        RetrievalResult,
        Sequence[tuple[DocumentChunk, Document, DocumentVersion]],
    ]:
        queries = [payload.query]
        if use_rewrite and llm is not None:
            rewrites = await llm.rewrite(
                payload.query, max_queries=2 if payload.multi_query else 1
            )
            queries.extend(query for query in rewrites if query not in queries)
        vector_store = _vector_store(self.settings)
        fused: dict[str, float] = {}
        raw_scores: dict[str, float] = {}
        source_observations: dict[str, list[ScoreContribution]] = {}
        query_contributions: dict[str, list[ScoreContribution]] = {}
        for query_index, query in enumerate(queries):
            query_vector = await embedding.embed_query(query)
            candidates = await vector_store.search(
                _profile_hash(embedding.profile),
                workspace_id=str(self.workspace_id),
                query_vector=query_vector,
                query_text=query,
                limit=payload.candidate_k,
                retrieval_mode=payload.retrieval_mode,
                fusion=payload.fusion,
                dense_weight=payload.dense_weight,
                sparse_weight=1 - payload.dense_weight,
            )
            valid_rank = 0
            seen_query_ids: set[str] = set()
            for chunk_id, score in candidates:
                canonical_chunk_id = _canonical_uuid_string(chunk_id)
                if canonical_chunk_id is None:
                    continue
                raw_scores[canonical_chunk_id] = max(
                    raw_scores.get(canonical_chunk_id, float("-inf")),
                    score,
                )
                if canonical_chunk_id in seen_query_ids:
                    continue
                seen_query_ids.add(canonical_chunk_id)
                valid_rank += 1
                source_observations.setdefault(canonical_chunk_id, []).append(
                    ScoreContribution(
                        query_index=query_index,
                        value=float(score),
                        rank=valid_rank,
                        source=(
                            "dense_similarity"
                            if payload.retrieval_mode == "DENSE"
                            else (
                                "bm25"
                                if payload.retrieval_mode == "SPARSE"
                                else "channel_fusion"
                            )
                        ),
                    )
                )
                contribution = 1 / (60 + valid_rank)
                query_contributions.setdefault(canonical_chunk_id, []).append(
                    ScoreContribution(
                        query_index=query_index,
                        value=contribution,
                        rank=valid_rank,
                        source="query_fusion",
                    )
                )
                fused[canonical_chunk_id] = fused.get(canonical_chunk_id, 0.0) + contribution
        ranked_ids = sorted(fused, key=lambda value: (-fused[value], value))
        valid_ids = [UUID(value) for value in ranked_ids]
        rows = await self.repo.published_chunks_by_ids(
            self.workspace_id, valid_ids
        )
        row_map = {str(row[0].id): row for row in rows}
        filtered_ids: list[str] = []
        for chunk_id in ranked_ids:
            row = row_map.get(chunk_id)
            if row is None:
                continue
            _, document, version = row
            if (
                payload.filters.document_ids
                and document.id not in payload.filters.document_ids
            ):
                continue
            if (
                payload.filters.source_formats
                and version.format not in payload.filters.source_formats
            ):
                continue
            published_at = version.published_at
            if (
                payload.filters.published_after is not None
                and (
                    published_at is None
                    or published_at < payload.filters.published_after
                )
            ):
                continue
            if (
                payload.filters.published_before is not None
                and (
                    published_at is None
                    or published_at > payload.filters.published_before
                )
            ):
                continue
            filtered_ids.append(chunk_id)
            if len(filtered_ids) >= payload.candidate_k:
                break
        query_fusion_scores = dict(fused)
        query_fusion_ranks = {
            chunk_id: rank for rank, chunk_id in enumerate(filtered_ids, start=1)
        }
        rerank_scores_by_id: dict[str, float] = {}
        rerank_ranks_by_id: dict[str, int] = {}
        if use_rerank and reranker is not None and filtered_ids:
            rerank_scores = await reranker.rerank(
                payload.query,
                [row_map[chunk_id][0].content for chunk_id in filtered_ids[:30]],
            )
            for chunk_id, score in zip(
                filtered_ids[:30], rerank_scores, strict=True
            ):
                fused[chunk_id] = 0.4 * fused[chunk_id] + 0.6 * score
                rerank_scores_by_id[chunk_id] = score
            rerank_ranks_by_id = {
                chunk_id: rank
                for rank, chunk_id in enumerate(
                    sorted(
                        rerank_scores_by_id,
                        key=lambda value: (-rerank_scores_by_id[value], value),
                    ),
                    start=1,
                )
            }
            filtered_ids.sort(key=lambda value: (-fused[value], value))
        scored_hits: list[ScoredHit] = []
        for final_rank, chunk_id in enumerate(filtered_ids[: payload.top_k], start=1):
            chunk = row_map[chunk_id][0]
            contexts = (
                await self.repo.context_chunks(
                    self.workspace_id,
                    chunk.document_version_id,
                    chunk.parent_chunk_id,
                    chunk.ordinal,
                )
                if payload.expand_context
                else []
            )
            scored_hits.append(
                ScoredHit(
                    chunk=_knowledge_chunk(chunk),
                    score=fused[chunk_id],
                    scores={
                        "dense": raw_scores.get(chunk_id, 0.0),
                        "total": fused[chunk_id],
                        **(
                            {"rerank": rerank_scores_by_id[chunk_id]}
                            if chunk_id in rerank_scores_by_id
                            else {}
                        ),
                    },
                    score_details=self._milvus_score_details(
                        payload=payload,
                        source_observations=tuple(
                            source_observations.get(chunk_id, ())
                        ),
                        query_contributions=tuple(
                            query_contributions.get(chunk_id, ())
                        ),
                        query_fusion_score=query_fusion_scores[chunk_id],
                        query_fusion_rank=query_fusion_ranks[chunk_id],
                        rerank_score=rerank_scores_by_id.get(chunk_id),
                        rerank_rank=rerank_ranks_by_id.get(chunk_id),
                        final_score=fused[chunk_id],
                        final_rank=final_rank,
                    ),
                    context=tuple(
                        _knowledge_chunk(context) for context in contexts
                    ),
                )
            )
        enabled = [f"MILVUS_{payload.retrieval_mode}"]
        if payload.retrieval_mode == "HYBRID":
            enabled.append(payload.fusion)
        if use_rerank:
            enabled.append("RERANK")
        if len(queries) > 1:
            enabled.append("QUERY_REWRITE")
        if payload.expand_context:
            enabled.append("CONTEXT_EXPANSION")
        selected_rows = [
            row_map[hit.chunk.chunk_id]
            for hit in scored_hits
            if hit.chunk.chunk_id in row_map
        ]
        return (
            RetrievalResult(
                hits=tuple(scored_hits),
                queries=tuple(queries),
                enabled_components=tuple(enabled),
                provider_mode=self.settings.rag_provider_mode,
                scoring=RetrievalScoring(
                    requested_retrieval_mode=payload.retrieval_mode,
                    effective_retrieval_mode=payload.retrieval_mode,
                    query_count=len(queries),
                    channel_fusion=FusionScoring(
                        method=payload.fusion if payload.retrieval_mode == "HYBRID" else None,
                        applied=payload.retrieval_mode == "HYBRID",
                        rrf_k=(
                            60
                            if payload.retrieval_mode == "HYBRID"
                            and payload.fusion == "RRF"
                            else None
                        ),
                    ),
                    query_fusion=FusionScoring(
                        method="RRF",
                        applied=True,
                        rrf_k=60,
                    ),
                    rerank_applied=bool(rerank_scores_by_id),
                ),
            ),
            selected_rows,
        )

    @staticmethod
    def _milvus_score_details(
        *,
        payload: SearchRequest,
        source_observations: tuple[ScoreContribution, ...],
        query_contributions: tuple[ScoreContribution, ...],
        query_fusion_score: float,
        query_fusion_rank: int,
        rerank_score: float | None,
        rerank_rank: int | None,
        final_score: float,
        final_rank: int,
    ) -> ScoreDetails:
        source_value = max(
            (observation.value for observation in source_observations),
            default=None,
        )
        source_rank = min(
            (
                observation.rank
                for observation in source_observations
                if observation.rank is not None
            ),
            default=None,
        )
        unavailable = ScoreStage(availability=ScoreAvailability.NOT_RUN)
        not_exposed = ScoreStage(availability=ScoreAvailability.NOT_EXPOSED)
        not_applicable = ScoreStage(availability=ScoreAvailability.NOT_APPLICABLE)
        dense = unavailable
        bm25 = unavailable
        channel_fusion = not_applicable
        source_stage = ScoreStage(
            availability=ScoreAvailability.AVAILABLE,
            value=source_value,
            rank=source_rank,
            method=(
                "COSINE"
                if payload.retrieval_mode == "DENSE"
                else "BM25"
            ),
            per_query=source_observations,
        )
        if payload.retrieval_mode == "DENSE":
            dense = source_stage
        elif payload.retrieval_mode == "SPARSE":
            bm25 = source_stage
        else:
            dense = not_exposed
            bm25 = not_exposed
            channel_fusion = ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=source_value,
                rank=source_rank,
                method=payload.fusion,
                rrf_k=60 if payload.fusion == "RRF" else None,
                per_query=source_observations,
            )
        rerank = (
            ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=rerank_score,
                rank=rerank_rank,
                method="RERANKER",
            )
            if rerank_score is not None
            else unavailable
        )
        return ScoreDetails(
            dense_similarity=dense,
            bm25=bm25,
            channel_fusion=channel_fusion,
            query_fusion=ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=query_fusion_score,
                rank=query_fusion_rank,
                method="RRF",
                rrf_k=60,
                per_query=query_contributions,
                contributions=query_contributions,
            ),
            rerank=rerank,
            final=ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=final_score,
                rank=final_rank,
                method="WEIGHTED_SUM" if rerank_score is not None else "IDENTITY",
            ),
        )

    async def search(self, payload: SearchRequest) -> SearchResponse:
        await self.access.require(self.workspace_id)
        started = time.perf_counter()
        if (
            self.settings.milvus_uri
            and payload.retrieval_mode in {"DENSE", "SPARSE", "HYBRID"}
            and self.settings.rag_provider_mode != "DISABLED"
        ):
            embedding = _embedding(self.settings)
            reranker = _reranker(self.settings)
            llm = _llm(self.settings)
            if payload.rerank == "ON" and reranker is None:
                raise AppError(
                    "reranker_dependency_unavailable", "Reranker 未配置", 503
                )
            if payload.rewrite == "ON" and llm is None:
                raise AppError(
                    "llm_dependency_unavailable", "Query Rewrite LLM 未配置", 503
                )
            use_rerank = payload.rerank == "ON" or (
                payload.rerank == "AUTO" and reranker is not None
            )
            use_rewrite = payload.rewrite == "ON"
            retrieval_started = time.perf_counter()
            try:
                result, rows = await self._milvus_retrieve(
                    payload,
                    embedding,
                    reranker,
                    llm,
                    use_rerank=use_rerank,
                    use_rewrite=use_rewrite,
                )
            except ProviderUnavailableError as exc:
                raise AppError(
                    "model_provider_unavailable",
                    "The configured model provider is temporarily unavailable",
                    503,
                ) from exc
            retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
            evidence = EvidenceGate(
                EvidenceGateConfig(
                    min_top_score=self.settings.rag_evidence_min_score,
                    min_lexical_coverage=self.settings.rag_evidence_min_lexical_coverage,
                )
            ).evaluate(
                payload.query,
                result.hits,
                query_count=len(result.queries),
                enforce_lexical_coverage=payload.response_mode == "HITS",
            )
            row_by_chunk = {str(row[0].id): row for row in rows}
            span_loader = getattr(self.repo, "source_spans_by_chunk_ids", None)
            span_map = (
                await span_loader(
                    self.workspace_id,
                    [row[0].id for row in rows],
                )
                if span_loader is not None
                else {}
            )
            milvus_hits = [
                SearchHit(
                    chunk_id=row_by_chunk[hit.chunk.chunk_id][0].id,
                    document_id=row_by_chunk[hit.chunk.chunk_id][0].document_id,
                    document_version_id=row_by_chunk[hit.chunk.chunk_id][
                        0
                    ].document_version_id,
                    title=row_by_chunk[hit.chunk.chunk_id][1].title,
                    page=row_by_chunk[hit.chunk.chunk_id][0].page,
                    heading_path=list(
                        row_by_chunk[hit.chunk.chunk_id][0].heading_path
                    ),
                    quote=row_by_chunk[hit.chunk.chunk_id][0].content[:360],
                    source_span_id=_primary_source_span_id(
                        span_map.get(row_by_chunk[hit.chunk.chunk_id][0].id, []),
                        row_by_chunk[hit.chunk.chunk_id][0].source_span_id,
                    ),
                    source_spans=_source_span_summaries(
                        span_map.get(row_by_chunk[hit.chunk.chunk_id][0].id, [])
                    ),
                    score=hit.score,
                    scores={
                        "dense": hit.scores.get("dense"),
                        "sparse": hit.scores.get("sparse"),
                        "fusion": hit.scores.get("total", hit.score),
                        "rerank": hit.scores.get("rerank"),
                    },
                    score_details=hit.score_details,
                    context="\n\n".join(item.text for item in hit.context)[:10000],
                )
                for hit in result.hits
                if hit.chunk.chunk_id in row_by_chunk
            ]
            refusal = (
                None
                if evidence.accepted
                else {
                    "code": evidence.code,
                    "message": "当前检索证据不足，已拒绝生成答案",
                }
            )
            answerability = _precheck_answerability(
                evidence,
                response_mode=payload.response_mode,
                visible_hit_ids=tuple(str(hit.chunk_id) for hit in milvus_hits),
            )
            milvus_answer: str | None = None
            if payload.response_mode == "ANSWER" and evidence.accepted:
                if llm is None:
                    raise AppError(
                        "llm_dependency_unavailable", "答案 LLM 未配置", 503
                    )
                try:
                    generated = await llm.answer(
                        payload.query,
                        [
                            EvidenceItem(hit_id=hit.chunk.chunk_id, text=hit.chunk.text)
                            for hit in result.hits
                        ],
                    )
                    if generated.status not in {"answered", "refused"}:
                        raise ProviderUnavailableError(
                            "answer provider returned an invalid status"
                        )
                except ProviderUnavailableError as exc:
                    raise AppError(
                        "llm_dependency_unavailable",
                        "The answer model provider is temporarily unavailable",
                        503,
                    ) from exc
                if generated.status == "answered":
                    milvus_answer = generated.text
                    answerability = _model_answerability(
                        evidence,
                        answered=True,
                        supporting_hit_ids=generated.cited_hit_ids,
                        visible_hit_ids=tuple(str(hit.chunk_id) for hit in milvus_hits),
                    )
                elif generated.status == "refused":
                    refusal = {
                        "code": "model_refused",
                        "message": generated.text or "模型判定证据不足",
                    }
                    answerability = _model_answerability(
                        evidence,
                        answered=False,
                        visible_hit_ids=tuple(str(hit.chunk_id) for hit in milvus_hits),
                    )
            total_ms = (time.perf_counter() - started) * 1000
            return SearchResponse(
                query=payload.query,
                response_mode=payload.response_mode,
                answer=milvus_answer,
                refusal=refusal,
                hits=milvus_hits,
                provider={
                    "mode": self.settings.rag_provider_mode,
                    "embedding": embedding.profile,
                    "reranker": (
                        reranker.profile if use_rerank and reranker else "disabled"
                    ),
                    "llm": llm.profile if llm else "disabled",
                    "profile": embedding.profile,
                },
                scoring=result.scoring,
                query_trace=_query_trace(payload, result.queries),
                answerability=answerability,
                components=list(result.enabled_components),
                timings_ms={
                    "database": 0.0,
                    "retrieval": retrieval_ms,
                    "total": total_ms,
                },
                warnings=list(result.warnings),
                degraded=result.degraded,
            )
        rows = await self.repo.published_chunks(
            self.workspace_id,
            document_ids=payload.filters.document_ids,
            formats=list(payload.filters.source_formats),
            published_from=payload.filters.published_after,
            published_to=payload.filters.published_before,
        )
        database_ms = (time.perf_counter() - started) * 1000
        if not rows:
            empty_evidence = EvidenceGate(
                EvidenceGateConfig(
                    min_top_score=self.settings.rag_evidence_min_score,
                    min_lexical_coverage=self.settings.rag_evidence_min_lexical_coverage,
                )
            ).evaluate(
                payload.query,
                (),
                enforce_lexical_coverage=payload.response_mode == "HITS",
            )
            return SearchResponse(
                query=payload.query,
                response_mode=payload.response_mode,
                refusal={"code": "insufficient_evidence", "message": "未检索到可用证据"},
                hits=[],
                provider={
                    "mode": self.settings.rag_provider_mode,
                    "embedding": "disabled",
                    "reranker": "disabled",
                    "llm": "disabled",
                    "profile": "none",
                },
                scoring=RetrievalScoring(
                    requested_retrieval_mode=payload.retrieval_mode,
                    effective_retrieval_mode=payload.retrieval_mode,
                    query_count=1,
                    channel_fusion=FusionScoring(
                        method=None,
                        applied=False,
                    ),
                    query_fusion=FusionScoring(
                        method=None,
                        applied=False,
                    ),
                    rerank_applied=False,
                ),
                query_trace=_query_trace(payload, None),
                answerability=_precheck_answerability(
                    empty_evidence,
                    response_mode=payload.response_mode,
                    visible_hit_ids=(),
                ),
                components=[],
                timings_ms={"database": database_ms, "retrieval": 0.0, "total": database_ms},
                warnings=[],
                degraded=False,
            )
        embedding = _embedding(self.settings)
        if (
            payload.retrieval_mode in {"DENSE", "HYBRID"}
            and self.settings.rag_provider_mode == "DISABLED"
        ):
            if payload.retrieval_mode == "DENSE":
                raise AppError(
                    "embedding_dependency_unavailable", "Dense Embedding 未配置", 503
                )
            effective_mode = RetrievalMode.SPARSE
            degraded = True
            base_warnings = ["Embedding 未配置，HYBRID 已降级为 SPARSE"]
        else:
            effective_mode = RetrievalMode(payload.retrieval_mode)
            degraded = False
            base_warnings = []
        knowledge_chunks = [_knowledge_chunk(row[0]) for row in rows]
        if self.settings.rag_provider_mode == "DISABLED":
            vectors = [[0.0] * 128 for _ in knowledge_chunks]
        else:
            try:
                vectors = await embedding.embed_documents(
                    [chunk.text for chunk in knowledge_chunks]
                )
            except ProviderUnavailableError as exc:
                raise AppError(
                    "model_provider_unavailable",
                    "The configured model provider is temporarily unavailable",
                    503,
                ) from exc
        indexed = [
            IndexedChunk(
                chunk=chunk,
                workspace_id=str(self.workspace_id),
                vector=tuple(vector),
                metadata={"title": document.title, "format": version.format},
            )
            for chunk, vector, (_, document, version) in zip(
                knowledge_chunks, vectors, rows, strict=True
            )
        ]
        reranker = _reranker(self.settings)
        llm = _llm(self.settings)
        if payload.rerank == "ON" and reranker is None:
            raise AppError("reranker_dependency_unavailable", "Reranker 未配置", 503)
        if payload.rewrite == "ON" and llm is None:
            raise AppError("llm_dependency_unavailable", "Query Rewrite LLM 未配置", 503)
        use_rerank = payload.rerank == "ON" or (
            payload.rerank == "AUTO" and reranker is not None
        )
        use_rewrite = payload.rewrite == "ON"
        retriever = HybridRetriever(
            chunks=indexed,
            embedding=embedding,
            reranker=reranker if use_rerank else None,
            llm=llm if use_rewrite else None,
        )
        retrieval_started = time.perf_counter()
        try:
            result = await retriever.search(
                payload.query,
                RetrievalConfig(
                    mode=effective_mode,
                    fusion=FusionMode(payload.fusion),
                    top_k=payload.top_k,
                    candidate_k=payload.candidate_k,
                    dense_weight=payload.dense_weight,
                    sparse_weight=1 - payload.dense_weight,
                    rerank=use_rerank,
                    rewrite=use_rewrite,
                    multi_query=payload.multi_query,
                    expand_context=payload.expand_context,
                ),
            )
        except ProviderUnavailableError as exc:
            raise AppError(
                "model_provider_unavailable",
                "The configured model provider is temporarily unavailable",
                503,
            ) from exc
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        evidence = EvidenceGate(
            EvidenceGateConfig(
                min_top_score=self.settings.rag_evidence_min_score,
                min_lexical_coverage=self.settings.rag_evidence_min_lexical_coverage,
            )
        ).evaluate(
            payload.query,
            result.hits,
            query_count=len(result.queries),
            enforce_lexical_coverage=payload.response_mode == "HITS",
        )
        row_by_chunk = {str(row[0].id): row for row in rows}
        span_loader = getattr(self.repo, "source_spans_by_chunk_ids", None)
        span_map = (
            await span_loader(
                self.workspace_id,
                [row[0].id for row in rows],
            )
            if span_loader is not None
            else {}
        )
        hits: list[SearchHit] = []
        for hit in result.hits:
            row = row_by_chunk.get(hit.chunk.chunk_id)
            if row is None:
                continue
            chunk, document, _ = row
            # This re-check is intentional: vector candidates are never trusted as facts.
            if document.published_version_id != chunk.document_version_id:
                continue
            score_details = hit.score_details
            assert score_details is not None
            hits.append(
                SearchHit(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    title=document.title,
                    page=chunk.page,
                    heading_path=list(chunk.heading_path),
                    quote=chunk.content[:360],
                    source_span_id=_primary_source_span_id(
                        span_map.get(chunk.id, []), chunk.source_span_id
                    ),
                    source_spans=_source_span_summaries(span_map.get(chunk.id, [])),
                    score=hit.score,
                    scores={
                        "dense": hit.scores.get("dense"),
                        "sparse": hit.scores.get("sparse"),
                        "fusion": hit.scores.get("total", hit.score),
                        "rerank": hit.scores.get("rerank"),
                    },
                    score_details=replace(
                        score_details,
                        final=replace(
                            score_details.final,
                            value=hit.score,
                            rank=len(hits) + 1,
                        ),
                    ),
                    context="\n\n".join(item.text for item in hit.context)[:10000],
                )
            )
        total_ms = (time.perf_counter() - started) * 1000
        warnings = [*base_warnings, *result.warnings]
        result_scoring = result.scoring
        assert result_scoring is not None
        answer: str | None = None
        refusal = (
            None
            if evidence.accepted
            else {
                "code": evidence.code,
                "message": "当前检索证据不足，已拒绝生成答案",
            }
        )
        answerability = _precheck_answerability(
            evidence,
            response_mode=payload.response_mode,
            visible_hit_ids=tuple(str(hit.chunk_id) for hit in hits),
        )
        if payload.response_mode == "ANSWER" and evidence.accepted:
            if llm is None or self.settings.rag_provider_mode == "FIXTURE":
                raise AppError(
                    "llm_dependency_unavailable",
                    "当前未配置可生成带引用答案的真实 LLM；请使用 HITS 模式",
                    503,
                )
            allowed = set(evidence.usable_hit_ids)
            try:
                generated = await llm.answer(
                    payload.query,
                    [
                        EvidenceItem(hit_id=hit.chunk.chunk_id, text=hit.chunk.text)
                        for hit in result.hits
                        if hit.chunk.chunk_id in allowed
                    ],
                )
                if generated.status not in {"answered", "refused"}:
                    raise ProviderUnavailableError(
                        "answer provider returned an invalid status"
                    )
            except ProviderUnavailableError as exc:
                raise AppError(
                    "llm_dependency_unavailable",
                    "The answer model provider is temporarily unavailable",
                    503,
                ) from exc
            if generated.status == "refused":
                refusal = {
                    "code": "model_refused",
                    "message": generated.text or "模型判定证据不足",
                }
                answerability = _model_answerability(
                    evidence,
                    answered=False,
                    visible_hit_ids=tuple(str(hit.chunk_id) for hit in hits),
                )
            elif generated.status == "answered":
                answer = generated.text
                answerability = _model_answerability(
                    evidence,
                    answered=True,
                    supporting_hit_ids=generated.cited_hit_ids,
                    visible_hit_ids=tuple(str(hit.chunk_id) for hit in hits),
                )
        return SearchResponse(
            query=payload.query,
            response_mode=payload.response_mode,
            answer=answer,
            refusal=refusal,
            hits=hits,
            provider={
                "mode": self.settings.rag_provider_mode,
                "embedding": embedding.profile,
                "reranker": reranker.profile if use_rerank and reranker else "disabled",
                "llm": llm.profile if llm else "disabled",
                "profile": embedding.profile,
            },
            scoring=RetrievalScoring(
                requested_retrieval_mode=payload.retrieval_mode,
                effective_retrieval_mode=effective_mode.value,
                query_count=len(result.queries),
                channel_fusion=result_scoring.channel_fusion,
                query_fusion=result_scoring.query_fusion,
                rerank_applied=result_scoring.rerank_applied,
            ),
            query_trace=_query_trace(payload, result.queries),
            answerability=answerability,
            components=list(result.enabled_components),
            timings_ms={
                "database": database_ms,
                "retrieval": retrieval_ms,
                "total": total_ms,
            },
            warnings=warnings,
            degraded=degraded or result.degraded,
        )


class KnowledgeWorker:
    """MySQL-leased Outbox/compensation consumer used by the worker process."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def drain_once(self) -> bool:
        now = utc_now()
        event = await self.session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.available_at <= now,
                or_(
                    OutboxEvent.status.in_(["PENDING", "RETRY_WAIT"]),
                    (
                        (OutboxEvent.status == "PROCESSING")
                        & (OutboxEvent.lease_expires_at < now)
                    ),
                ),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is not None:
            version = await self.session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.workspace_id == event.workspace_id,
                    DocumentVersion.id == event.aggregate_id,
                )
            )
            if version is None:
                event.status = "DEAD"
                event.last_error_code = "resource_not_found"
                await self.session.commit()
                return True
            service = KnowledgeService(
                self.session,
                version.created_by_user_id,
                event.workspace_id,
                self.settings,
            )
            await service.process_outbox_event(event.id)
            return True

        task = await self.session.scalar(
            select(CompensationTask)
            .where(
                or_(
                    CompensationTask.status.in_(["PENDING", "RETRY_WAIT"]),
                    (
                        (CompensationTask.status == "PROCESSING")
                        & (CompensationTask.lease_expires_at < now)
                    ),
                )
            )
            .order_by(CompensationTask.created_at, CompensationTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if task is None:
            return False
        version = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.workspace_id == task.workspace_id,
                DocumentVersion.id == task.resource_id,
            )
        )
        if version is None:
            task.status = "FAILED"
            task.error_message = "资源不存在"
            await self.session.commit()
            return True
        service = KnowledgeService(
            self.session,
            version.created_by_user_id,
            task.workspace_id,
            self.settings,
        )
        await service.process_compensation_task(task.id)
        return True

    async def drain(self, *, limit: int = 100) -> int:
        processed = 0
        while processed < limit and await self.drain_once():
            processed += 1
        return processed


def _format_from_name(filename: str) -> str:
    extension = PurePath(filename).suffix.lower()
    return {
        ".pdf": "PDF",
        ".docx": "DOCX",
        ".pptx": "PPTX",
        ".md": "MARKDOWN",
        ".markdown": "MARKDOWN",
        ".txt": "TXT",
    }.get(extension, "UNSUPPORTED")


def _profile_hash(profile: str) -> str:
    return hashlib.sha256(profile.encode()).hexdigest()[:16]


def _canonical_uuid_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _query_trace(
    payload: SearchRequest,
    queries: Sequence[str] | None,
) -> dict[str, Any]:
    if not payload.include_query_trace:
        return {
            "exposed": False,
            "items": [],
            "redaction_reason": "NOT_REQUESTED",
        }
    if queries is None:
        return {
            "exposed": False,
            "items": [],
            "redaction_reason": "RETRIEVAL_NOT_RUN",
        }
    return {
        "exposed": True,
        "items": [
            {
                "index": index,
                "kind": "ORIGINAL" if index == 0 else "REWRITE",
                "text": query,
            }
            for index, query in enumerate(queries)
        ],
        "redaction_reason": None,
    }


def _precheck_answerability(
    evidence: EvidenceDecision,
    *,
    response_mode: str,
    visible_hit_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    visible = set(visible_hit_ids) if visible_hit_ids is not None else None
    checked_hit_ids = [
        item
        for item in evidence.usable_hit_ids
        if visible is None or item in visible
    ]
    diagnostics = _answerability_diagnostics(evidence)
    if response_mode == "HITS":
        return {
            "status": "NOT_EVALUATED",
            "method": "NOT_RUN",
            "reason_code": "HITS_MODE",
            "precheck_code": evidence.code,
            "provider_called": False,
            **diagnostics,
            "checked_hit_ids": checked_hit_ids,
            "supporting_hit_ids": [],
        }
    if not evidence.accepted:
        return {
            "status": "REJECTED",
            "method": "HEURISTIC_GATE",
            "reason_code": evidence.code,
            "precheck_code": evidence.code,
            "provider_called": False,
            **diagnostics,
            "checked_hit_ids": checked_hit_ids,
            "supporting_hit_ids": [],
        }
    return {
        "status": "NOT_EVALUATED",
        "method": "NOT_RUN",
        "reason_code": "AWAITING_GROUNDED_ANSWER",
        "precheck_code": evidence.code,
        "provider_called": False,
        **diagnostics,
        "checked_hit_ids": checked_hit_ids,
        "supporting_hit_ids": [],
    }


def _model_answerability(
    evidence: EvidenceDecision,
    *,
    answered: bool,
    supporting_hit_ids: Sequence[str] = (),
    visible_hit_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    visible = set(visible_hit_ids) if visible_hit_ids is not None else None
    checked = tuple(
        item
        for item in evidence.usable_hit_ids
        if visible is None or item in visible
    )
    allowed = set(checked)
    supporting = list(dict.fromkeys(item for item in supporting_hit_ids if item in allowed))
    return {
        "status": "SUPPORTED" if answered else "REJECTED",
        "method": "GROUNDED_ANSWER_LLM",
        "reason_code": "model_answered" if answered else "model_refused",
        "precheck_code": evidence.code,
        "provider_called": True,
        **_answerability_diagnostics(evidence),
        "checked_hit_ids": list(checked),
        "supporting_hit_ids": supporting,
    }


def _answerability_diagnostics(evidence: EvidenceDecision) -> dict[str, Any]:
    return {
        "raw_top_score": evidence.top_score,
        "gate_score": evidence.gate_score,
        "score_threshold": evidence.score_threshold,
        "lexical_coverage": evidence.lexical_coverage,
        "lexical_threshold": evidence.lexical_threshold,
        "lexical_passed": evidence.lexical_passed,
    }


def _chunk_row(
    workspace_id: UUID, chunk: KnowledgeChunk, parent_mapping: dict[str, UUID]
) -> DocumentChunk:
    return DocumentChunk(
        id=UUID(chunk.chunk_id),
        workspace_id=workspace_id,
        document_id=UUID(chunk.document_id),
        document_version_id=UUID(chunk.document_version_id),
        parent_chunk_id=parent_mapping.get(chunk.parent_id or ""),
        kind=chunk.kind.value.upper(),
        ordinal=chunk.ordinal,
        heading_path=list(chunk.heading_path),
        page=chunk.page,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        source_span_id=chunk.source_span_id,
        content_sha256=hashlib.sha256(chunk.text.encode()).hexdigest(),
        content=chunk.text,
        indexed=False,
    )


def _chunk_source_span_rows(
    workspace_id: UUID,
    version: DocumentVersion,
    blocks: Sequence[SourceBlock],
    chunks: Sequence[KnowledgeChunk],
) -> list[ChunkSourceSpan]:
    """Resolve child chunks back to immutable layout blocks.

    A chunk may cross pages or slides, so each overlapping block is persisted as
    an independent span. Parent chunks are context containers and are not indexed.
    """

    rows: list[ChunkSourceSpan] = []
    for chunk in chunks:
        if chunk.kind is not ChunkKind.CHILD:
            continue
        ordinal = 0
        for block_index, block in enumerate(blocks):
            overlap_start = max(chunk.char_start, block.char_start)
            overlap_end = min(chunk.char_end, block.char_end)
            if overlap_end <= overlap_start:
                continue
            local_start = overlap_start - block.char_start
            local_end = overlap_end - block.char_start
            raw_excerpt = block.text[local_start:local_end]
            leading = len(raw_excerpt) - len(raw_excerpt.lstrip())
            trailing = len(raw_excerpt) - len(raw_excerpt.rstrip())
            local_start += leading
            local_end -= trailing
            excerpt = block.text[local_start:local_end]
            if not excerpt:
                continue
            excerpt_sha = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
            block_ref = block.block_key or hashlib.sha256(
                (
                    f"{version.id}:{block_index}:{block.page or 0}:"
                    f"{block.slide or 0}:{block.char_start}:{block.char_end}"
                ).encode()
            ).hexdigest()
            source_span_id = hashlib.sha256(
                (
                    f"{version.id}:{block_ref}:{local_start}:{local_end}:"
                    f"{excerpt_sha}"
                ).encode()
            ).hexdigest()
            rows.append(
                ChunkSourceSpan(
                    workspace_id=workspace_id,
                    document_id=version.document_id,
                    document_version_id=version.id,
                    chunk_id=UUID(chunk.chunk_id),
                    primary_source_span_id=chunk.source_span_id,
                    source_span_id=source_span_id,
                    span_ordinal=ordinal,
                    block_ref=block_ref,
                    page_no=block.page,
                    slide_no=block.slide,
                    bbox_json=block.bbox.to_dict() if block.bbox else None,
                    block_char_start=local_start,
                    block_char_end=local_end,
                    excerpt_text=excerpt,
                    excerpt_sha256=excerpt_sha,
                )
            )
            ordinal += 1
    return rows


def _source_span_summaries(
    spans: Sequence[ChunkSourceSpan],
) -> list[SourceSpanSummary]:
    return [
        SourceSpanSummary(
            source_span_id=span.source_span_id,
            page_no=span.page_no,
            slide_no=span.slide_no,
            bbox=span.bbox_json,
            excerpt=span.excerpt_text,
            excerpt_sha256=span.excerpt_sha256,
        )
        for span in spans
    ]


def _primary_source_span_id(
    spans: Sequence[ChunkSourceSpan], fallback: str
) -> str:
    """Use the first resolvable V2 span while retaining V1 compatibility."""

    return spans[0].source_span_id if spans else fallback


def _knowledge_chunk(chunk: DocumentChunk) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        document_version_id=str(chunk.document_version_id),
        kind=ChunkKind(chunk.kind.lower()),
        ordinal=chunk.ordinal,
        text=chunk.content,
        page=chunk.page,
        heading_path=tuple(chunk.heading_path),
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        source_span_id=chunk.source_span_id,
        parent_id=str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
    )
