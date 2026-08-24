from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.config import Settings, get_settings
from app.knowledge.errors import OCRProviderUnavailableError
from app.knowledge.parsers import DocumentParser
from app.knowledge.types import BlockKind, ChunkKind, KnowledgeChunk, SourceBlock
from app.main import app
from app.models.base import utc_now
from app.models.knowledge import (
    ChunkSourceSpan,
    CompensationTask,
    DocumentChunk,
    DocumentVersion,
    DocumentVersionArtifact,
    OutboxEvent,
)
from app.services import knowledge as knowledge_service
from app.services.knowledge import KnowledgeService, _chunk_source_span_rows
from tests.conftest import auth_headers, register


async def _workspace(client: AsyncClient, token: str) -> dict:
    response = await client.get("/api/v1/workspaces", headers=auth_headers(token))
    assert response.status_code == 200
    return response.json()["items"][0]


class _DeterministicLayoutProvider:
    """Exercise the V2 service flow without requiring optional Docling wheels."""

    def __init__(self, *, limits) -> None:
        self._parser = DocumentParser(limits)

    def parse_layout(self, **kwargs):
        return self._parser.parse_layout(**kwargs)


async def test_v2_upload_publishes_manifest_spans_and_resolves_them(
    client: AsyncClient,
    session_factory,
    monkeypatch,
) -> None:
    settings = Settings().model_copy(
        update={
            "unstructured_pipeline_v2_enabled": True,
            "rag_ocr_enabled": False,
            "rag_ocr_engine": "DISABLED",
            "rag_sync_ingestion_enabled": True,
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        knowledge_service, "DoclingParserProvider", _DeterministicLayoutProvider
    )

    owner_token, _ = await register(client, "layout-owner@example.com")
    workspace_id = (await _workspace(client, owner_token))["id"]
    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers={
            **auth_headers(owner_token),
            "Idempotency-Key": "layout-v2-upload-001",
        },
        files={
            "file": (
                "生产制度.md",
                "# 生产权限\n\n普通生产权限最长保留 30 天。".encode(),
                "text/markdown",
            )
        },
        data={"sync": "true", "ocr_mode": "OFF"},
    )
    assert upload.status_code == 202, upload.text
    body = upload.json()
    assert body["job"]["status"] == "SUCCEEDED"
    assert body["job"]["stage_detail"] == "COMPLETE"
    assert body["version"]["status"] == "PUBLISHED"
    assert body["version"]["structure_schema_version"] == 2

    async with session_factory() as session:
        artifact = (
            await session.execute(select(DocumentVersionArtifact))
        ).scalar_one()
        chunks = (await session.execute(select(DocumentChunk))).scalars().all()
        spans = (await session.execute(select(ChunkSourceSpan))).scalars().all()
    child_chunks = [chunk for chunk in chunks if chunk.kind == "CHILD"]
    parent_chunks = [chunk for chunk in chunks if chunk.kind == "PARENT"]
    assert artifact.artifact_type == "LAYOUT_JSON"
    assert artifact.object_key.endswith("layout-v2.json")
    assert len(artifact.sha256) == 64
    assert child_chunks and parent_chunks
    assert all(chunk.indexed for chunk in child_chunks)
    assert all(not chunk.indexed for chunk in parent_chunks)
    assert {span.chunk_id for span in spans} == {chunk.id for chunk in child_chunks}

    search = await client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=auth_headers(owner_token),
        json={
            "query": "生产权限最长多久",
            "retrieval_mode": "HYBRID",
            "rerank": "OFF",
            "rewrite": "OFF",
            "response_mode": "HITS",
        },
    )
    assert search.status_code == 200, search.text
    hit = search.json()["hits"][0]
    assert hit["source_spans"]
    source_span_id = hit["source_spans"][0]["source_span_id"]
    assert hit["source_span_id"] == source_span_id
    resolve_path = (
        f"/api/v1/workspaces/{workspace_id}/documents/{body['document']['id']}"
        f"/versions/{body['version']['id']}/source-spans/{source_span_id}"
    )
    resolved = await client.get(resolve_path, headers=auth_headers(owner_token))
    assert resolved.status_code == 200, resolved.text
    resolved_body = resolved.json()
    assert resolved_body["source_span_id"] == source_span_id
    assert resolved_body["document_version_id"] == body["version"]["id"]
    assert resolved_body["excerpt"]
    assert len(resolved_body["excerpt_sha256"]) == 64

    other_token, _ = await register(client, "layout-outsider@example.com")
    other_workspace_id = (await _workspace(client, other_token))["id"]
    cross_tenant_path = resolve_path.replace(workspace_id, other_workspace_id)
    denied = await client.get(
        cross_tenant_path,
        headers=auth_headers(other_token),
    )
    assert denied.status_code == 404


