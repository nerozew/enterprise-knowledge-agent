from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.knowledge as knowledge_service_module
from app.core.config import Settings
from app.knowledge.evidence import EvidenceGate
from app.models.base import utc_now
from app.models.knowledge import Document, DocumentChunk, DocumentVersion
from app.schemas.knowledge import SearchRequest
from app.services.knowledge import KnowledgeService


@pytest.mark.asyncio
async def test_compact_milvus_uuid_maps_to_mysql_row_and_traceable_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = uuid4()
    workspace_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    chunk_id = uuid4()

    chunk = DocumentChunk(
        id=chunk_id,
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        parent_chunk_id=None,
        kind="CHILD",
        ordinal=0,
        heading_path=["生产权限"],
        page=1,
        char_start=0,
        char_end=18,
        source_span_id="a" * 64,
        content_sha256="b" * 64,
        content="普通生产权限最长有效三十天",
        indexed=True,
    )
    document = Document(
        id=document_id,
        workspace_id=workspace_id,
        created_by_user_id=actor_id,
        title="生产系统访问规范",
        published_version_id=version_id,
    )
    version = DocumentVersion(
        id=version_id,
        workspace_id=workspace_id,
        document_id=document_id,
        created_by_user_id=actor_id,
        format="TXT",
        mime_type="text/plain",
        file_name="access.txt",
        file_size=128,
        file_sha256="c" * 64,
        object_key="knowledge/access.txt",
        status="PUBLISHED",
        published_at=utc_now(),
    )
    row = (chunk, document, version)

    class VectorStore:
        async def search(self, *args: Any, **kwargs: Any) -> list[tuple[str, float]]:
            return [
                ("not-a-uuid", 1.0),
                (chunk_id.hex, 0.9),
            ]

    class Embedding:
        profile = "fixture-embedding-v1"

        async def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    class Repository:
        requested_ids: list[UUID] = []

        async def published_chunks_by_ids(
            self, requested_workspace_id: UUID, chunk_ids: list[UUID]
        ) -> list[tuple[DocumentChunk, Document, DocumentVersion]]:
            assert requested_workspace_id == workspace_id
            self.requested_ids = chunk_ids
            return [row]

        async def context_chunks(self, *args: Any, **kwargs: Any) -> list[DocumentChunk]:
            return []

    vector_store = VectorStore()
    monkeypatch.setattr(
        knowledge_service_module,
        "_vector_store",
        lambda settings: vector_store,
    )
    settings = Settings(
        app_env="test",
        jwt_secret="test-secret-that-is-longer-than-32-characters",
        rag_provider_mode="FIXTURE",
    )
    service = KnowledgeService(
        cast(AsyncSession, object()),
        actor_id,
        workspace_id,
        settings,
    )
    repository = Repository()
    service.repo = cast(Any, repository)
    payload = SearchRequest(
        query="普通生产权限",
        retrieval_mode="DENSE",
        top_k=1,
        candidate_k=5,
        rerank="OFF",
        rewrite="OFF",
        expand_context=False,
    )

    result, selected_rows = await service._milvus_retrieve(
        payload,
        Embedding(),
        None,
        None,
        use_rerank=False,
        use_rewrite=False,
    )

    canonical_chunk_id = str(chunk_id)
    assert repository.requested_ids == [chunk_id]
    assert [hit.chunk.chunk_id for hit in result.hits] == [canonical_chunk_id]
    assert result.hits[0].scores["dense"] == 0.9
    assert selected_rows == [row]
    evidence = EvidenceGate().evaluate(payload.query, result.hits)
    assert evidence.accepted is True
    assert evidence.code == "accepted"
    assert evidence.usable_hit_ids == (canonical_chunk_id,)


