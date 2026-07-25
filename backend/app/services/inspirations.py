from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.base import utc_now
from app.models.entities import IdempotencyRecord, Inspiration, MemberRole, Tag, User
from app.repositories.inspirations import InspirationRepository
from app.repositories.tags import TagRepository
from app.repositories.workspaces import IdempotencyRepository, WorkspaceRepository
from app.schemas.inspirations import (
    InspirationCreate,
    InspirationPatch,
    InspirationResponse,
)
from app.services.workspaces import (
    WorkspaceAccessService,
    add_audit,
    canonical_request_hash,
    hash_idempotency_key,
    provision_personal_workspace,
    validate_idempotency_key,
)


@dataclass(frozen=True)
class InspirationMutationResult:
    resource: Inspiration
    response_json: dict[str, Any] | None = None


def inspiration_response_snapshot(inspiration: Inspiration) -> dict[str, Any]:
    return InspirationResponse.model_validate(inspiration).model_dump(mode="json")


class InspirationService:
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
        self.inspirations = InspirationRepository(session)
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

    async def _workspace_tags(
        self, workspace_id: UUID, tag_ids: builtins.list[UUID]
    ) -> builtins.list[Tag]:
        tags = await self.tags.get_many_in_workspace(workspace_id, tag_ids)
        if len(tags) != len(tag_ids):
            raise AppError("invalid_tags", "包含不存在或不属于当前 Workspace 的标签", 422)
        by_id = {tag.id: tag for tag in tags}
        return [by_id[tag_id] for tag_id in tag_ids]

    async def _replay_winner(
        self,
        workspace_id: UUID,
        scope: str,
        key_hash: str | None,
        request_hash: str,
        *,
        conflict: Exception | None = None,
    ) -> InspirationMutationResult | None:
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
        replay = await self.inspirations.get_in_workspace(
            workspace_id, winner.resource_id, include_deleted=True
        )
        if replay is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        return InspirationMutationResult(replay, winner.response_json)

    async def create(
        self, payload: InspirationCreate, idempotency_key: str | None = None
    ) -> InspirationMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        request_hash = canonical_request_hash(
            {
                "title": payload.title,
                "content": payload.content,
                "source_url": str(payload.source_url) if payload.source_url else None,
                "tag_ids": sorted(str(tag_id) for tag_id in payload.tag_ids),
            }
        )
        key_hash = None
        if idempotency_key is not None:
            key_hash = hash_idempotency_key(
                validate_idempotency_key(idempotency_key)
            )
            record = await self.idempotency.get(
                workspace_id, self.user_id, "inspiration.create", key_hash
            )
            if record:
                if record.request_hash != request_hash:
                    raise AppError(
                        "idempotency_conflict", "该幂等键已用于不同请求", 409
                    )
                replay_resource = await self.inspirations.get_in_workspace(
                    workspace_id, record.resource_id, include_deleted=True
                )
                if replay_resource is None:
                    raise AppError("resource_not_found", "资源不存在", 404)
                return InspirationMutationResult(
                    replay_resource, record.response_json
                )
        tags = await self._workspace_tags(workspace_id, payload.tag_ids)
        inspiration = Inspiration(
            workspace_id=workspace_id,
            user_id=self.user_id,
            title=payload.title,
            content=payload.content,
            source_url=str(payload.source_url) if payload.source_url else None,
            tags=tags,
        )
        self.inspirations.add(inspiration)
        await self.session.flush()
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.user_id,
            actor_role=actor_role,
            action="inspiration.created",
            target_type="inspiration",
            target_id=inspiration.id,
            request_id=self.request_id,
            metadata={"version": 1, "tag_count": len(tags)},
        )
        response_json = inspiration_response_snapshot(inspiration)
        if key_hash is not None:
            self.idempotency.add(
                IdempotencyRecord(
                    workspace_id=workspace_id,
                    actor_id=self.user_id,
                    scope="inspiration.create",
                    key_hash=key_hash,
                    request_hash=request_hash,
                    response_status=201,
                    response_json=response_json,
                    resource_type="inspiration",
                    resource_id=inspiration.id,
                    expires_at=utc_now() + timedelta(days=1),
                )
            )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if key_hash is not None:
                winner = await self.idempotency.get(
                    workspace_id, self.user_id, "inspiration.create", key_hash
                )
                if winner and winner.request_hash == request_hash:
                    replay = await self.inspirations.get_in_workspace(
                        workspace_id, winner.resource_id, include_deleted=True
                    )
                    if replay is not None:
                        return InspirationMutationResult(
                            replay, winner.response_json
                        )
            raise AppError(
                "idempotency_conflict", "该幂等键已用于不同请求", 409
            ) from exc
        loaded = await self.inspirations.get_in_workspace(workspace_id, inspiration.id)
        assert loaded is not None
        return InspirationMutationResult(loaded)

    async def get(self, inspiration_id: UUID, *, include_deleted: bool = False) -> Inspiration:
        workspace_id, _ = await self._workspace(MemberRole.VIEWER)
        inspiration = await self.inspirations.get_in_workspace(
            workspace_id, inspiration_id, include_deleted=include_deleted
        )
        if inspiration is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        return inspiration

    async def list(
        self,
        *,
        q: str | None = None,
        tag_ids: builtins.list[UUID] | None = None,
        tag_match: str = "any",
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort: str = "updated_desc",
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[Inspiration], int]:
        workspace_id, _ = await self._workspace(MemberRole.VIEWER)
        unique_tag_ids = list(dict.fromkeys(tag_ids or []))
        if unique_tag_ids:
            workspace_tags = await self.tags.get_many_in_workspace(
                workspace_id, unique_tag_ids
            )
            if len(workspace_tags) != len(unique_tag_ids):
                raise AppError("resource_not_found", "资源不存在", 404)
        if date_from and date_from.tzinfo is None:
            raise AppError("invalid_date", "date_from 必须包含时区", 422)
        if date_to and date_to.tzinfo is None:
            raise AppError("invalid_date", "date_to 必须包含时区", 422)
        if date_from and date_to and date_from > date_to:
            raise AppError("invalid_date_range", "date_from 不能晚于 date_to", 422)
        return await self.inspirations.list(
            workspace_id,
            query_text=q,
            tag_ids=unique_tag_ids,
            tag_match=tag_match,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
        )

    async def update(
        self,
        inspiration_id: UUID,
        payload: InspirationPatch,
        idempotency_key: str | None = None,
    ) -> InspirationMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        key_hash = None
        request_hash = canonical_request_hash(
            {
                "inspiration_id": inspiration_id,
                "payload": payload.model_dump(mode="json", exclude_unset=True),
            }
        )
        scope = f"inspiration.update:{inspiration_id}"
        if idempotency_key is not None:
            key_hash = hash_idempotency_key(
                validate_idempotency_key(idempotency_key)
            )
            record = await self.idempotency.get(
                workspace_id, self.user_id, scope, key_hash
            )
            if record:
                if record.request_hash != request_hash:
                    raise AppError(
                        "idempotency_conflict", "该幂等键已用于不同请求", 409
                    )
                replay = await self.inspirations.get_in_workspace(
                    workspace_id, record.resource_id, include_deleted=True
                )
                if replay is None:
                    raise AppError("resource_not_found", "资源不存在", 404)
                return InspirationMutationResult(replay, record.response_json)
        await self.get(inspiration_id)
        tags = None
        if payload.tag_ids is not None:
            tags = await self._workspace_tags(workspace_id, payload.tag_ids)
        values: dict[str, object] = {
            "version": Inspiration.version + 1,
            "updated_at": utc_now(),
        }
        changed_fields: list[str] = []
        for field in ("title", "content", "summary", "expanded_content"):
            if field in payload.model_fields_set:
                values[field] = getattr(payload, field)
                changed_fields.append(field)
        if "source_url" in payload.model_fields_set:
            values["source_url"] = str(payload.source_url) if payload.source_url else None
            changed_fields.append("source_url")
        if tags is not None:
            changed_fields.append("tag_ids")
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Inspiration)
                .where(
                    Inspiration.workspace_id == workspace_id,
                    Inspiration.id == inspiration_id,
                    Inspiration.deleted_at.is_(None),
                    Inspiration.version == payload.version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            winner = await self._replay_winner(
                workspace_id, scope, key_hash, request_hash
            )
            if winner is not None:
                return winner
            raise AppError("version_conflict", "灵感已被其他操作更新，请刷新后重试", 409)
        if tags is not None:
            current = await self.inspirations.get_in_workspace(workspace_id, inspiration_id)
            assert current is not None
            current.tags = tags
        else:
            current = await self.inspirations.get_in_workspace(
                workspace_id, inspiration_id
            )
            assert current is not None
        await self.session.flush()
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.user_id,
            actor_role=actor_role,
            action="inspiration.updated",
            target_type="inspiration",
            target_id=inspiration_id,
            request_id=self.request_id,
            metadata={
                "from_version": payload.version,
                "to_version": payload.version + 1,
                "changed_fields": changed_fields,
            },
        )
        response_json = inspiration_response_snapshot(current)
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
                    resource_type="inspiration",
                    resource_id=inspiration_id,
                    expires_at=utc_now() + timedelta(days=1),
                )
            )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self._replay_winner(
                workspace_id,
                scope,
                key_hash,
                request_hash,
                conflict=exc,
            )
            if winner is not None:
                return winner
            raise AppError(
                "idempotency_conflict", "该幂等键已用于不同请求", 409
            ) from exc
        return InspirationMutationResult(
            await self.get(inspiration_id), response_json
        )

    async def delete(
        self,
        inspiration_id: UUID,
        version: int,
        idempotency_key: str | None = None,
    ) -> InspirationMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        scope = f"inspiration.delete:{inspiration_id}"
        request_hash = canonical_request_hash(
            {"inspiration_id": inspiration_id, "version": version}
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
        await self.get(inspiration_id)
        now = utc_now()
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Inspiration)
                .where(
                    Inspiration.workspace_id == workspace_id,
                    Inspiration.id == inspiration_id,
                    Inspiration.deleted_at.is_(None),
                    Inspiration.version == version,
                )
                .values(
                    deleted_at=now,
                    updated_at=now,
                    version=Inspiration.version + 1,
                )
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            replay = await self._replay_winner(
                workspace_id, scope, key_hash, request_hash
            )
            if replay is not None:
                return replay
            raise AppError("version_conflict", "灵感已被其他操作更新，请刷新后重试", 409)
        current = await self.inspirations.get_in_workspace(
            workspace_id, inspiration_id, include_deleted=True
        )
        assert current is not None
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.user_id,
            actor_role=actor_role,
            action="inspiration.deleted",
            target_type="inspiration",
            target_id=inspiration_id,
            request_id=self.request_id,
            metadata={"from_version": version, "to_version": version + 1},
        )
        response_json = inspiration_response_snapshot(current)
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
                    resource_type="inspiration",
                    resource_id=inspiration_id,
                    expires_at=utc_now() + timedelta(days=7),
                )
            )
        try:
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
            raise AppError(
                "idempotency_conflict", "该幂等键已用于不同请求", 409
            ) from exc
        return InspirationMutationResult(current, response_json)

    async def restore(
        self,
        inspiration_id: UUID,
        version: int,
        idempotency_key: str | None = None,
    ) -> InspirationMutationResult:
        workspace_id, actor_role = await self._workspace(MemberRole.EDITOR)
        scope = f"inspiration.restore:{inspiration_id}"
        request_hash = canonical_request_hash(
            {"inspiration_id": inspiration_id, "version": version}
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
        inspiration = await self.get(inspiration_id, include_deleted=True)
        if inspiration.deleted_at is None:
            raise AppError("not_deleted", "灵感当前未处于删除状态", 409)
        now = utc_now()
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Inspiration)
                .where(
                    Inspiration.workspace_id == workspace_id,
                    Inspiration.id == inspiration_id,
                    Inspiration.deleted_at.is_not(None),
                    Inspiration.version == version,
                )
                .values(
                    deleted_at=None,
                    updated_at=now,
                    version=Inspiration.version + 1,
                )
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            replay = await self._replay_winner(
                workspace_id, scope, key_hash, request_hash
            )
            if replay is not None:
                return replay
            raise AppError("version_conflict", "灵感已被其他操作更新，请刷新后重试", 409)
        current = await self.inspirations.get_in_workspace(
            workspace_id, inspiration_id
        )
        assert current is not None
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.user_id,
            actor_role=actor_role,
            action="inspiration.restored",
            target_type="inspiration",
            target_id=inspiration_id,
            request_id=self.request_id,
            metadata={"from_version": version, "to_version": version + 1},
        )
        response_json = inspiration_response_snapshot(current)
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
                    resource_type="inspiration",
                    resource_id=inspiration_id,
                    expires_at=utc_now() + timedelta(days=7),
                )
            )
        try:
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
            raise AppError(
                "idempotency_conflict", "该幂等键已用于不同请求", 409
            ) from exc
        return InspirationMutationResult(current, response_json)
