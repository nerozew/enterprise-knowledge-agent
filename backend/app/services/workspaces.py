from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.base import utc_now
from app.models.entities import (
    AuditLog,
    IdempotencyRecord,
    MemberRole,
    MemberStatus,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceStatus,
    WorkspaceType,
)
from app.repositories.users import UserRepository
from app.repositories.workspaces import (
    AuditLogRepository,
    IdempotencyRepository,
    WorkspaceMemberRepository,
    WorkspaceRepository,
)
from app.schemas.workspaces import MemberCreate, MemberPatch, WorkspaceCreate, WorkspacePatch

ROLE_RANK = {
    MemberRole.VIEWER.value: 1,
    MemberRole.EDITOR.value: 2,
    MemberRole.ADMIN.value: 3,
    MemberRole.OWNER.value: 4,
}


def canonical_request_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def hash_idempotency_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def validate_idempotency_key(key: str | None) -> str:
    if key is None or not 8 <= len(key) <= 128 or not key.isascii():
        raise AppError(
            "invalid_idempotency_key",
            "Idempotency-Key 必须为 8 到 128 个 ASCII 字符",
            422,
        )
    return key


def add_audit(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    actor_id: UUID,
    actor_role: str,
    action: str,
    target_type: str,
    target_id: UUID | None,
    request_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    # metadata 只能由服务端白名单字段构造，绝不接受请求体快照。
    log = AuditLog(
        workspace_id=workspace_id,
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
        metadata_json=metadata or {},
    )
    AuditLogRepository(session).add(log)
    return log


def workspace_response_snapshot(
    workspace: Workspace, member: WorkspaceMember
) -> dict[str, Any]:
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "kind": workspace.type,
        "status": workspace.status,
        "version": workspace.version,
        "role": member.role,
        "member_status": member.status,
        "is_default": workspace.personal_owner_user_id == member.user_id,
        "created_at": workspace.created_at.isoformat(),
        "updated_at": workspace.updated_at.isoformat(),
    }


def member_response_snapshot(
    member: WorkspaceMember, user: User
) -> dict[str, Any]:
    return {
        "workspace_id": str(member.workspace_id),
        "user_id": str(member.user_id),
        "email": user.email,
        "display_name": user.display_name,
        "role": member.role,
        "status": member.status,
        "version": member.version,
        "joined_at": member.created_at.isoformat() if member.created_at else None,
        "updated_at": member.updated_at.isoformat(),
    }


async def provision_personal_workspace(
    session: AsyncSession, user: User, *, request_id: str | None = None
) -> tuple[Workspace, WorkspaceMember]:
    existing = await WorkspaceRepository(session).get_personal(user.id)
    if existing:
        member = await WorkspaceMemberRepository(session).get(existing.id, user.id)
        if member is None:
            raise RuntimeError("个人 Workspace 缺少 owner membership")
        return existing, member
    workspace = Workspace(
        name=f"{user.display_name}的个人空间",
        type=WorkspaceType.PERSONAL.value,
        status=WorkspaceStatus.ACTIVE.value,
        personal_owner_user_id=user.id,
    )
    session.add(workspace)
    await session.flush()
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=MemberRole.OWNER.value,
        status=MemberStatus.ACTIVE.value,
        invited_by_user_id=user.id,
    )
    session.add(member)
    add_audit(
        session,
        workspace_id=workspace.id,
        actor_id=user.id,
        actor_role=MemberRole.OWNER.value,
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
        request_id=request_id,
        metadata={"kind": WorkspaceType.PERSONAL.value, "version": 1},
    )
    add_audit(
        session,
        workspace_id=workspace.id,
        actor_id=user.id,
        actor_role=MemberRole.OWNER.value,
        action="membership.created",
        target_type="workspace_member",
        target_id=user.id,
        request_id=request_id,
        metadata={
            "role": MemberRole.OWNER.value,
            "status": MemberStatus.ACTIVE.value,
            "version": 1,
        },
    )
    return workspace, member


class WorkspaceAccessService:
    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = actor_id
        self.workspaces = WorkspaceRepository(session)

    async def personal(self) -> tuple[Workspace, WorkspaceMember]:
        workspace = await self.workspaces.get_personal(self.actor_id)
        if workspace is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        access = await self.workspaces.get_active_with_membership(workspace.id, self.actor_id)
        if access is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        return access

    async def require(
        self,
        workspace_id: UUID,
        *,
        minimum_role: MemberRole = MemberRole.VIEWER,
        for_update: bool = False,
    ) -> tuple[Workspace, WorkspaceMember]:
        access = await self.workspaces.get_active_with_membership(
            workspace_id, self.actor_id, for_update=for_update
        )
        if access is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if ROLE_RANK[access[1].role] < ROLE_RANK[minimum_role.value]:
            raise AppError("permission_denied", "当前角色无权执行此操作", 403)
        return access