@pytest.mark.asyncio
async def test_milvus_candidates_deduplicate_per_query_and_keep_rerank_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = uuid4()
    workspace_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()

    def chunk(chunk_id: UUID, ordinal: int, content: str) -> DocumentChunk:
        return DocumentChunk(
            id=chunk_id,
            workspace_id=workspace_id,
            document_id=document_id,
            document_version_id=version_id,
            parent_chunk_id=None,
            kind="CHILD",
            ordinal=ordinal,
            heading_path=["Production access"],
            page=1,
            char_start=ordinal * 100,
            char_end=ordinal * 100 + len(content),
            source_span_id=f"{ordinal + 1:064x}",
            content_sha256=f"{ordinal + 11:064x}",
            content=content,
            indexed=True,
        )

    first_chunk = chunk(first_id, 0, "production access lasts thirty days")
    second_chunk = chunk(second_id, 1, "production access requires owner approval")
    document = Document(
        id=document_id,
        workspace_id=workspace_id,
        created_by_user_id=actor_id,
        title="Production access",
        published_version_id=version_id,
    )
    version = DocumentVersion(
        id=version_id,
        workspace_id=workspace_id,
        document_id=document_id,
        created_by_user_id=actor_id,
        format="TXT",
        mime_type="text/plain",
        file_name="access.txt",
        file_size=256,
        file_sha256="d" * 64,
        object_key="knowledge/access.txt",
        status="PUBLISHED",
        published_at=utc_now(),
    )
    first_row = (first_chunk, document, version)
    second_row = (second_chunk, document, version)

    class VectorStore:
        calls = 0

        async def search(self, *args: Any, **kwargs: Any) -> list[tuple[Any, float]]:
            self.calls += 1
            return [
                (123, 1.0),
                ("not-a-uuid", 0.99),
                (first_id.hex, 0.5),
                (str(first_id), 0.9),
                (second_id.hex, 0.8),
            ]

    class Embedding:
        profile = "fixture-embedding-v1"

        async def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    class RewriteLLM:
        async def rewrite(self, query: str, *, max_queries: int) -> list[str]:
            assert max_queries == 1
            return ["alternate production access"]

    class Reranker:
        inputs: list[str] = []

        async def rerank(self, query: str, texts: list[str]) -> list[float]:
            self.inputs = texts
            return [0.1, 0.9]

    class Repository:
        async def published_chunks_by_ids(
            self, requested_workspace_id: UUID, chunk_ids: list[UUID]
        ) -> list[tuple[DocumentChunk, Document, DocumentVersion]]:
            assert requested_workspace_id == workspace_id
            assert chunk_ids == [first_id, second_id]
            return [second_row, first_row]

        async def context_chunks(self, *args: Any, **kwargs: Any) -> list[DocumentChunk]:
            return []

    vector_store = VectorStore()
    monkeypatch.setattr(
        knowledge_service_module,
        "_vector_store",
        lambda settings: vector_store,
    )
    service = KnowledgeService(
        cast(AsyncSession, object()),
        actor_id,
        workspace_id,
        Settings(
            app_env="test",
            jwt_secret="test-secret-that-is-longer-than-32-characters",
            rag_provider_mode="FIXTURE",
        ),
    )
    service.repo = cast(Any, Repository())
    reranker = Reranker()
    payload = SearchRequest(
        query="production access",
        retrieval_mode="DENSE",
        top_k=2,
        candidate_k=10,
        rerank="ON",
        rewrite="ON",
        expand_context=False,
    )

    result, selected_rows = await service._milvus_retrieve(
        payload,
        Embedding(),
        reranker,
        RewriteLLM(),
        use_rerank=True,
        use_rewrite=True,
    )

    assert vector_store.calls == 2
    assert reranker.inputs == [first_chunk.content, second_chunk.content]
    assert [hit.chunk.chunk_id for hit in result.hits] == [
        str(second_id),
        str(first_id),
    ]
    assert selected_rows == [second_row, first_row]
    scores = {hit.chunk.chunk_id: hit.scores for hit in result.hits}
    assert scores[str(first_id)]["dense"] == 0.9
    assert scores[str(second_id)]["dense"] == 0.8
    assert scores[str(first_id)]["rerank"] == 0.1
    assert scores[str(second_id)]["rerank"] == 0.9
    assert scores[str(first_id)]["total"] == pytest.approx(0.4 * (2 / 61) + 0.6 * 0.1)
    assert scores[str(second_id)]["total"] == pytest.approx(0.4 * (2 / 62) + 0.6 * 0.9)
