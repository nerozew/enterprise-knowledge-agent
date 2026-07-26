from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.main import app
from app.models.knowledge import DocumentVersion, OutboxEvent
from app.services.knowledge import KnowledgeService, _object_store
from tests.conftest import auth_headers, register


async def _workspace(client: AsyncClient, token: str) -> dict:
    response = await client.get("/api/v1/workspaces", headers=auth_headers(token))
    assert response.status_code == 200
    return response.json()["items"][0]


async def _team_workspace(client: AsyncClient, token: str, key: str) -> dict:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "知识团队"},
        headers={**auth_headers(token), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _upload(
    client: AsyncClient,
    token: str,
    workspace_id: str,
    *,
    content: bytes,
    filename: str = "制度.md",
    key: str = "document-upload-key-001",
    document_id: str | None = None,
) -> dict:
    suffix = (
        f"/documents/{document_id}/versions" if document_id else "/documents"
    )
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}{suffix}",
        headers={**auth_headers(token), "Idempotency-Key": key},
        files={
            "file": (
                filename,
                content,
                "text/plain" if filename.endswith(".txt") else "text/markdown",
            )
        },
        data={"sync": "true"},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_document_upload_dedupe_search_citations_and_answer_dependency(
    client: AsyncClient,
):
    token, _ = await register(client, "knowledge-owner@example.com")
    workspace = await _workspace(client, token)
    workspace_id = workspace["id"]
    content = (
        "# 生产权限\n\n普通生产权限最长保留 30 天，到期后必须重新审批。\n\n"
        "Break Glass 权限最长 2 小时，事后 24 小时内补充复盘材料。"
    ).encode()
    first = await _upload(client, token, workspace_id, content=content)
    assert first["reused"] is False
    assert first["job"]["status"] == "SUCCEEDED"
    assert first["version"]["status"] == "PUBLISHED"
    assert first["document"]["published_version_id"] == first["version"]["id"]

    replay = await _upload(client, token, workspace_id, content=content)
    assert replay["reused"] is True
    assert replay["job"]["id"] == first["job"]["id"]

    duplicate = await _upload(
        client,
        token,
        workspace_id,
        content=content,
        key="document-upload-key-002",
    )
    assert duplicate["reused"] is True
    documents = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers=auth_headers(token),
    )
    assert documents.status_code == 200
    assert documents.json()["total"] == 1

    search = await client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=auth_headers(token),
        json={
            "query": "Break Glass 权限最长多久",
            "retrieval_mode": "HYBRID",
            "top_k": 5,
            "candidate_k": 30,
            "fusion": "RRF",
            "rerank": "AUTO",
            "rewrite": "OFF",
            "multi_query": False,
            "expand_context": True,
            "response_mode": "HITS",
        },
    )
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["provider"]["mode"] == "FIXTURE"
    assert body["hits"]
    assert body["hits"][0]["document_version_id"] == first["version"]["id"]
    assert body["hits"][0]["source_span_id"]
    assert body["hits"][0]["quote"]
    assert "CONTEXT_EXPANSION" in body["components"]

    answer = await client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=auth_headers(token),
        json={"query": "生产权限多久", "response_mode": "ANSWER"},
    )
    assert answer.status_code == 503
    assert answer.json()["error"]["code"] == "llm_dependency_unavailable"


async def test_failed_new_version_keeps_old_published_pointer(client: AsyncClient):
    token, _ = await register(client, "version-owner@example.com")
    workspace_id = (await _workspace(client, token))["id"]
    first = await _upload(
        client,
        token,
        workspace_id,
        content="# 有效制度\n\n旧发布版本继续提供检索。".encode(),
        key="version-upload-key-001",
    )
    failed = await _upload(
        client,
        token,
        workspace_id,
        content=b"\x00binary",
        filename="bad.md",
        key="version-upload-key-002",
        document_id=first["document"]["id"],
    )
    assert failed["job"]["status"] == "FAILED"
    assert failed["version"]["status"] == "FAILED"
    current = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{first['document']['id']}",
        headers=auth_headers(token),
    )
    assert current.status_code == 200
    assert current.json()["published_version_id"] == first["version"]["id"]


async def test_cross_workspace_document_and_job_are_hidden(client: AsyncClient):
    owner_token, _ = await register(client, "tenant-one@example.com")
    other_token, _ = await register(client, "tenant-two@example.com")
    owner_workspace = (await _workspace(client, owner_token))["id"]
    other_workspace = (await _workspace(client, other_token))["id"]
    uploaded = await _upload(
        client,
        owner_token,
        owner_workspace,
        content="# 租户隔离\n\n这段内容只属于第一个 Workspace。".encode(),
        key="tenant-upload-key-001",
    )
    document = await client.get(
        f"/api/v1/workspaces/{other_workspace}/documents/{uploaded['document']['id']}",
        headers=auth_headers(other_token),
    )
    job = await client.get(
        f"/api/v1/workspaces/{other_workspace}/ingestion-jobs/{uploaded['job']['id']}",
        headers=auth_headers(other_token),
    )
    assert document.status_code == 404
    assert job.status_code == 404

    search = await client.post(
        f"/api/v1/workspaces/{other_workspace}/search",
        headers=auth_headers(other_token),
        json={"query": "租户隔离", "response_mode": "HITS"},
    )
    assert search.status_code == 200
    assert search.json()["hits"] == []