class WorkspaceService:
    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = actor_id
        self.workspaces = WorkspaceRepository(session)
        self.members = WorkspaceMemberRepository(session)
        self.idempotency = IdempotencyRepository(session)
        self.access = WorkspaceAccessService(session, actor_id)

    async def list(
        self, *, page: int = 1, page_size: int = 50
    ) -> tuple[list[tuple[Workspace, WorkspaceMember]], int]:
        return await self.workspaces.list_for_user(
            self.actor_id, page=page, page_size=page_size
        )

    async def get(self, workspace_id: UUID) -> tuple[Workspace, WorkspaceMember]:
        return await self.access.require(workspace_id)

    async def create(
        self, payload: WorkspaceCreate, idempotency_key: str | None, request_id: str | None
    ) -> tuple[Workspace, WorkspaceMember, dict[str, Any] | None]:
        key = validate_idempotency_key(idempotency_key)
        personal, _ = await self.access.personal()
        personal_id = personal.id
        scope = "workspace.create"
        request_hash = canonical_request_hash({"name": payload.name})
        existing = await self.idempotency.get(
            personal_id, self.actor_id, scope, hash_idempotency_key(key)
        )
        if existing:
            if existing.request_hash != request_hash:
                raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
            replay = await self.access.require(existing.resource_id)
            return replay[0], replay[1], existing.response_json

        workspace = Workspace(
            name=payload.name,
            type=WorkspaceType.TEAM.value,
            status=WorkspaceStatus.ACTIVE.value,
        )
        self.workspaces.add(workspace)
        await self.session.flush()
        member = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=self.actor_id,
            role=MemberRole.OWNER.value,
            status=MemberStatus.ACTIVE.value,
            invited_by_user_id=self.actor_id,
        )
        self.members.add(member)
        await self.session.flush()
        add_audit(
            self.session,
            workspace_id=workspace.id,
            actor_id=self.actor_id,
            actor_role=MemberRole.OWNER.value,
            action="workspace.created",
            target_type="workspace",
            target_id=workspace.id,
            request_id=request_id,
            metadata={"kind": WorkspaceType.TEAM.value, "version": 1},
        )
        add_audit(
            self.session,
            workspace_id=workspace.id,
            actor_id=self.actor_id,
            actor_role=MemberRole.OWNER.value,
            action="membership.created",
            target_type="workspace_member",
            target_id=self.actor_id,
            request_id=request_id,
            metadata={
                "role": MemberRole.OWNER.value,
                "status": MemberStatus.ACTIVE.value,
                "version": 1,
            },
        )
        self.idempotency.add(
            IdempotencyRecord(
                workspace_id=personal_id,
                actor_id=self.actor_id,
                scope=scope,
                key_hash=hash_idempotency_key(key),
                request_hash=request_hash,
                response_status=201,
                response_json=workspace_response_snapshot(workspace, member),
                resource_type="workspace",
                resource_id=workspace.id,
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.idempotency.get(
                personal_id, self.actor_id, scope, hash_idempotency_key(key)
            )
            if winner and winner.request_hash == request_hash:
                replay = await self.access.require(winner.resource_id)
                return replay[0], replay[1], winner.response_json
            raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409) from exc
        return workspace, member, None

    async def update(
        self, workspace_id: UUID, payload: WorkspacePatch, request_id: str | None
    ) -> tuple[Workspace, WorkspaceMember]:
        workspace, member = await self.access.require(
            workspace_id, minimum_role=MemberRole.ADMIN
        )
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Workspace)
                .where(Workspace.id == workspace_id, Workspace.version == payload.version)
                .values(
                    name=payload.name,
                    version=Workspace.version + 1,
                    updated_at=utc_now(),
                )
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise AppError("version_conflict", "Workspace 已被其他操作更新", 409)
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="workspace.updated",
            target_type="workspace",
            target_id=workspace_id,
            request_id=request_id,
            metadata={"from_version": payload.version, "to_version": payload.version + 1},
        )
        await self.session.commit()
        refreshed = await self.access.require(workspace_id)
        return refreshed


