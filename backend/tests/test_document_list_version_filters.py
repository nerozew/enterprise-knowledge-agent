from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.base import utc_now
from app.models.entities import User, Workspace
from app.models.knowledge import Document, DocumentVersion
from app.repositories.knowledge import KnowledgeRepository


def _version(
    *,
    version_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    user_id: UUID,
    source_format: str,
    status: str,
    created_at: datetime,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        workspace_id=workspace_id,
        document_id=document_id,
        created_by_user_id=user_id,
        format=source_format,
        mime_type="text/plain",
        file_name=f"{version_id}.{source_format.lower()}",
        file_size=10,
        file_sha256=version_id.hex.ljust(64, "0"),
        object_key=f"knowledge/{workspace_id}/{version_id}",
        status=status,
        created_at=created_at,
        published_at=created_at if status == "PUBLISHED" else None,
    )


@pytest.mark.asyncio
async def test_repository_document_list_uses_one_workspace_scoped_target_version(
    session_factory,
) -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    current_published_document_id = uuid4()
    latest_failed_document_id = uuid4()
    deleted_document_id = uuid4()
    other_document_id = uuid4()
    now = utc_now()

    current_published_id = UUID("00000000-0000-0000-0000-000000000012")
    historical_failed_id = UUID("00000000-0000-0000-0000-000000000011")
    published_before_failure_id = UUID("00000000-0000-0000-0000-000000000021")
    latest_failed_id = UUID("00000000-0000-0000-0000-000000000022")
    deleted_published_id = UUID("00000000-0000-0000-0000-000000000031")
    other_published_id = UUID("00000000-0000-0000-0000-000000000041")

    async with session_factory() as session:
        session.add(
            User(
                id=user_id,
                email="repository-filter@example.com",
                password_hash="hash",
                display_name="Repository filter",
            )
        )
        session.add_all(
            [
                Workspace(
                    id=workspace_id,
                    name="Workspace A",
                    type="TEAM",
                    status="ACTIVE",
                ),
                Workspace(
                    id=other_workspace_id,
                    name="Workspace B",
                    type="TEAM",
                    status="ACTIVE",
                ),
            ]
        )
        documents = [
            Document(
                id=current_published_document_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                title="Current published",
                status="ACTIVE",
            ),
            Document(
                id=latest_failed_document_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                title="Latest failed",
                status="ACTIVE",
            ),
            Document(
                id=deleted_document_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                title="Deleted",
                status="DELETED",
            ),
            Document(
                id=other_document_id,
                workspace_id=other_workspace_id,
                created_by_user_id=user_id,
                title="Other workspace",
                status="ACTIVE",
            ),
        ]
        session.add_all(documents)
        await session.flush()

        session.add_all(
            [
                _version(
                    version_id=historical_failed_id,
                    workspace_id=workspace_id,
                    document_id=current_published_document_id,
                    user_id=user_id,
                    source_format="TXT",
                    status="FAILED",
                    created_at=now - timedelta(minutes=2),
                ),
                _version(
                    version_id=current_published_id,
                    workspace_id=workspace_id,
                    document_id=current_published_document_id,
                    user_id=user_id,
                    source_format="MARKDOWN",
                    status="PUBLISHED",
                    created_at=now - timedelta(minutes=1),
                ),
                _version(
                    version_id=published_before_failure_id,
                    workspace_id=workspace_id,
                    document_id=latest_failed_document_id,
                    user_id=user_id,
                    source_format="TXT",
                    status="PUBLISHED",
                    created_at=now,
                ),
                _version(
                    version_id=latest_failed_id,
                    workspace_id=workspace_id,
                    document_id=latest_failed_document_id,
                    user_id=user_id,
                    source_format="MARKDOWN",
                    status="FAILED",
                    created_at=now,
                ),
                _version(
                    version_id=deleted_published_id,
                    workspace_id=workspace_id,
                    document_id=deleted_document_id,
                    user_id=user_id,
                    source_format="MARKDOWN",
                    status="PUBLISHED",
                    created_at=now,
                ),
                _version(
                    version_id=other_published_id,
                    workspace_id=other_workspace_id,
                    document_id=other_document_id,
                    user_id=user_id,
                    source_format="MARKDOWN",
                    status="PUBLISHED",
                    created_at=now,
                ),
            ]
        )
        await session.flush()
        documents[0].published_version_id = current_published_id
        documents[1].published_version_id = published_before_failure_id
        documents[2].published_version_id = deleted_published_id
        documents[3].published_version_id = other_published_id
        await session.commit()

        repository = KnowledgeRepository(session)

        markdown_published, total = await repository.list_documents(
            workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            source_format="MARKDOWN",
            version_status="PUBLISHED",
        )
        assert total == 1
        assert [row.id for row in markdown_published] == [current_published_document_id]

        failed, total = await repository.list_documents(
            workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            version_status="FAILED",
        )
        assert total == 1
        assert [row.id for row in failed] == [latest_failed_document_id]

        failed_markdown, total = await repository.list_documents(
            workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            source_format="MARKDOWN",
            version_status="FAILED",
        )
        assert total == 1
        assert [row.id for row in failed_markdown] == [latest_failed_document_id]

        failed_txt, total = await repository.list_documents(
            workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            source_format="TXT",
            version_status="FAILED",
        )
        assert total == 0
        assert failed_txt == []

        published_txt, total = await repository.list_documents(
            workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            source_format="TXT",
        )
        assert total == 1
        assert [row.id for row in published_txt] == [latest_failed_document_id]

        deleted, total = await repository.list_documents(
            workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            status="DELETED",
        )
        assert total == 1
        assert [row.id for row in deleted] == [deleted_document_id]

        other_rows, total = await repository.list_documents(
            other_workspace_id,
            include_deleted=False,
            page=1,
            page_size=20,
            source_format="MARKDOWN",
            version_status="PUBLISHED",
        )
        assert total == 1
        assert [row.id for row in other_rows] == [other_document_id]

        versions = await repository.list_versions(
            workspace_id,
            latest_failed_document_id,
        )
        assert [version.id for version in versions] == [
            latest_failed_id,
            published_before_failure_id,
        ]
