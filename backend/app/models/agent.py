from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AgentRunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TodoStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AgentDispatchStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conversations_workspace_id"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="conversation_status"),
        CheckConstraint("version >= 1", name="conversation_version"),
        Index("ix_conversations_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ConversationStatus.ACTIVE.value, server_default="ACTIVE"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_agent_runs_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "id",
            "checkpoint_ns",
            "checkpoint_id",
            name="uq_agent_runs_workspace_checkpoint",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_runs_workspace_conversation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id", "checkpoint_ns", "checkpoint_id"],
            [
                "agent_checkpoints.workspace_id",
                "agent_checkpoints.thread_id",
                "agent_checkpoints.checkpoint_ns",
                "agent_checkpoints.checkpoint_id",
            ],
            ondelete="RESTRICT",
            name="fk_agent_runs_workspace_checkpoint",
        ),
        CheckConstraint(
            "status IN ('CREATED','RUNNING','WAITING_APPROVAL','COMPLETED','FAILED','CANCELLED')",
            name="agent_run_status",
        ),
        CheckConstraint(
            "route IS NULL OR route IN "
            "('DIRECT_ANSWER','KNOWLEDGE_QA','INSPIRATION_SEARCH','TODO_CREATE')",
            name="agent_run_route",
        ),
        CheckConstraint("version >= 1", name="agent_run_version"),
        CheckConstraint(
            "(checkpoint_ns IS NULL AND checkpoint_id IS NULL) OR "
            "(checkpoint_ns IS NOT NULL AND checkpoint_id IS NOT NULL)",
            name="agent_run_checkpoint_complete",
        ),
        Index("ix_agent_runs_workspace_status_updated", "workspace_id", "status", "updated_at"),
        Index("ix_agent_runs_workspace_conversation", "workspace_id", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    conversation_id: Mapped[UUID] = mapped_column()
    requested_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(
        String(24), default=AgentRunStatus.CREATED.value, server_default="CREATED"
    )
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    graph_version: Mapped[str] = mapped_column(String(80))
    checkpoint_ns: Mapped[str | None] = mapped_column(String(160), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_text: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentMessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_messages_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "sequence_no",
            name="uq_messages_workspace_conversation_seq",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_messages_workspace_conversation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_messages_workspace_run",
        ),
        CheckConstraint("role IN ('SYSTEM','USER','ASSISTANT','TOOL')", name="message_role"),
        CheckConstraint("sequence_no >= 1", name="message_sequence"),
        CheckConstraint("version >= 1", name="message_version"),
        Index(
            "ix_messages_workspace_conversation", "workspace_id", "conversation_id", "sequence_no"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    conversation_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    sequence_no: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRunEvent(Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_agent_run_events_workspace_id"),
        UniqueConstraint(
            "workspace_id", "run_id", "sequence_no", name="uq_agent_run_events_run_seq"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_run_events_workspace_run",
        ),
        CheckConstraint("sequence_no >= 1", name="agent_run_event_sequence"),
        Index("ix_agent_run_events_workspace_run", "workspace_id", "run_id", "sequence_no"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRunDispatch(Base):
    __tablename__ = "agent_run_dispatches"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uq_agent_dispatch_workspace_run"),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_dispatch_workspace_run",
        ),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','SUCCEEDED','FAILED')",
            name="agent_dispatch_status",
        ),
        CheckConstraint("attempts >= 0", name="agent_dispatch_attempts"),
        Index(
            "ix_agent_dispatch_due",
            "status",
            "available_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(16), default=AgentDispatchStatus.PENDING.value, server_default="PENDING"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_approval_requests_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "tool_call_id",
            name="uq_approval_workspace_run_tool_call",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "run_id",
            "tool_call_id",
            name="uq_approval_workspace_id_run_tool",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_approval_requests_workspace_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id", "checkpoint_ns", "checkpoint_id"],
            [
                "agent_runs.workspace_id",
                "agent_runs.id",
                "agent_runs.checkpoint_ns",
                "agent_runs.checkpoint_id",
            ],
            ondelete="RESTRICT",
            name="fk_approval_requests_run_checkpoint",
        ),
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','EXPIRED')",
            name="approval_request_status",
        ),
        CheckConstraint("version >= 1", name="approval_request_version"),
        CheckConstraint(
            "(checkpoint_ns IS NULL AND checkpoint_id IS NULL) OR "
            "(checkpoint_ns IS NOT NULL AND checkpoint_id IS NOT NULL)",
            name="approval_checkpoint_complete",
        ),
        Index("ix_approval_workspace_status_expiry", "workspace_id", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    tool_call_id: Mapped[UUID] = mapped_column()
    requested_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    arguments_hash: Mapped[str] = mapped_column(String(64))
    checkpoint_ns: Mapped[str | None] = mapped_column(String(160), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ApprovalStatus.PENDING.value, server_default="PENDING"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_todos_workspace_id"),
        UniqueConstraint(
            "workspace_id", "source_run_id", "source_tool_call_id", name="uq_todos_source_call"
        ),
        UniqueConstraint("workspace_id", "approval_id", name="uq_todos_workspace_approval"),
        ForeignKeyConstraint(
            ["workspace_id", "source_run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_todos_workspace_source_run",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "approval_id",
                "source_run_id",
                "source_tool_call_id",
            ],
            [
                "approval_requests.workspace_id",
                "approval_requests.id",
                "approval_requests.run_id",
                "approval_requests.tool_call_id",
            ],
            ondelete="RESTRICT",
            name="fk_todos_workspace_approval_source",
        ),
        CheckConstraint("status IN ('OPEN','COMPLETED','CANCELLED')", name="todo_status"),
        CheckConstraint("version >= 1", name="todo_version"),
        Index("ix_todos_workspace_status_due", "workspace_id", "status", "due_at"),
        Index("ix_todos_workspace_assignee", "workspace_id", "assignee_user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assignee_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source_run_id: Mapped[UUID] = mapped_column()
    source_tool_call_id: Mapped[UUID] = mapped_column()
    approval_id: Mapped[UUID] = mapped_column()
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=TodoStatus.OPEN.value, server_default="OPEN"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentCitation(Base):
    __tablename__ = "agent_citations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_agent_citations_workspace_id"),
        UniqueConstraint(
            "workspace_id", "run_id", "ordinal", name="uq_agent_citations_run_ordinal"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_citations_workspace_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "document_version_id", "chunk_id", "source_span_id"],
            [
                "document_chunks.workspace_id",
                "document_chunks.document_version_id",
                "document_chunks.id",
                "document_chunks.source_span_id",
            ],
            ondelete="RESTRICT",
            name="fk_agent_citations_workspace_chunk_span",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["messages.workspace_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_agent_citations_workspace_message",
        ),
        CheckConstraint("ordinal >= 1", name="agent_citation_ordinal"),
        Index("ix_agent_citations_workspace_run", "workspace_id", "run_id", "ordinal"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    message_id: Mapped[UUID | None] = mapped_column(nullable=True)
    document_version_id: Mapped[UUID] = mapped_column()
    chunk_id: Mapped[UUID] = mapped_column()
    source_span_id: Mapped[str] = mapped_column(String(64))
    quote: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_checkpoints_workspace_conversation",
        ),
        Index(
            "ix_agent_checkpoints_scope_created",
            "workspace_id",
            "thread_id",
            "checkpoint_ns",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(160), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(64))
    checkpoint_blob: Mapped[bytes] = mapped_column(
        LargeBinary().with_variant(MEDIUMBLOB(), "mysql")
    )
    metadata_type: Mapped[str] = mapped_column(String(64))
    metadata_blob: Mapped[bytes] = mapped_column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentCheckpointWrite(Base):
    __tablename__ = "agent_checkpoint_writes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_checkpoint_writes_workspace_conversation",
        ),
        Index(
            "ix_agent_checkpoint_writes_scope",
            "workspace_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
        Index(
            "ix_agent_checkpoint_writes_orphan_cleanup",
            "workspace_id",
            "checkpoint_ns",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(160), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    write_idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_path: Mapped[str] = mapped_column(String(512), default="", server_default="")
    channel: Mapped[str] = mapped_column(String(255))
    value_type: Mapped[str] = mapped_column(String(64))
    value_blob: Mapped[bytes] = mapped_column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
