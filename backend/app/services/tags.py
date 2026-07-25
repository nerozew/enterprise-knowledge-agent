from __future__ import annotations

import unicodedata
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.base import utc_now
from app.models.entities import InspirationTag, Tag
from app.repositories.tags import TagRepository
from app.schemas.tags import TagCreate, TagPatch


def normalize_tag_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class TagService:
    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self.session = session
        self.user_id = user_id
        self.tags = TagRepository(session)

    async def list(self) -> list[tuple[Tag, int]]:
        return await self.tags.list_with_usage_counts(self.user_id)

    async def create(self, payload: TagCreate) -> tuple[Tag, int]:
        normalized = normalize_tag_name(payload.name)
        if not normalized:
            raise AppError("invalid_tag_name", "标签名称不能为空", 422)
        if await self.tags.get_by_normalized_name(self.user_id, normalized):
            raise AppError("tag_exists", "同名标签已存在", 409)
        tag = Tag(
            user_id=self.user_id,
            name=payload.name,
            normalized_name=normalized,
            color=payload.color,
        )
        self.tags.add(tag)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AppError("tag_exists", "同名标签已存在", 409) from exc
        await self.session.refresh(tag)
        return tag, 0

    async def update(self, tag_id: UUID, payload: TagPatch) -> tuple[Tag, int]:
        tag = await self.tags.get_owned(tag_id, self.user_id)
        if tag is None:
            raise AppError("not_found", "标签不存在", 404)
        values: dict[str, object] = {"version": Tag.version + 1, "updated_at": utc_now()}
        if "name" in payload.model_fields_set:
            assert payload.name is not None
            values["name"] = payload.name
            values["normalized_name"] = normalize_tag_name(payload.name)
        if "color" in payload.model_fields_set:
            values["color"] = payload.color
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Tag)
                .where(
                    Tag.id == tag_id,
                    Tag.user_id == self.user_id,
                    Tag.version == payload.version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise AppError("version_conflict", "标签已被其他操作更新，请刷新后重试", 409)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AppError("tag_exists", "同名标签已存在", 409) from exc
        updated = await self.tags.get_owned(tag_id, self.user_id)
        assert updated is not None
        return updated, await self.tags.usage_count(tag_id)

    async def delete(self, tag_id: UUID, version: int, *, force: bool = False) -> None:
        tag = await self.tags.get_owned(tag_id, self.user_id)
        if tag is None:
            raise AppError("not_found", "标签不存在", 404)
        if tag.version != version:
            raise AppError("version_conflict", "标签已被其他操作更新，请刷新后重试", 409)
        usage_count = await self.tags.usage_count(tag_id)
        if usage_count and not force:
            raise AppError(
                "tag_in_use",
                "标签正在被灵感使用；确认解除关联后请使用 force=true",
                409,
                {"usage_count": usage_count},
            )
        if usage_count:
            await self.session.execute(
                delete(InspirationTag).where(InspirationTag.tag_id == tag_id)
            )
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                delete(Tag).where(
                    Tag.id == tag_id,
                    Tag.user_id == self.user_id,
                    Tag.version == version,
                )
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise AppError("version_conflict", "标签已被其他操作更新，请刷新后重试", 409)
        await self.session.commit()
