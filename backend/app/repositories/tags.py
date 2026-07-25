from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import InspirationTag, Tag


class TagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_with_usage_counts(self, workspace_id: UUID) -> list[tuple[Tag, int]]:
        result = await self.session.execute(
            select(Tag, func.count(InspirationTag.inspiration_id))
            .outerjoin(
                InspirationTag,
                and_(
                    InspirationTag.workspace_id == Tag.workspace_id,
                    InspirationTag.tag_id == Tag.id,
                ),
            )
            .where(Tag.workspace_id == workspace_id)
            .group_by(Tag.id)
            .order_by(Tag.name.asc())
        )
        return [(tag, int(usage_count)) for tag, usage_count in result.all()]

    async def usage_count(self, workspace_id: UUID, tag_id: UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(InspirationTag)
                .where(
                    InspirationTag.workspace_id == workspace_id,
                    InspirationTag.tag_id == tag_id,
                )
            )
            or 0
        )

    async def get_in_workspace(self, workspace_id: UUID, tag_id: UUID) -> Tag | None:
        return await self.session.scalar(
            select(Tag).where(Tag.workspace_id == workspace_id, Tag.id == tag_id)
        )

    async def get_many_in_workspace(
        self, workspace_id: UUID, tag_ids: Sequence[UUID]
    ) -> Sequence[Tag]:
        if not tag_ids:
            return []
        result = await self.session.scalars(
            select(Tag).where(Tag.workspace_id == workspace_id, Tag.id.in_(tag_ids))
        )
        return list(result.all())

    async def get_by_normalized_name(
        self, workspace_id: UUID, normalized_name: str
    ) -> Tag | None:
        return await self.session.scalar(
            select(Tag).where(
                Tag.workspace_id == workspace_id,
                Tag.normalized_name == normalized_name,
            )
        )

    def add(self, tag: Tag) -> None:
        self.session.add(tag)
