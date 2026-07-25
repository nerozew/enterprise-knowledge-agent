from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.base import utc_now
from app.models.entities import (
    IdempotencyRecord,
    InspirationTag,
    MemberRole,
    Tag,
    User,
)
from app.repositories.tags import TagRepository
from app.repositories.workspaces import IdempotencyRepository, WorkspaceRepository
from app.schemas.tags import TagCreate, TagPatch, TagReference, TagResponse
from app.services.workspaces import (
    WorkspaceAccessService,
    add_audit,
    canonical_request_hash,
    hash_idempotency_key,
    provision_personal_workspace,
    validate_idempotency_key,
)


def normalize_tag_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class TagMutationResult:
    tag: Tag | None
    usage_count: int = 0
    response_json: dict[str, Any] | None = None


def tag_response_snapshot(tag: Tag, usage_count: int) -> dict[str, Any]:
    reference = TagReference.model_validate(tag)
    return TagResponse(
        **reference.model_dump(), usage_count=usage_count
    ).model_dump(mode="json")


class TagService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: UUID,
        workspace_id: UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.request_id = request_id
        self.tags = TagRepository(session)
        self.access = WorkspaceAccessService(session, user_id)
        self.idempotency = IdempotencyRepository(session)

    async def _workspace(self, minimum_role: MemberRole) -> tuple[UUID, str]:
        if self.workspace_id is None:
            personal = await WorkspaceRepository(self.session).get_personal(self.user_id)
            if personal is None:
                user = await self.session.get(User, self.user_id)
                if user is None:
                    raise AppError("resource_not_found", "资源不存在", 404)
                personal, _ = await provision_personal_workspace(self.session, user)
                await self.session.commit()
            self.workspace_id = personal.id
        _, member = await self.access.require(
            self.workspace_id, minimum_role=minimum_role
        )
        return self.workspace_id, member.role

    async def _replay_winner(
        self,
        workspace_id: UUID,
        scope: str,
        key_hash: str | None,
        request_hash: str,
        *,
        allow_missing_resource: bool = False,
        conflict: Exception | None = None,
    ) -> TagMutationResult | None:
        if key_hash is None:
            return None
        winner = await self.idempotency.get(
            workspace_id, self.user_id, scope, key_hash
        )
        if winner is None:
            return None
        if winner.request_hash != request_hash:
            raise AppError(
                "idempotency_conflict", "该幂等键已用于不同请求", 409
            ) from conflict
        if winner.response_json is not None:
            usage_count = winner.response_json.get("usage_count", 0)
            return TagMutationResult(
                None,
                usage_count if isinstance(usage_count, int) else 0,
                winner.response_json,
            )
        tag = await self.tags.get_in_workspace(workspace_id, winner.resource_id)
        if tag is None and not allow_missing_resource:
            raise AppError("resource_not_found", "资源不存在", 404)
        usage_count = (
            await self.tags.usage_count(workspace_id, winner.resource_id)
            if tag is not None
            else 0
        )
        return TagMutationResult(tag, usage_count)

    async def list(self) -> list[tuple[Tag, int]]:
        workspace_id, _ = await self._workspace(MemberRole.VIEWER)
        return await self.tags.list_with_usage_counts(workspace_id)

    async def create(
        self, payload: TagCreate, idempotency_key: str | None = None
    ) -> TagMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        normalized = normalize_tag_name(payload.name)
        if not normalized:
            raise AppError("invalid_tag_name", "标签名称不能为空", 422)
        scope = "tag.create"
        request_hash = canonical_request_hash(
            {"name": payload.name, "color": payload.color}
        )
        key_hash = (
            hash_idempotency_key(validate_idempotency_key(idempotency_key))
            if idempotency_key is not None
            else None
        )
        replay = await self._replay_winner(
            workspace_id, scope, key_hash, request_hash
        )
        if replay is not None:
            return replay
        if await self.tags.get_by_normalized_name(workspace_id, normalized):
            raise AppError("tag_exists", "同名标签已存在", 409)
        tag = Tag(
            workspace_id=workspace_id,
            user_id=self.user_id,
            name=payload.name,
            normalized_name=normalized,
            color=payload.color,
        )
        self.tags.add(tag)
        try:
            await self.session.flush()
            response_json = tag_response_snapshot(tag, 0)
            add_audit(
                self.session,
                workspace_id=workspace_id,
                actor_id=self.user_id,
                actor_role=actor_role,
                action="tag.created",
                target_type="tag",
                target_id=tag.id,
                request_id=self.request_id,
                metadata={"version": 1},
            )
            if key_hash is not None:
                self.idempotency.add(
                    IdempotencyRecord(
                        workspace_id=workspace_id,
                        actor_id=self.user_id,
                        scope=scope,
                        key_hash=key_hash,
                        request_hash=request_hash,
                        response_status=201,
                        response_json=response_json,
                        resource_type="tag",
                        resource_id=tag.id,
                        expires_at=utc_now() + timedelta(days=1),
                    )
                )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            replay = await self._replay_winner(
                workspace_id,
                scope,
                key_hash,
                request_hash,
                conflict=exc,
            )
            if replay is not None:
                return replay
            raise AppError("tag_exists", "同名标签已存在", 409) from exc
        return TagMutationResult(tag)

    async def update(
        self,
        tag_id: UUID,
        payload: TagPatch,
        idempotency_key: str | None = None,
    ) -> TagMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        scope = f"tag.update:{tag_id}"
        request_hash = canonical_request_hash(
            {
                "tag_id": tag_id,
                "payload": payload.model_dump(mode="json", exclude_unset=True),
            }
        )
        key_hash = (
            hash_idempotency_key(validate_idempotency_key(idempotency_key))
            if idempotency_key is not None
            else None
        )
        replay = await self._replay_winner(
            workspace_id, scope, key_hash, request_hash
        )
        if replay is not None:
            return replay
        tag = await self.tags.get_in_workspace(workspace_id, tag_id)
        if tag is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        values: dict[str, object] = {
            "version": Tag.version + 1,
            "updated_at": utc_now(),
        }
        changed_fields: list[str] = []
        if "name" in payload.model_fields_set:
            assert payload.name is not None
            values["name"] = payload.name
            values["normalized_name"] = normalize_tag_name(payload.name)
            changed_fields.append("name")
        if "color" in payload.model_fields_set:
            values["color"] = payload.color
            changed_fields.append("color")
        try:
            result = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(Tag)
                    .where(
                        Tag.workspace_id == workspace_id,
                        Tag.id == tag_id,
                        Tag.version == payload.version,
                    )
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                await self.session.rollback()
                replay = await self._replay_winner(
                    workspace_id, scope, key_hash, request_hash
                )
                if replay is not None:
                    return replay
                raise AppError(
                    "version_conflict",
                    "标签已被其他操作更新，请刷新后重试",
                    409,
                )
            updated = await self.tags.get_in_workspace(workspace_id, tag_id)
            assert updated is not None
            usage_count = await self.tags.usage_count(workspace_id, tag_id)
            response_json = tag_response_snapshot(updated, usage_count)
            add_audit(
                self.session,
                workspace_id=workspace_id,
                actor_id=self.user_id,
                actor_role=actor_role,
                action="tag.updated",
                target_type="tag",
                target_id=tag_id,
                request_id=self.request_id,
                metadata={
                    "from_version": payload.version,
                    "to_version": payload.version + 1,
                    "changed_fields": changed_fields,
                },
            )
            if key_hash is not None:
                self.idempotency.add(
                    IdempotencyRecord(
                        workspace_id=workspace_id,
                        actor_id=self.user_id,
                        scope=scope,
                        key_hash=key_hash,
                        request_hash=request_hash,
                        response_status=200,
                        response_json=response_json,
                        resource_type="tag",
                        resource_id=tag_id,
                        expires_at=utc_now() + timedelta(days=1),
                    )
                )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            replay = await self._replay_winner(
                workspace_id,
                scope,
                key_hash,
                request_hash,
                conflict=exc,
            )
            if replay is not None:
                return replay
            raise AppError("tag_exists", "同名标签已存在", 409) from exc
        return TagMutationResult(updated, usage_count)

    async def delete(
        self,
        tag_id: UUID,
        version: int,
        *,
        force: bool = False,
        idempotency_key: str | None = None,
    ) -> TagMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        scope = f"tag.delete:{tag_id}"
        request_hash = canonical_request_hash(
            {"tag_id": tag_id, "version": version, "force": force}
        )
        key_hash = (
            hash_idempotency_key(validate_idempotency_key(idempotency_key))
            if idempotency_key is not None
            else None
        )
        replay = await self._replay_winner(
            workspace_id,
            scope,
            key_hash,
            request_hash,
            allow_missing_resource=True,
        )
        if replay is not None:
            return replay
        tag = await self.tags.get_in_workspace(workspace_id, tag_id)
        if tag is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        if tag.version != version:
            raise AppError("version_conflict", "标签已被其他操作更新，请刷新后重试", 409)
        usage_count = await self.tags.usage_count(workspace_id, tag_id)
        if usage_count and not force:
            raise AppError(
                "tag_in_use",
                "标签正在被灵感使用；确认解除关联后请使用 force=true",
                409,
                {"usage_count": usage_count},
            )
        try:
            if usage_count:
                await self.session.execute(
                    delete(InspirationTag).where(
                        InspirationTag.workspace_id == workspace_id,
                        InspirationTag.tag_id == tag_id,
                    )
                )
            result = cast(
                CursorResult[Any],
                await self.session.execute(
                    delete(Tag).where(
                        Tag.workspace_id == workspace_id,
                        Tag.id == tag_id,
                        Tag.version == version,
                    )
                ),
            )
            if result.rowcount != 1:
                await self.session.rollback()
                replay = await self._replay_winner(
                    workspace_id,
                    scope,
                    key_hash,
                    request_hash,
                    allow_missing_resource=True,
                )
                if replay is not None:
                    return replay
                raise AppError(
                    "version_conflict",
                    "标签已被其他操作更新，请刷新后重试",
                    409,
                )
            response_json = {"deleted": True, "tag_id": str(tag_id)}
            add_audit(
                self.session,
                workspace_id=workspace_id,
                actor_id=self.user_id,
                actor_role=actor_role,
                action="tag.deleted",
                target_type="tag",
                target_id=tag_id,
                request_id=self.request_id,
                metadata={"version": version, "usage_count": usage_count},
            )
            if key_hash is not None:
                self.idempotency.add(
                    IdempotencyRecord(
                        workspace_id=workspace_id,
                        actor_id=self.user_id,
                        scope=scope,
                        key_hash=key_hash,
                        request_hash=request_hash,
                        response_status=204,
                        response_json=response_json,
                        resource_type="tag",
                        resource_id=tag_id,
                        expires_at=utc_now() + timedelta(days=7),
                    )
                )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            replay = await self._replay_winner(
                workspace_id,
                scope,
                key_hash,
                request_hash,
                allow_missing_resource=True,
                conflict=exc,
            )
            if replay is not None:
                return replay
            raise AppError(
                "idempotency_conflict", "该幂等键已用于不同请求", 409
            ) from exc
        return TagMutationResult(None, response_json=response_json)
