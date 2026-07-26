from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    IngestionJob,
)


class KnowledgeRepository:
    """All resource reads require an explicit Workspace scope."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, entity: object) -> None:
        self.session.add(entity)

    async def get_document(
        self, workspace_id: UUID, document_id: UUID, *, for_update: bool = False
    ) -> Document | None:
        query = select(Document).where(
            Document.workspace_id == workspace_id, Document.id == document_id
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def list_documents(
        self,
        workspace_id: UUID,
        *,
        include_deleted: bool,
        page: int,
        page_size: int,
        published_only: bool = False,
        q: str | None = None,
        source_format: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Document], int]:
        filters = [Document.workspace_id == workspace_id]
        if status is not None:
            filters.append(Document.status == status)
        elif not include_deleted:
            filters.append(Document.status == DocumentStatus.ACTIVE.value)
        if published_only:
            filters.extend(
                [
                    Document.status == DocumentStatus.ACTIVE.value,
                    Document.published_version_id.is_not(None),
                ]
            )
        if q:
            filters.append(Document.title.contains(q, autoescape=True))
        if source_format:
            filters.append(
                exists(
                    select(DocumentVersion.id).where(
                        DocumentVersion.workspace_id == workspace_id,
                        DocumentVersion.document_id == Document.id,
                        DocumentVersion.format == source_format,
                        DocumentVersion.status == DocumentVersionStatus.PUBLISHED.value,
                    )
                )
            )
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(Document).where(*filters)
            )
            or 0
        )
        rows = list(
            (
                await self.session.scalars(
                    select(Document)
                    .where(*filters)
                    .order_by(Document.updated_at.desc(), Document.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return rows, total

    async def get_version(
        self, workspace_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        return await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.workspace_id == workspace_id,
                DocumentVersion.id == version_id,
            )
        )

    async def get_version_by_hash(
        self, workspace_id: UUID, file_sha256: str
    ) -> DocumentVersion | None:
        return await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.workspace_id == workspace_id,
                DocumentVersion.file_sha256 == file_sha256,
                DocumentVersion.status.in_(
                    [
                        DocumentVersionStatus.PUBLISHING.value,
                        DocumentVersionStatus.PUBLISHED.value,
                    ]
                ),
            )
            .order_by(DocumentVersion.created_at.desc())
            .limit(1)
        )

    async def list_versions(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        published_version_id: UUID | None = None,
    ) -> list[DocumentVersion]:
        filters = [
            DocumentVersion.workspace_id == workspace_id,
            DocumentVersion.document_id == document_id,
        ]
        if published_version_id is not None:
            filters.extend(
                [
                    DocumentVersion.id == published_version_id,
                    DocumentVersion.status == DocumentVersionStatus.PUBLISHED.value,
                ]
            )
        return list(
            (
                await self.session.scalars(
                    select(DocumentVersion)
                    .where(*filters)
                    .order_by(DocumentVersion.created_at.desc())
                )
            ).all()
        )

    async def get_job(
        self, workspace_id: UUID, job_id: UUID, *, for_update: bool = False
    ) -> IngestionJob | None:
        query = select(IngestionJob).where(
            IngestionJob.workspace_id == workspace_id, IngestionJob.id == job_id
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def latest_job_for_version(
        self, workspace_id: UUID, version_id: UUID
    ) -> IngestionJob | None:
        return await self.session.scalar(
            select(IngestionJob)
            .where(
                IngestionJob.workspace_id == workspace_id,
                IngestionJob.document_version_id == version_id,
            )
            .order_by(IngestionJob.created_at.desc())
            .limit(1)
        )

    async def delete_chunks_for_version(
        self, workspace_id: UUID, version_id: UUID
    ) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.document_version_id == version_id,
            )
        )

    async def published_chunks(
        self,
        workspace_id: UUID,
        *,
        document_ids: list[UUID],
        formats: list[str],
        published_from: datetime | None,
        published_to: datetime | None,
    ) -> list[tuple[DocumentChunk, Document, DocumentVersion]]:
        query = (
            select(DocumentChunk, Document, DocumentVersion)
            .join(
                Document,
                (Document.workspace_id == DocumentChunk.workspace_id)
                & (Document.id == DocumentChunk.document_id),
            )
            .join(
                DocumentVersion,
                (DocumentVersion.workspace_id == DocumentChunk.workspace_id)
                & (DocumentVersion.id == DocumentChunk.document_version_id),
            )
            .where(
                DocumentChunk.workspace_id == workspace_id,
                Document.status == DocumentStatus.ACTIVE.value,
                Document.published_version_id == DocumentChunk.document_version_id,
                DocumentVersion.status == DocumentVersionStatus.PUBLISHED.value,
            )
        )
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        if formats:
            query = query.where(DocumentVersion.format.in_(formats))
        if published_from:
            query = query.where(DocumentVersion.published_at >= published_from)
        if published_to:
            query = query.where(DocumentVersion.published_at <= published_to)
        rows = (
            await self.session.execute(
                query.order_by(DocumentChunk.document_version_id, DocumentChunk.ordinal)
            )
        ).all()
        return [(row[0], row[1], row[2]) for row in rows]

    async def context_chunks(
        self,
        workspace_id: UUID,
        version_id: UUID,
        parent_id: UUID | None,
        ordinal: int,
    ) -> list[DocumentChunk]:
        filters = [
            DocumentChunk.workspace_id == workspace_id,
            DocumentChunk.document_version_id == version_id,
        ]
        if parent_id is not None:
            filters.append(
                (DocumentChunk.id == parent_id)
                | (
                    (DocumentChunk.kind == "CHILD")
                    & (DocumentChunk.parent_chunk_id == parent_id)
                    & (DocumentChunk.ordinal.between(ordinal - 1, ordinal + 1))
                )
            )
        else:
            filters.append(
                (DocumentChunk.kind == "CHILD")
                & (DocumentChunk.ordinal.between(ordinal - 1, ordinal + 1))
            )
        return list(
            (
                await self.session.scalars(
                    select(DocumentChunk)
                    .where(*filters)
                    .order_by(DocumentChunk.kind.desc(), DocumentChunk.ordinal)
                )
            ).all()
        )

    async def published_chunks_by_ids(
        self, workspace_id: UUID, chunk_ids: list[UUID]
    ) -> list[tuple[DocumentChunk, Document, DocumentVersion]]:
        if not chunk_ids:
            return []
        rows = (
            await self.session.execute(
                select(DocumentChunk, Document, DocumentVersion)
                .join(
                    Document,
                    (Document.workspace_id == DocumentChunk.workspace_id)
                    & (Document.id == DocumentChunk.document_id),
                )
                .join(
                    DocumentVersion,
                    (DocumentVersion.workspace_id == DocumentChunk.workspace_id)
                    & (DocumentVersion.id == DocumentChunk.document_version_id),
                )
                .where(
                    DocumentChunk.workspace_id == workspace_id,
                    DocumentChunk.id.in_(chunk_ids),
                    DocumentChunk.kind == "CHILD",
                    Document.status == DocumentStatus.ACTIVE.value,
                    Document.published_version_id == DocumentChunk.document_version_id,
                    DocumentVersion.status == DocumentVersionStatus.PUBLISHED.value,
                )
            )
        ).all()
        return [(row[0], row[1], row[2]) for row in rows]
