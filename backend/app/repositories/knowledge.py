from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.knowledge import (
    ChunkSourceSpan,
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionArtifact,
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
        version_status: str | None = None,
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
        if source_format is not None or version_status is not None:
            target_version = aliased(DocumentVersion)
            target_status = (
                version_status
                if version_status is not None
                else DocumentVersionStatus.PUBLISHED.value
            )
            if target_status == DocumentVersionStatus.PUBLISHED.value:
                target_id_filter = target_version.id == Document.published_version_id
            else:
                latest_version = aliased(DocumentVersion)
                latest_version_id = (
                    select(latest_version.id)
                    .where(
                        latest_version.workspace_id == workspace_id,
                        latest_version.document_id == Document.id,
                    )
                    .order_by(
                        latest_version.created_at.desc(),
                        latest_version.id.desc(),
                    )
                    .limit(1)
                    .correlate(Document)
                    .scalar_subquery()
                )
                target_id_filter = target_version.id == latest_version_id
            version_filters = [
                target_version.workspace_id == workspace_id,
                target_version.document_id == Document.id,
                target_id_filter,
                target_version.status == target_status,
            ]
            if source_format is not None:
                version_filters.append(target_version.format == source_format)
            filters.append(
                exists(select(target_version.id).where(*version_filters).correlate(Document))
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
                    .order_by(
                        DocumentVersion.created_at.desc(),
                        DocumentVersion.id.desc(),
                    )
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

    async def delete_artifacts_for_version(
        self, workspace_id: UUID, version_id: UUID
    ) -> list[DocumentVersionArtifact]:
        artifacts = list(
            (
                await self.session.scalars(
                    select(DocumentVersionArtifact).where(
                        DocumentVersionArtifact.workspace_id == workspace_id,
                        DocumentVersionArtifact.document_version_id == version_id,
                    )
                )
            ).all()
        )
        await self.session.execute(
            delete(DocumentVersionArtifact).where(
                DocumentVersionArtifact.workspace_id == workspace_id,
                DocumentVersionArtifact.document_version_id == version_id,
            )
        )
        return artifacts

    async def source_spans_by_chunk_ids(
        self, workspace_id: UUID, chunk_ids: list[UUID]
    ) -> dict[UUID, list[ChunkSourceSpan]]:
        if not chunk_ids:
            return {}
        rows = list(
            (
                await self.session.scalars(
                    select(ChunkSourceSpan)
                    .where(
                        ChunkSourceSpan.workspace_id == workspace_id,
                        ChunkSourceSpan.chunk_id.in_(chunk_ids),
                    )
                    .order_by(ChunkSourceSpan.chunk_id, ChunkSourceSpan.span_ordinal)
                )
            ).all()
        )
        grouped: dict[UUID, list[ChunkSourceSpan]] = {}
        for row in rows:
            grouped.setdefault(row.chunk_id, []).append(row)
        return grouped

    async def resolve_source_span(
        self,
        workspace_id: UUID,
        document_id: UUID,
        version_id: UUID,
        source_span_id: str,
        *,
        published_only: bool,
    ) -> tuple[ChunkSourceSpan, DocumentVersion] | None:
        query = (
            select(ChunkSourceSpan, DocumentVersion)
            .join(
                DocumentVersion,
                (DocumentVersion.workspace_id == ChunkSourceSpan.workspace_id)
                & (DocumentVersion.id == ChunkSourceSpan.document_version_id),
            )
            .join(
                Document,
                (Document.workspace_id == ChunkSourceSpan.workspace_id)
                & (Document.id == ChunkSourceSpan.document_id),
            )
            .where(
                ChunkSourceSpan.workspace_id == workspace_id,
                ChunkSourceSpan.document_id == document_id,
                ChunkSourceSpan.document_version_id == version_id,
                ChunkSourceSpan.source_span_id == source_span_id,
            )
        )
        if published_only:
            query = query.where(
                Document.status == DocumentStatus.ACTIVE.value,
                Document.published_version_id == version_id,
                DocumentVersion.status == DocumentVersionStatus.PUBLISHED.value,
            )
        row = (await self.session.execute(query)).first()
        if row is None:
            return None
        return row[0], row[1]

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
