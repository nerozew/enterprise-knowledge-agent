from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import Inspiration, Tag


class InspirationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _workspace_query(self, workspace_id: UUID, inspiration_id: UUID) -> Select:
        return (
            select(Inspiration)
            .options(selectinload(Inspiration.tags))
            .where(
                Inspiration.workspace_id == workspace_id,
                Inspiration.id == inspiration_id,
            )
        )

    async def get_in_workspace(
        self, workspace_id: UUID, inspiration_id: UUID, *, include_deleted: bool = False
    ) -> Inspiration | None:
        query = self._workspace_query(workspace_id, inspiration_id)
        if not include_deleted:
            query = query.where(Inspiration.deleted_at.is_(None))
        return await self.session.scalar(query)

    async def list(
        self,
        workspace_id: UUID,
        *,
        query_text: str | None,
        tag_ids: list[UUID],
        tag_match: str,
        date_from: datetime | None,
        date_to: datetime | None,
        sort: str,
        include_deleted: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[Inspiration], int]:
        filters = [Inspiration.workspace_id == workspace_id]
        if not include_deleted:
            filters.append(Inspiration.deleted_at.is_(None))
        if query_text:
            pattern = f"%{query_text}%"
            filters.append(
                or_(
                    Inspiration.title.ilike(pattern),
                    Inspiration.content.ilike(pattern),
                    Inspiration.summary.ilike(pattern),
                    Inspiration.expanded_content.ilike(pattern),
                )
            )
        if date_from:
            filters.append(Inspiration.created_at >= date_from)
        if date_to:
            filters.append(Inspiration.created_at <= date_to)

        ids_query = select(Inspiration.id).where(*filters)
        if tag_ids:
            ids_query = (
                ids_query.join(Inspiration.tags)
                .where(Tag.id.in_(tag_ids), Tag.workspace_id == workspace_id)
                .group_by(Inspiration.id)
            )
            if tag_match == "all":
                ids_query = ids_query.having(func.count(func.distinct(Tag.id)) == len(tag_ids))

        sort_column = (
            Inspiration.created_at if sort.startswith("created_") else Inspiration.updated_at
        )
        sort_expression = sort_column.asc() if sort.endswith("_asc") else sort_column.desc()
        id_expression = Inspiration.id.asc() if sort.endswith("_asc") else Inspiration.id.desc()
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(ids_query.order_by(None).subquery())
            )
            or 0
        )
        page_ids = list(
            (
                await self.session.scalars(
                    ids_query.order_by(sort_expression, id_expression)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        if not page_ids:
            return [], total
        rows = list(
            (
                await self.session.scalars(
                    select(Inspiration)
                    .options(selectinload(Inspiration.tags))
                    .where(
                        Inspiration.workspace_id == workspace_id,
                        Inspiration.id.in_(page_ids),
                    )
                )
            )
            .unique()
            .all()
        )
        by_id = {item.id: item for item in rows}
        return [by_id[item_id] for item_id in page_ids], total

    def add(self, inspiration: Inspiration) -> None:
        self.session.add(inspiration)
