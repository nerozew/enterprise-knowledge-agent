from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AuditLog,
    IdempotencyRecord,
    MemberStatus,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceStatus,
)


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_personal(self, user_id: UUID) -> Workspace | None:
        return await self.session.scalar(
            select(Workspace).where(
                Workspace.personal_owner_user_id == user_id,
                Workspace.status == WorkspaceStatus.ACTIVE.value,
            )
        )

    async def get_active_with_membership(
        self, workspace_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> tuple[Workspace, WorkspaceMember] | None:
        query = (
            select(Workspace, WorkspaceMember)
            .join(
                WorkspaceMember,
                and_(
                    WorkspaceMember.workspace_id == Workspace.id,
                    WorkspaceMember.user_id == user_id,
                    WorkspaceMember.status == MemberStatus.ACTIVE.value,
                ),
            )
            .where(
                Workspace.id == workspace_id,
                Workspace.status == WorkspaceStatus.ACTIVE.value,
            )
        )
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).one_or_none()
        return (row[0], row[1]) if row else None

    async def list_for_user(
        self, user_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[Workspace, WorkspaceMember]], int]:
        filters = (
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == MemberStatus.ACTIVE.value,
            Workspace.status == WorkspaceStatus.ACTIVE.value,
        )
        total = int(
            await self.session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(*filters)
            )
            or 0
        )
        rows = (
            await self.session.execute(
                select(Workspace, WorkspaceMember)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(*filters)
                .order_by(
                    Workspace.personal_owner_user_id.is_(None),
                    Workspace.updated_at.desc(),
                    Workspace.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(row[0], row[1]) for row in rows], total

    async def get_by_id(self, workspace_id: UUID, *, for_update: bool = False) -> Workspace | None:
        query = select(Workspace).where(Workspace.id == workspace_id)
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    def add(self, workspace: Workspace) -> None:
        self.session.add(workspace)


class WorkspaceMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, workspace_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> WorkspaceMember | None:
        query = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def list(
        self, workspace_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[WorkspaceMember, User]], int]:
        filters = (
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.status != MemberStatus.REMOVED.value,
        )
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(WorkspaceMember).where(*filters)
            )
            or 0
        )
        rows = (
            await self.session.execute(
                select(WorkspaceMember, User)
                .join(User, User.id == WorkspaceMember.user_id)
                .where(*filters)
                .order_by(WorkspaceMember.created_at.asc(), WorkspaceMember.user_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(row[0], row[1]) for row in rows], total

    async def active_owner_count(self, workspace_id: UUID, *, for_update: bool = False) -> int:
        query: Select[tuple[WorkspaceMember]] = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == "OWNER",
            WorkspaceMember.status == MemberStatus.ACTIVE.value,
        )
        if for_update:
            query = query.with_for_update()
        return len(list((await self.session.scalars(query)).all()))

    def add(self, member: WorkspaceMember) -> None:
        self.session.add(member)


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, log: AuditLog) -> None:
        self.session.add(log)

    async def list(
        self,
        workspace_id: UUID,
        *,
        action: str | None,
        actor_id: UUID | None,
        target_type: str | None,
        target_id: UUID | None,
        date_from: datetime | None,
        date_to: datetime | None,
        cursor_created_at: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> list[tuple[AuditLog, User]]:
        filters = [AuditLog.workspace_id == workspace_id]
        if action:
            filters.append(AuditLog.action == action)
        if actor_id:
            filters.append(AuditLog.actor_id == actor_id)
        if target_type:
            filters.append(AuditLog.target_type == target_type)
        if target_id:
            filters.append(AuditLog.target_id == target_id)
        if date_from:
            filters.append(AuditLog.created_at >= date_from)
        if date_to:
            filters.append(AuditLog.created_at <= date_to)
        if cursor_created_at and cursor_id:
            filters.append(
                or_(
                    AuditLog.created_at < cursor_created_at,
                    and_(
                        AuditLog.created_at == cursor_created_at,
                        AuditLog.id < cursor_id,
                    ),
                )
            )
        rows = (
            await self.session.execute(
                select(AuditLog, User)
                .join(User, User.id == AuditLog.actor_id)
                .where(*filters)
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(limit)
            )
        ).all()
        return [(row[0], row[1]) for row in rows]


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, workspace_id: UUID, actor_id: UUID, scope: str, key_hash: str
    ) -> IdempotencyRecord | None:
        return await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.workspace_id == workspace_id,
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key_hash == key_hash,
            )
        )

    def add(self, record: IdempotencyRecord) -> None:
        self.session.add(record)
