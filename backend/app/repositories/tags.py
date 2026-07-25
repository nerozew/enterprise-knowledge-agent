from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import InspirationTag, Tag


class TagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_with_usage_counts(self, user_id: UUID) -> list[tuple[Tag, int]]:
        result = await self.session.execute(
            select(Tag, func.count(InspirationTag.inspiration_id))
            .outerjoin(InspirationTag, InspirationTag.tag_id == Tag.id)
            .where(Tag.user_id == user_id)
            .group_by(Tag.id)
            .order_by(Tag.name.asc())
        )
        return [(tag, int(usage_count)) for tag, usage_count in result.all()]

    async def usage_count(self, tag_id: UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(InspirationTag)
                .where(InspirationTag.tag_id == tag_id)
            )
            or 0
        )

    async def get_owned(self, tag_id: UUID, user_id: UUID) -> Tag | None:
        return await self.session.scalar(
            select(Tag).where(Tag.id == tag_id, Tag.user_id == user_id)
        )

    async def get_many_owned(
        self, tag_ids: Sequence[UUID], user_id: UUID
    ) -> Sequence[Tag]:
        if not tag_ids:
            return []
        result = await self.session.scalars(
            select(Tag).where(Tag.user_id == user_id, Tag.id.in_(tag_ids))
        )
        return list(result.all())

    async def get_by_normalized_name(self, user_id: UUID, normalized_name: str) -> Tag | None:
        return await self.session.scalar(
            select(Tag).where(
                Tag.user_id == user_id, Tag.normalized_name == normalized_name
            )
        )

    def add(self, tag: Tag) -> None:
        self.session.add(tag)
