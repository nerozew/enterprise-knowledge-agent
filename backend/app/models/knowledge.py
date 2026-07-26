from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class DocumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class DocumentVersionStatus(StrEnum):
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class IngestionStatus(StrEnum):
    QUEUED = "QUEUED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    STORING = "STORING"
    INDEXING = "INDEXING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    COMPENSATING = "COMPENSATING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_documents_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "id", "published_version_id"],
            [
                "document_versions.workspace_id",
                "document_versions.document_id",
                "document_versions.id",
            ],
            name="fk_documents_published_version",
            use_alter=True,
        ),
        Index("ix_documents_workspace_status_updated", "workspace_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(
        String(16), default=DocumentStatus.ACTIVE.value, server_default="ACTIVE"
    )
    published_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_document_versions_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "document_id",
            "id",
            name="uq_document_versions_workspace_document_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_document_versions_workspace_document",
        ),
        Index(
            "ix_document_versions_workspace_document_created",
            "workspace_id",
            "document_id",
            "created_at",
        ),
        Index(
            "ix_document_versions_workspace_hash",
            "workspace_id",
            "file_sha256",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    format: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str] = mapped_column(String(128))
    file_name: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(768))
    status: Mapped[str] = mapped_column(
        String(24), default=DocumentVersionStatus.PUBLISHING.value
    )
    parser_profile: Mapped[str] = mapped_column(
        String(64), default="structure-v1", server_default="structure-v1"
    )
    chunk_profile: Mapped[str] = mapped_column(
        String(64), default="parent-child-v1", server_default="parent-child-v1"
    )
    index_profile: Mapped[str] = mapped_column(
        String(128), default="fixture-hash-v1", server_default="fixture-hash-v1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_ingestion_jobs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_ingestion_jobs_workspace_document",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            ondelete="CASCADE",
            name="fk_ingestion_jobs_workspace_version",
        ),
        Index("ix_ingestion_jobs_workspace_status_updated", "workspace_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    document_version_id: Mapped[UUID] = mapped_column()
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    previous_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default=IngestionStatus.QUEUED.value, server_default="QUEUED"
    )
    stage: Mapped[str] = mapped_column(String(24), default="QUEUED", server_default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class IngestionJobAttempt(Base):
    __tablename__ = "ingestion_job_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["ingestion_jobs.workspace_id", "ingestion_jobs.id"],
            ondelete="CASCADE",
            name="fk_ingestion_attempts_workspace_job",
        ),
        UniqueConstraint("job_id", "attempt_no", name="uq_ingestion_attempt_job_no"),
        Index("ix_ingestion_attempts_workspace_job", "workspace_id", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    attempt_no: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id"],
            [
                "document_versions.workspace_id",
                "document_versions.document_id",
                "document_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_document_chunks_workspace_document_version",
        ),
        UniqueConstraint(
            "document_version_id", "source_span_id", "kind", name="uq_chunks_version_span_kind"
        ),
        Index(
            "ix_chunks_workspace_version_kind_order",
            "workspace_id",
            "document_version_id",
            "kind",
            "ordinal",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    document_version_id: Mapped[UUID] = mapped_column()
    parent_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(12))
    ordinal: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    source_span_id: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_hash", name="uq_outbox_workspace_dedupe"),
        Index("ix_outbox_status_available", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(80))
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[UUID] = mapped_column()
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(String(768), nullable=True)
    config_version: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompensationTask(Base):
    __tablename__ = "compensation_tasks"
    __table_args__ = (
        Index("ix_compensation_workspace_status", "workspace_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    task_type: Mapped[str] = mapped_column(String(48))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID] = mapped_column()
    object_key: Mapped[str | None] = mapped_column(String(768), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
