from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.base import utc_now
from app.models.entities import Inspiration, Tag
from app.repositories.inspirations import InspirationRepository
from app.repositories.tags import TagRepository
from app.schemas.inspirations import InspirationCreate, InspirationPatch


class InspirationService:
    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self.session = session
        self.user_id = user_id
        self.inspirations = InspirationRepository(session)
        self.tags = TagRepository(session)

    async def _owned_tags(
        self, tag_ids: builtins.list[UUID]
    ) -> builtins.list[Tag]:
        tags = await self.tags.get_many_owned(tag_ids, self.user_id)
        if len(tags) != len(tag_ids):
            raise AppError("invalid_tags", "包含不存在或不属于当前用户的标签", 422)
        by_id = {tag.id: tag for tag in tags}
        return [by_id[tag_id] for tag_id in tag_ids]

    async def create(self, payload: InspirationCreate) -> Inspiration:
        tags = await self._owned_tags(payload.tag_ids)
        inspiration = Inspiration(
            user_id=self.user_id,
            title=payload.title,
            content=payload.content,
            source_url=str(payload.source_url) if payload.source_url else None,
            tags=tags,
        )
        self.inspirations.add(inspiration)
        await self.session.commit()
        loaded = await self.inspirations.get_owned(inspiration.id, self.user_id)
        assert loaded is not None
        return loaded

    async def get(self, inspiration_id: UUID, *, include_deleted: bool = False) -> Inspiration:
        inspiration = await self.inspirations.get_owned(
            inspiration_id, self.user_id, include_deleted=include_deleted
        )
        if inspiration is None:
            raise AppError("not_found", "灵感不存在", 404)
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
        unique_tag_ids = list(dict.fromkeys(tag_ids or []))
        if unique_tag_ids:
            owned_tags = await self.tags.get_many_owned(unique_tag_ids, self.user_id)
            if len(owned_tags) != len(unique_tag_ids):
                raise AppError("not_found", "包含不存在的标签", 404)
        if date_from and date_from.tzinfo is None:
            raise AppError("invalid_date", "date_from 必须包含时区", 422)
        if date_to and date_to.tzinfo is None:
            raise AppError("invalid_date", "date_to 必须包含时区", 422)
        if date_from and date_to and date_from > date_to:
            raise AppError("invalid_date_range", "date_from 不能晚于 date_to", 422)
        return await self.inspirations.list(
            self.user_id,
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

    async def update(self, inspiration_id: UUID, payload: InspirationPatch) -> Inspiration:
        inspiration = await self.get(inspiration_id)
        tags = None
        if payload.tag_ids is not None:
            tags = await self._owned_tags(payload.tag_ids)
        values: dict[str, object] = {
            "version": Inspiration.version + 1,
            "updated_at": utc_now(),
        }
        for field in ("title", "content", "summary", "expanded_content"):
            if field in payload.model_fields_set:
                values[field] = getattr(payload, field)
        if "source_url" in payload.model_fields_set:
            values["source_url"] = str(payload.source_url) if payload.source_url else None
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Inspiration)
                .where(
                    Inspiration.id == inspiration_id,
                    Inspiration.user_id == self.user_id,
                    Inspiration.deleted_at.is_(None),
                    Inspiration.version == payload.version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise AppError("version_conflict", "灵感已被其他操作更新，请刷新后重试", 409)
        if tags is not None:
            inspiration.tags = tags
        await self.session.commit()
        return await self.get(inspiration_id)

    async def delete(self, inspiration_id: UUID, version: int) -> Inspiration:
        await self.get(inspiration_id)
        now = utc_now()
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Inspiration)
                .where(
                    Inspiration.id == inspiration_id,
                    Inspiration.user_id == self.user_id,
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
            raise AppError("version_conflict", "灵感已被其他操作更新，请刷新后重试", 409)
        await self.session.commit()
        return await self.get(inspiration_id, include_deleted=True)

    async def restore(self, inspiration_id: UUID, version: int) -> Inspiration:
        inspiration = await self.get(inspiration_id, include_deleted=True)
        if inspiration.deleted_at is None:
            raise AppError("not_deleted", "灵感当前未处于删除状态", 409)
        now = utc_now()
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Inspiration)
                .where(
                    Inspiration.id == inspiration_id,
                    Inspiration.user_id == self.user_id,
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
            raise AppError("version_conflict", "灵感已被其他操作更新，请刷新后重试", 409)
        await self.session.commit()
        return await self.get(inspiration_id)
