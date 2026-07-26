from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now


class WorkspaceType(StrEnum):
    PERSONAL = "PERSONAL"
    TEAM = "TEAM"


class WorkspaceStatus(StrEnum):
    ACTIVE = "ACTIVE"


class MemberRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


class MemberStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "type IN ('PERSONAL','TEAM')", name="workspace_type"
        ),
        CheckConstraint("status IN ('ACTIVE')", name="workspace_status"),
        CheckConstraint(
            "(type = 'PERSONAL' AND personal_owner_user_id IS NOT NULL) OR "
            "(type = 'TEAM' AND personal_owner_user_id IS NULL)",
            name="workspace_personal_owner",
        ),
        UniqueConstraint(
            "personal_owner_user_id", name="uq_workspaces_personal_owner_user_id"
        ),
        Index("ix_workspaces_status_updated", "status", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default=WorkspaceStatus.ACTIVE.value, server_default="ACTIVE"
    )
    personal_owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('OWNER','ADMIN','EDITOR','VIEWER')",
            name="workspace_member_role",
        ),
        CheckConstraint(
            "status IN ('INVITED','ACTIVE','SUSPENDED','REMOVED')",
            name="workspace_member_status",
        ),
        Index("ix_workspace_members_user_status", "user_id", "status", "updated_at"),
        Index("ix_workspace_members_workspace_status_role", "workspace_id", "status", "role"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default=MemberStatus.ACTIVE.value, server_default="ACTIVE"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_audit_logs_workspace_action", "workspace_id", "action", "created_at"),
        Index("ix_audit_logs_workspace_actor", "workspace_id", "actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    actor_role: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[UUID | None] = mapped_column(nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="SUCCESS", server_default="SUCCESS")
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "actor_id",
            "scope",
            "key_hash",
            name="uq_idempotency_workspace_actor_scope_key",
        ),
        Index("ix_idempotency_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(80))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_hash_algorithm: Mapped[str] = mapped_column(
        String(16), default="SHA-256", server_default="SHA-256"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="SUCCEEDED", server_default="SUCCEEDED"
    )
    operation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    response_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    response_status: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InspirationTag(Base):
    __tablename__ = "inspiration_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "inspiration_id"],
            ["inspirations.workspace_id", "inspirations.id"],
            ondelete="CASCADE",
            name="fk_inspiration_tags_workspace_inspiration",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tag_id"],
            ["tags.workspace_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_inspiration_tags_workspace_tag",
        ),
        Index("ix_inspiration_tags_tag_id", "tag_id"),
        Index("ix_inspiration_tags_workspace_tag", "workspace_id", "tag_id"),
    )

    inspiration_id: Mapped[UUID] = mapped_column(primary_key=True)
    tag_id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Inspiration(Base):
    __tablename__ = "inspirations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_inspirations_workspace_id"),
        Index("ix_inspirations_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_inspirations_workspace_deleted", "workspace_id", "deleted_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    expanded_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tags: Mapped[list[Tag]] = relationship(
        secondary="inspiration_tags",
        primaryjoin="and_(Inspiration.workspace_id == InspirationTag.workspace_id, "
        "Inspiration.id == InspirationTag.inspiration_id)",
        secondaryjoin="and_(Tag.workspace_id == InspirationTag.workspace_id, "
        "Tag.id == InspirationTag.tag_id)",
        lazy="selectin",
        back_populates="inspirations",
    )


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_tags_workspace_id"),
        UniqueConstraint(
            "workspace_id", "normalized_name", name="uq_tags_workspace_normalized_name"
        ),
        Index("ix_tags_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    normalized_name: Mapped[str] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    inspirations: Mapped[list[Inspiration]] = relationship(
        secondary="inspiration_tags",
        primaryjoin="and_(Tag.workspace_id == InspirationTag.workspace_id, "
        "Tag.id == InspirationTag.tag_id)",
        secondaryjoin="and_(Inspiration.workspace_id == InspirationTag.workspace_id, "
        "Inspiration.id == InspirationTag.inspiration_id)",
        lazy="selectin",
        back_populates="tags",
    )


class AiGeneration(Base):
    __tablename__ = "ai_generations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "inspiration_id"],
            ["inspirations.workspace_id", "inspirations.id"],
            ondelete="CASCADE",
            name="fk_ai_generations_workspace_inspiration",
        ),
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "idempotency_key",
            name="uq_ai_generations_workspace_user_idempotency",
        ),
        Index(
            "ix_ai_generations_workspace_inspiration_created",
            "workspace_id",
            "inspiration_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    inspiration_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(80))
    output: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