class MemberService:
    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = actor_id
        self.access = WorkspaceAccessService(session, actor_id)
        self.members = WorkspaceMemberRepository(session)
        self.users = UserRepository(session)
        self.idempotency = IdempotencyRepository(session)

    @staticmethod
    def _admin_guard(actor: WorkspaceMember, target_role: str, new_role: str | None) -> None:
        if actor.role == MemberRole.ADMIN.value and (
            target_role in {MemberRole.OWNER.value, MemberRole.ADMIN.value}
            or new_role in {MemberRole.OWNER.value, MemberRole.ADMIN.value}
        ):
            raise AppError("permission_denied", "ADMIN 无权管理 ADMIN 或 OWNER", 403)

    async def list(
        self, workspace_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[WorkspaceMember, User]], int]:
        await self.access.require(workspace_id, minimum_role=MemberRole.ADMIN)
        return await self.members.list(workspace_id, page=page, page_size=page_size)

    async def add(
        self,
        workspace_id: UUID,
        payload: MemberCreate,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> tuple[WorkspaceMember, User, dict[str, Any] | None]:
        key = validate_idempotency_key(idempotency_key)
        _, actor = await self.access.require(
            workspace_id, minimum_role=MemberRole.ADMIN
        )
        self._admin_guard(actor, MemberRole.VIEWER.value, payload.role)
        request_hash = canonical_request_hash(
            {"email": str(payload.email).strip().lower(), "role": payload.role}
        )
        scope = "membership.create"
        record = await self.idempotency.get(
            workspace_id, self.actor_id, scope, hash_idempotency_key(key)
        )
        if record:
            if record.request_hash != request_hash:
                raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
            replay_member = await self.members.get(workspace_id, record.resource_id)
            replay_user = await self.users.get_by_id(record.resource_id)
            if replay_member is None or replay_user is None:
                raise AppError("resource_not_found", "资源不存在", 404)
            return replay_member, replay_user, record.response_json
        candidate = await self.users.get_active_by_email(str(payload.email).strip().lower())
        if candidate is None:
            raise AppError("member_candidate_not_found", "待添加用户不存在", 404)
        if await self.members.get(workspace_id, candidate.id):
            raise AppError("membership_exists", "该用户已存在成员记录", 409)
        member = WorkspaceMember(
            workspace_id=workspace_id,
            user_id=candidate.id,
            role=payload.role,
            status=MemberStatus.ACTIVE.value,
            invited_by_user_id=self.actor_id,
        )
        self.members.add(member)
        try:
            # flush 也可能在并发成员创建时触发唯一约束冲突，必须与 commit
            # 使用同一恢复分支。
            await self.session.flush()
            add_audit(
                self.session,
                workspace_id=workspace_id,
                actor_id=self.actor_id,
                actor_role=actor.role,
                action="membership.created",
                target_type="workspace_member",
                target_id=candidate.id,
                request_id=request_id,
                metadata={
                    "role": payload.role,
                    "status": MemberStatus.ACTIVE.value,
                    "version": 1,
                },
            )
            self.idempotency.add(
                IdempotencyRecord(
                    workspace_id=workspace_id,
                    actor_id=self.actor_id,
                    scope=scope,
                    key_hash=hash_idempotency_key(key),
                    request_hash=request_hash,
                    response_status=201,
                    response_json=member_response_snapshot(member, candidate),
                    resource_type="workspace_member",
                    resource_id=candidate.id,
                    expires_at=utc_now() + timedelta(days=7),
                )
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.idempotency.get(
                workspace_id,
                self.actor_id,
                scope,
                hash_idempotency_key(key),
            )
            if winner:
                if winner.request_hash != request_hash:
                    raise AppError(
                        "idempotency_conflict", "该幂等键已用于不同请求", 409
                    ) from exc
                replay_member = await self.members.get(
                    workspace_id, winner.resource_id
                )
                replay_user = await self.users.get_by_id(winner.resource_id)
                if replay_member is not None and replay_user is not None:
                    return replay_member, replay_user, winner.response_json
            raise AppError("membership_exists", "该用户已存在成员记录", 409) from exc
        return member, candidate, None

    async def change(
        self,
        workspace_id: UUID,
        target_user_id: UUID,
        payload: MemberPatch,
        idempotency_key: str | None,
        request_id: str | None,
        *,
        action: str = "membership.updated",
    ) -> tuple[WorkspaceMember, User, dict[str, Any] | None]:
        key = validate_idempotency_key(idempotency_key)
        is_self_removal = (
            action == "membership.removed" and target_user_id == self.actor_id
        )
        workspace, actor = await self.access.require(
            workspace_id,
            minimum_role=(
                MemberRole.VIEWER if is_self_removal else MemberRole.ADMIN
            ),
            for_update=True,
        )
        target = await self.members.get(workspace_id, target_user_id, for_update=True)
        if target is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if not is_self_removal:
            self._admin_guard(actor, target.role, payload.role)
        request_hash = canonical_request_hash(
            {
                "target_user_id": target_user_id,
                "role": payload.role,
                "status": payload.status,
                "version": payload.version,
                "action": action,
            }
        )
        scope = f"{action}:{target_user_id}"
        record = await self.idempotency.get(
            workspace_id, self.actor_id, scope, hash_idempotency_key(key)
        )
        if record:
            if record.request_hash != request_hash:
                raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
            user = await self.users.get_by_id(target_user_id)
            assert user is not None
            return target, user, record.response_json
        if target.version != payload.version:
            raise AppError("version_conflict", "成员记录已被其他操作更新", 409)
        new_role = payload.role or target.role
        new_status = payload.status or target.status
        if (
            workspace.type == WorkspaceType.PERSONAL.value
            and workspace.personal_owner_user_id == target_user_id
            and (
                new_role != MemberRole.OWNER.value
                or new_status != MemberStatus.ACTIVE.value
            )
        ):
            raise AppError(
                "personal_workspace_owner_required",
                "个人 Workspace 所有者不得降级、停用或移除",
                409,
            )
        reduces_owner = (
            target.role == MemberRole.OWNER.value
            and target.status == MemberStatus.ACTIVE.value
            and (
                new_role != MemberRole.OWNER.value
                or new_status != MemberStatus.ACTIVE.value
            )
        )
        if reduces_owner and await self.members.active_owner_count(
            workspace_id, for_update=True
        ) <= 1:
            raise AppError(
                "last_owner_required", "Workspace 必须保留至少一名 OWNER", 409
            )
        old_role, old_status, old_version = target.role, target.status, target.version
        target.role = new_role
        target.status = new_status
        target.version += 1
        target.updated_at = utc_now()
        user = await self.users.get_by_id(target_user_id)
        assert user is not None
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=actor.role,
            action=action,
            target_type="workspace_member",
            target_id=target_user_id,
            request_id=request_id,
            metadata={
                "from_role": old_role,
                "to_role": new_role,
                "from_status": old_status,
                "to_status": new_status,
                "from_version": old_version,
                "to_version": target.version,
            },
        )
        self.idempotency.add(
            IdempotencyRecord(
                workspace_id=workspace_id,
                actor_id=self.actor_id,
                scope=scope,
                key_hash=hash_idempotency_key(key),
                request_hash=request_hash,
                response_status=200,
                response_json=member_response_snapshot(target, user),
                resource_type="workspace_member",
                resource_id=target_user_id,
                expires_at=utc_now() + timedelta(days=7),
            )
        )
        await self.session.commit()
        return target, user, None


class AuditService:
    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.access = WorkspaceAccessService(session, actor_id)
        self.logs = AuditLogRepository(session)

    @staticmethod
    def encode_cursor(created_at: datetime, log_id: UUID) -> str:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        raw = json.dumps(
            {"created_at": created_at.astimezone(UTC).isoformat(), "id": str(log_id)},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
        if not cursor:
            return None, None
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            value = json.loads(raw)
            created_at = datetime.fromisoformat(value["created_at"])
            if created_at.tzinfo is None:
                raise ValueError
            return created_at, UUID(value["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AppError("invalid_cursor", "审计游标无效", 422) from exc

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
        cursor: str | None,
        limit: int,
    ) -> tuple[list[tuple[AuditLog, User]], str | None]:
        await self.access.require(workspace_id, minimum_role=MemberRole.ADMIN)
        if date_from and date_from.tzinfo is None or date_to and date_to.tzinfo is None:
            raise AppError("invalid_date", "审计日期必须包含时区", 422)
        cursor_at, cursor_id = self.decode_cursor(cursor)
        rows = await self.logs.list(
            workspace_id,
            action=action,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            date_from=date_from,
            date_to=date_to,
            cursor_created_at=cursor_at,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            self.encode_cursor(items[-1][0].created_at, items[-1][0].id)
            if has_more and items
            else None
        )
        return items, next_cursor