async def test_viewer_cannot_read_ingestion_jobs_or_unpublished_documents(
    client: AsyncClient,
):
    owner_token, _ = await register(client, "knowledge-rbac-owner@example.com")
    viewer_token, _ = await register(client, "knowledge-rbac-viewer@example.com")
    team = await _team_workspace(client, owner_token, "knowledge-rbac-team")
    added = await client.post(
        f"/api/v1/workspaces/{team['id']}/members",
        json={"email": "knowledge-rbac-viewer@example.com", "role": "VIEWER"},
        headers={
            **auth_headers(owner_token),
            "Idempotency-Key": "knowledge-rbac-viewer",
        },
    )
    assert added.status_code == 201, added.text
    published = await _upload(
        client,
        owner_token,
        team["id"],
        content="# 已发布制度\n\n仅已发布内容可见。".encode(),
        key="knowledge-rbac-published",
    )
    await _upload(
        client,
        owner_token,
        team["id"],
        content=b"\x00broken",
        filename="failed.md",
        key="knowledge-rbac-failed",
    )

    visible = await client.get(
        f"/api/v1/workspaces/{team['id']}/documents",
        params={"include_deleted": True},
        headers=auth_headers(viewer_token),
    )
    job = await client.get(
        f"/api/v1/workspaces/{team['id']}/ingestion-jobs/{published['job']['id']}",
        headers=auth_headers(viewer_token),
    )
    assert visible.status_code == 200
    assert [item["id"] for item in visible.json()["items"]] == [
        published["document"]["id"]
    ]
    assert job.status_code == 403
    assert job.json()["error"]["code"] == "permission_denied"


async def test_document_list_combines_title_format_and_status_filters(
    client: AsyncClient,
):
    token, _ = await register(client, "knowledge-filter-owner@example.com")
    workspace_id = (await _workspace(client, token))["id"]
    markdown = await _upload(
        client,
        token,
        workspace_id,
        content="# 运维制度\n\nMarkdown 内容。".encode(),
        filename="运维制度.md",
        key="knowledge-filter-markdown",
    )
    text = await _upload(
        client,
        token,
        workspace_id,
        content="安全手册正文。".encode(),
        filename="安全手册.txt",
        key="knowledge-filter-text",
    )

    active = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"q": "安全", "source_format": "TXT", "status": "ACTIVE"},
        headers=auth_headers(token),
    )
    q_only = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"q": "安全"},
        headers=auth_headers(token),
    )
    format_only = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"source_format": "TXT"},
        headers=auth_headers(token),
    )
    mismatch = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"q": "安全", "source_format": "MARKDOWN", "status": "ACTIVE"},
        headers=auth_headers(token),
    )
    assert active.status_code == mismatch.status_code == 200
    assert q_only.json()["total"] == 1, q_only.text
    assert format_only.json()["total"] == 1, format_only.text
    assert [item["id"] for item in active.json()["items"]] == [
        text["document"]["id"]
    ]
    assert mismatch.json()["total"] == 0

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{markdown['document']['id']}",
        params={"version": markdown["document"]["version"]},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "knowledge-filter-delete",
        },
    )
    deleted_filter = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"q": "运维", "source_format": "MARKDOWN", "status": "DELETED"},
        headers=auth_headers(token),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted_filter.status_code == 200
    assert [item["id"] for item in deleted_filter.json()["items"]] == [
        markdown["document"]["id"]
    ]


async def test_source_promote_replays_from_verified_target_without_staging(
    client: AsyncClient,
    session_factory,
):
    settings = Settings().model_copy(
        update={"rag_sync_ingestion_enabled": False}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    token, _ = await register(client, "knowledge-replay-owner@example.com")
    workspace_id = (await _workspace(client, token))["id"]
    uploaded = await _upload(
        client,
        token,
        workspace_id,
        content="# 崩溃重放\n\n目标对象已写入但事件尚未完成。".encode(),
        key="knowledge-promote-replay",
    )
    assert uploaded["job"]["status"] == "QUEUED"

    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == UUID(uploaded["version"]["id"])
            )
        )
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == UUID(uploaded["version"]["id"])
            )
        )
        assert event is not None and version is not None
        payload = event.payload_json
        store = _object_store(settings)
        content = await store.get(
            str(payload["staging_bucket"]), str(payload["staging_key"])
        )
        await store.put(
            str(payload["target_bucket"]),
            event.object_key or version.object_key,
            content,
            version.mime_type,
        )
        await store.delete(
            str(payload["staging_bucket"]), str(payload["staging_key"])
        )
        completed = await KnowledgeService(
            session,
            version.created_by_user_id,
            version.workspace_id,
            settings,
        ).process_outbox_event(event.id)
        assert completed is True

    job = await client.get(
        f"/api/v1/workspaces/{workspace_id}/ingestion-jobs/{uploaded['job']['id']}",
        headers=auth_headers(token),
    )
    assert job.status_code == 200
    assert job.json()["status"] == "SUCCEEDED"