class _HeartbeatResult:
    rowcount = 1


class _HeartbeatSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    async def execute(self, _statement):
        return _HeartbeatResult()


async def test_outbox_lease_heartbeat_stops_after_success_and_failure() -> None:
    session = _HeartbeatSession()
    service = KnowledgeService(
        session,  # type: ignore[arg-type]
        uuid4(),
        uuid4(),
        Settings(),
    )
    lease = knowledge_service._OutboxLease(uuid4(), "worker-1", 1)
    result = await service._await_with_outbox_lease(
        asyncio.sleep(0.035, result="parsed"),
        lease=lease,
        timeout=1,
        heartbeat_interval=0.01,
    )
    assert result == "parsed"
    assert session.commits >= 2
    commits_after_success = session.commits
    await asyncio.sleep(0.03)
    assert session.commits == commits_after_success

    async def fail() -> None:
        await asyncio.sleep(0.025)
        raise RuntimeError("parse failed")

    with pytest.raises(RuntimeError, match="parse failed"):
        await service._await_with_outbox_lease(
            fail(),
            lease=lease,
            timeout=1,
            heartbeat_interval=0.01,
        )
    commits_after_failure = session.commits
    await asyncio.sleep(0.03)
    assert session.commits == commits_after_failure


def test_source_span_offsets_match_trimmed_excerpt() -> None:
    workspace_id = uuid4()
    version = SimpleNamespace(id=uuid4(), document_id=uuid4())
    block = SourceBlock(
        kind=BlockKind.PARAGRAPH,
        text="  可复核证据  ",
        page=1,
        heading_path=(),
        char_start=100,
        char_end=109,
        block_key="page-1-block-0",
    )
    chunk = KnowledgeChunk(
        chunk_id=uuid4().hex,
        document_id=str(version.document_id),
        document_version_id=str(version.id),
        kind=ChunkKind.CHILD,
        ordinal=0,
        text=block.text,
        page=1,
        heading_path=(),
        char_start=100,
        char_end=109,
        source_span_id="legacy-span",
        parent_id=uuid4().hex,
    )
    rows = _chunk_source_span_rows(workspace_id, version, [block], [chunk])
    assert len(rows) == 1
    row = rows[0]
    assert block.text[row.block_char_start : row.block_char_end] == row.excerpt_text
    assert row.excerpt_text == "可复核证据"


