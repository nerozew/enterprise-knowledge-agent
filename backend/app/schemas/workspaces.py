from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import EmailStr, Field, model_validator

from app.schemas.common import APIModel, Page

WorkspaceKind = Literal["PERSONAL", "TEAM"]
MemberRoleValue = Literal["OWNER", "ADMIN", "EDITOR", "VIEWER"]
MemberStatusValue = Literal["INVITED", "ACTIVE", "SUSPENDED", "REMOVED"]


class WorkspaceCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)


class WorkspacePatch(APIModel):
    name: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class WorkspaceSummary(APIModel):
    id: UUID
    name: str
    kind: WorkspaceKind
    status: Literal["ACTIVE"]
    version: int
    role: MemberRoleValue
    member_status: MemberStatusValue
    is_default: bool
    created_at: datetime
    updated_at: datetime


class WorkspaceList(Page[WorkspaceSummary]):
    pass


class MemberCreate(APIModel):
    email: EmailStr
    role: MemberRoleValue


class MemberPatch(APIModel):
    version: int = Field(ge=1)
    role: MemberRoleValue | None = None
    status: Literal["ACTIVE", "SUSPENDED", "REMOVED"] | None = None

    @model_validator(mode="after")
    def has_change(self) -> MemberPatch:
        if self.role is None and self.status is None:
            raise ValueError("role 和 status 至少提供一项")
        return self


class MemberSummary(APIModel):
    workspace_id: UUID
    user_id: UUID
    email: EmailStr
    display_name: str
    role: MemberRoleValue
    status: MemberStatusValue
    version: int
    joined_at: datetime | None
    updated_at: datetime


class MemberList(Page[MemberSummary]):
    pass


class AuditActor(APIModel):
    user_id: UUID
    display_name: str
    role: MemberRoleValue


class AuditLogSummary(APIModel):
    id: UUID
    workspace_id: UUID
    actor: AuditActor
    action: str
    target_type: str
    target_id: UUID | None
    result: str
    request_id: str | None
    metadata: dict[str, Any]
    created_at: datetime


class AuditLogList(APIModel):
    items: list[AuditLogSummary]
    next_cursor: str | None