async def test_retryable_parser_failure_replays_outbox_and_publishes_once(
    client: AsyncClient,
    session_factory,
    monkeypatch,
) -> None:
    settings = Settings().model_copy(
        update={
            "unstructured_pipeline_v2_enabled": True,
            "rag_sync_ingestion_enabled": True,
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings

    class _FlakyProvider(_DeterministicLayoutProvider):
        calls = 0

        def parse_layout(self, **kwargs):
            type(self).calls += 1
            if type(self).calls == 1:
                raise OCRProviderUnavailableError("OCR runtime initialization failed")
            return super().parse_layout(**kwargs)

    monkeypatch.setattr(knowledge_service, "DoclingParserProvider", _FlakyProvider)
    owner_token, user = await register(client, "layout-retry@example.com")
    workspace_id = (await _workspace(client, owner_token))["id"]
    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers={**auth_headers(owner_token), "Idempotency-Key": "layout-retry-001"},
        files={"file": ("retry.md", b"# Retry\n\nprovider recovery", "text/markdown")},
        data={"sync": "true", "ocr_mode": "OFF"},
    )
    assert upload.status_code == 202, upload.text
    assert upload.json()["job"]["status"] == "RETRY_WAIT"

    async with session_factory() as session:
        event = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == UUID(upload.json()["version"]["id"])
                )
            )
        ).scalar_one()
        stale_delete = CompensationTask(
            workspace_id=UUID(workspace_id),
            task_type="DELETE_DERIVED_OBJECT",
            resource_type="document_version_artifact",
            resource_id=UUID(upload.json()["version"]["id"]),
            object_key="stale/layout.json",
        )
        stale_deactivate = CompensationTask(
            workspace_id=UUID(workspace_id),
            task_type="DEACTIVATE_VECTORS",
            resource_type="document_version",
            resource_id=UUID(upload.json()["version"]["id"]),
        )
        session.add_all([stale_delete, stale_deactivate])
        await session.commit()
        service = KnowledgeService(
            session,
            UUID(user["id"]),
            UUID(workspace_id),
            settings,
        )
        assert await service.process_outbox_event(event.id) is True
        assert await service.process_outbox_event(event.id) is True
        monkeypatch.setattr(
            knowledge_service,
            "_object_store",
            lambda _settings: pytest.fail("published manifest must not be deleted"),
        )
        monkeypatch.setattr(
            knowledge_service,
            "_vector_store",
            lambda _settings: pytest.fail("published vectors must not be deactivated"),
        )
        assert await service.process_compensation_task(stale_delete.id) is True
        assert await service.process_compensation_task(stale_deactivate.id) is True
        versions = (await session.execute(select(DocumentVersion))).scalars().all()
        artifacts = (await session.execute(select(DocumentVersionArtifact))).scalars().all()
    assert len(versions) == 1
    assert versions[0].status == "PUBLISHED"
    assert len(artifacts) == 1


async def test_stale_outbox_attempt_is_fenced_after_lease_takeover(
    client: AsyncClient,
    session_factory,
) -> None:
    owner_token, user = await register(client, "layout-fence@example.com")
    workspace_id = (await _workspace(client, owner_token))["id"]
    upload = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers={**auth_headers(owner_token), "Idempotency-Key": "layout-fence-001"},
        files={"file": ("fence.md", b"# Fence\n\nlease fencing", "text/markdown")},
        data={"sync": "false", "ocr_mode": "OFF"},
    )
    assert upload.status_code == 202, upload.text
    version_id = UUID(upload.json()["version"]["id"])

    async with session_factory() as first:
        event = (
            await first.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == version_id)
            )
        ).scalar_one()
        event.status = "PROCESSING"
        event.attempts = 1
        event.lease_owner = "worker-old"
        event.lease_expires_at = utc_now() - timedelta(seconds=1)
        version = await first.get(DocumentVersion, version_id)
        assert version is not None
        version.status = "PUBLISHING"
        await first.commit()
        event_id = event.id
        stale_lease = knowledge_service._OutboxLease(event_id, "worker-old", 1)

        async with session_factory() as takeover:
            await takeover.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == event_id)
                .values(
                    attempts=2,
                    lease_owner="worker-new",
                    lease_expires_at=utc_now() + timedelta(seconds=60),
                )
            )
            await takeover.commit()

        service = KnowledgeService(
            first,
            UUID(user["id"]),
            UUID(workspace_id),
            Settings(),
        )
        with pytest.raises(
            knowledge_service._OutboxLeaseLost,
            match="lease was lost",
        ):
            await service._assert_outbox_lease(stale_lease)

    async with session_factory() as verify:
        claimed = await verify.get(OutboxEvent, event_id)
        assert claimed is not None
        assert claimed.status == "PROCESSING"
        assert claimed.lease_owner == "worker-new"
        assert claimed.attempts == 2
        version = await verify.get(DocumentVersion, version_id)
        assert version is not None
        assert version.status != "PUBLISHED"
