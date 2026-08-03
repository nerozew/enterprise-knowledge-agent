from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.knowledge as knowledge_service_module
from app.core.config import Settings
from app.core.errors import AppError
from app.knowledge.evidence import EvidenceGate, EvidenceGateConfig
from app.knowledge.providers import StructuredAnswer
from app.knowledge.types import (
    ChunkKind,
    KnowledgeChunk,
    ScoreAvailability,
    ScoreDetails,
    ScoredHit,
    ScoreStage,
)
from app.models.base import utc_now
from app.models.knowledge import Document, DocumentChunk, DocumentVersion
from app.schemas.knowledge import SearchRequest
from app.services.knowledge import KnowledgeService
from tests.conftest import auth_headers, register
from tests.test_p2_p4_api import _workspace


def _scored_hit(
    *,
    raw_score: float,
    query_fusion_score: float,
    rerank_score: float | None = None,
) -> ScoredHit:
    chunk = KnowledgeChunk(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        document_version_id=str(uuid4()),
        kind=ChunkKind.CHILD,
        ordinal=0,
        text="approved evidence",
        page=1,
        heading_path=("Policy",),
        char_start=0,
        char_end=17,
        source_span_id="traceable-span",
    )
    unavailable = ScoreStage(availability=ScoreAvailability.NOT_RUN)
    rerank = (
        ScoreStage(
            availability=ScoreAvailability.AVAILABLE,
            value=rerank_score,
            rank=1,
            method="RERANKER",
        )
        if rerank_score is not None
        else unavailable
    )
    details = ScoreDetails(
        dense_similarity=unavailable,
        bm25=unavailable,
        channel_fusion=unavailable,
        query_fusion=ScoreStage(
            availability=ScoreAvailability.AVAILABLE,
            value=query_fusion_score,
            rank=1,
            method="RRF",
            rrf_k=60,
        ),
        rerank=rerank,
        final=ScoreStage(
            availability=ScoreAvailability.AVAILABLE,
            value=raw_score,
            rank=1,
            method="WEIGHTED_SUM" if rerank_score is not None else "IDENTITY",
        ),
    )
    return ScoredHit(
        chunk=chunk,
        score=raw_score,
        scores={"total": raw_score},
        score_details=details,
    )


def test_evidence_gate_normalizes_multi_query_without_changing_raw_score() -> None:
    hit = _scored_hit(raw_score=0.048395, query_fusion_score=0.048395)
    decision = EvidenceGate(
        EvidenceGateConfig(min_top_score=0.015),
    ).evaluate(
        "semantic paraphrase",
        [hit],
        query_count=3,
        enforce_lexical_coverage=False,
    )

    assert hit.score == 0.048395
    assert decision.top_score == 0.048395
    assert decision.gate_score == pytest.approx(0.016131666666666667)
    assert decision.accepted is True
    assert decision.query_count == 3

    single_query = EvidenceGate().evaluate(
        "semantic paraphrase",
        [hit],
        enforce_lexical_coverage=False,
    )
    assert single_query.gate_score == 0.048395


def test_evidence_gate_normalizes_only_query_fusion_before_rerank_mix() -> None:
    hit = _scored_hit(
        raw_score=0.499358,
        query_fusion_score=0.048395,
        rerank_score=0.8,
    )
    decision = EvidenceGate().evaluate(
        "semantic paraphrase",
        [hit],
        query_count=3,
        enforce_lexical_coverage=False,
    )

    assert decision.gate_score == pytest.approx(
        0.4 * (0.048395 / 3) + 0.6 * 0.8
    )
    assert hit.score == 0.499358


def _knowledge_row(
    *,
    actor_id: UUID,
    workspace_id: UUID,
    content: str,
) -> tuple[DocumentChunk, Document, DocumentVersion]:
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
        heading_path=["Policy"],
        page=1,
        char_start=0,
        char_end=len(content),
        source_span_id="a" * 64,
        content_sha256="b" * 64,
        content=content,
        indexed=True,
    )
    document = Document(
        id=document_id,
        workspace_id=workspace_id,
        created_by_user_id=actor_id,
        title="Policy",
        published_version_id=version_id,
    )
    version = DocumentVersion(
        id=version_id,
        workspace_id=workspace_id,
        document_id=document_id,
        created_by_user_id=actor_id,
        format="TXT",
        mime_type="text/plain",
        file_name="policy.txt",
        file_size=len(content.encode()),
        file_sha256="c" * 64,
        object_key="knowledge/policy.txt",
        status="PUBLISHED",
        published_at=utc_now(),
    )
    return chunk, document, version


async def _search_with_milvus_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: SearchRequest,
    content: str,
    answer_status: str = "answered",
    min_score: float = 0.015,
) -> tuple[Any, Any, Any]:
    actor_id = uuid4()
    workspace_id = uuid4()
    row = _knowledge_row(
        actor_id=actor_id,
        workspace_id=workspace_id,
        content=content,
    )

    class Access:
        async def require(self, requested_workspace_id: UUID) -> None:
            assert requested_workspace_id == workspace_id

    class Repository:
        async def published_chunks_by_ids(
            self,
            requested_workspace_id: UUID,
            chunk_ids: list[UUID],
        ) -> list[tuple[DocumentChunk, Document, DocumentVersion]]:
            assert requested_workspace_id == workspace_id
            assert chunk_ids == [row[0].id]
            return [row]

        async def context_chunks(self, *args: Any, **kwargs: Any) -> list[DocumentChunk]:
            return []

    class VectorStore:
        calls = 0

        async def search(self, *args: Any, **kwargs: Any) -> list[tuple[str, float]]:
            self.calls += 1
            return [(str(row[0].id), 0.9)]

    class Embedding:
        profile = "test-embedding"

        async def embed_query(self, query: str) -> tuple[float, float]:
            return (1.0, 0.0)

    class LLM:
        profile = "test-grounded-answer"
        answer_calls = 0

        async def rewrite(self, query: str, *, max_queries: int) -> list[str]:
            return [query, "approved policy rewrite", "approved policy rewrite"]

        async def answer(self, query: str, evidence: list[Any]) -> StructuredAnswer:
            self.answer_calls += 1
            if answer_status == "refused":
                return StructuredAnswer("refused", "insufficient support", ())
            if answer_status != "answered":
                return StructuredAnswer(answer_status, "invalid status", ())
            return StructuredAnswer(
                "answered",
                "grounded answer",
                (evidence[0].hit_id,),
            )

    vector_store = VectorStore()
    llm = LLM()
    monkeypatch.setattr(
        knowledge_service_module,
        "_vector_store",
        lambda settings: vector_store,
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_embedding",
        lambda settings: Embedding(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_reranker",
        lambda settings: None,
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_llm",
        lambda settings: llm,
    )
    service = KnowledgeService(
        cast(AsyncSession, object()),
        actor_id,
        workspace_id,
        Settings(
            app_env="test",
            jwt_secret="test-secret-that-is-longer-than-32-characters",
            milvus_uri="http://milvus.test:19530",
            rag_provider_mode="REMOTE",
            rag_evidence_min_score=min_score,
        ),
    )
    service.access = cast(Any, Access())
    service.repo = cast(Any, Repository())
    response = await service.search(payload)
    return response, llm, vector_store


@pytest.mark.asyncio
async def test_hits_trace_opt_in_uses_deduplicated_queries_and_never_calls_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, vector_store = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="approved policy",
            retrieval_mode="DENSE",
            response_mode="HITS",
            rewrite="ON",
            multi_query=True,
            rerank="OFF",
            include_query_trace=True,
        ),
        content="approved policy evidence",
    )

    assert llm.answer_calls == 0
    assert vector_store.calls == 2
    assert response.scoring.query_count == 2
    assert response.query_trace.exposed is True
    assert [(item.index, item.kind, item.text) for item in response.query_trace.items] == [
        (0, "ORIGINAL", "approved policy"),
        (1, "REWRITE", "approved policy rewrite"),
    ]
    assert {
        item.query_index
        for item in response.hits[0].score_details.query_fusion.contributions
    } == {item.index for item in response.query_trace.items}
    assert response.answerability.status == "NOT_EVALUATED"
    assert response.answerability.reason_code == "HITS_MODE"
    assert response.answerability.provider_called is False


@pytest.mark.asyncio
async def test_query_trace_opt_out_never_exposes_query_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, _ = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="private original query",
            retrieval_mode="DENSE",
            response_mode="HITS",
            rewrite="OFF",
            rerank="OFF",
        ),
        content="private original query evidence",
    )

    assert llm.answer_calls == 0
    assert response.query_trace.exposed is False
    assert response.query_trace.items == []
    assert response.query_trace.redaction_reason == "NOT_REQUESTED"
    assert "private original query" not in response.query_trace.model_dump_json()


@pytest.mark.asyncio
async def test_hits_low_score_is_not_answerability_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, _ = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="approved policy",
            retrieval_mode="DENSE",
            response_mode="HITS",
            rewrite="OFF",
            rerank="OFF",
        ),
        content="approved policy evidence",
        min_score=0.02,
    )

    assert llm.answer_calls == 0
    assert response.answerability.status == "NOT_EVALUATED"
    assert response.answerability.method == "NOT_RUN"
    assert response.answerability.reason_code == "HITS_MODE"
    assert response.answerability.precheck_code == "score_below_threshold"
    assert response.answerability.gate_score < response.answerability.score_threshold


@pytest.mark.asyncio
async def test_hits_low_lexical_coverage_is_not_answerability_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, _ = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="computer missing",
            retrieval_mode="DENSE",
            response_mode="HITS",
            rewrite="OFF",
            rerank="OFF",
        ),
        content="device loss reporting procedure",
    )

    assert llm.answer_calls == 0
    assert response.answerability.status == "NOT_EVALUATED"
    assert response.answerability.method == "NOT_RUN"
    assert response.answerability.reason_code == "HITS_MODE"
    assert response.answerability.precheck_code == "lexical_coverage_below_threshold"
    assert response.answerability.lexical_passed is False
    assert (
        response.answerability.lexical_coverage
        < response.answerability.lexical_threshold
    )


@pytest.mark.asyncio
async def test_hits_empty_result_is_not_answerability_rejection(
    client: AsyncClient,
) -> None:
    token, _ = await register(client, "empty-hits-answerability@example.com")
    workspace_id = (await _workspace(client, token))["id"]
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=auth_headers(token),
        json={
            "query": "nothing indexed",
            "retrieval_mode": "SPARSE",
            "response_mode": "HITS",
        },
    )

    assert response.status_code == 200
    answerability = response.json()["answerability"]
    assert answerability["status"] == "NOT_EVALUATED"
    assert answerability["method"] == "NOT_RUN"
    assert answerability["reason_code"] == "HITS_MODE"
    assert answerability["precheck_code"] == "no_traceable_evidence"
    assert answerability["raw_top_score"] == 0
    assert answerability["gate_score"] == 0
    assert answerability["checked_hit_ids"] == []


@pytest.mark.asyncio
async def test_answerability_records_model_refusal_after_heuristic_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, _ = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="请假怎么办",
            retrieval_mode="DENSE",
            response_mode="ANSWER",
            rewrite="OFF",
            rerank="OFF",
        ),
        content="员工休假审批流程",
        answer_status="refused",
    )

    assert llm.answer_calls == 1
    assert response.answer is None
    assert response.refusal is not None
    assert response.refusal.code == "model_refused"
    assert response.answerability.status == "REJECTED"
    assert response.answerability.method == "GROUNDED_ANSWER_LLM"
    assert response.answerability.precheck_code == "accepted"
    assert response.answerability.provider_called is True


@pytest.mark.asyncio
async def test_answer_hard_precheck_rejection_does_not_call_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, _ = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="approved policy",
            retrieval_mode="DENSE",
            response_mode="ANSWER",
            rewrite="OFF",
            rerank="OFF",
        ),
        content="approved policy evidence",
        min_score=0.02,
    )

    assert llm.answer_calls == 0
    assert response.answer is None
    assert response.answerability.status == "REJECTED"
    assert response.answerability.method == "HEURISTIC_GATE"
    assert response.answerability.reason_code == "score_below_threshold"
    assert response.answerability.provider_called is False


@pytest.mark.asyncio
async def test_milvus_unknown_answer_status_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AppError) as error:
        await _search_with_milvus_fakes(
            monkeypatch,
            payload=SearchRequest(
                query="approved policy",
                retrieval_mode="DENSE",
                response_mode="ANSWER",
                rewrite="OFF",
                rerank="OFF",
            ),
            content="approved policy evidence",
            answer_status="unexpected",
        )

    assert error.value.code == "llm_dependency_unavailable"
    assert error.value.status_code == 503
    assert error.value.message == (
        "The answer model provider is temporarily unavailable"
    )


@pytest.mark.asyncio
async def test_answer_mode_treats_low_lexical_coverage_as_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, llm, _ = await _search_with_milvus_fakes(
        monkeypatch,
        payload=SearchRequest(
            query="电脑找不到了怎么办",
            retrieval_mode="DENSE",
            response_mode="ANSWER",
            rewrite="OFF",
            rerank="OFF",
        ),
        content="信息设备遗失事件处置和报告流程",
        answer_status="answered",
    )

    assert llm.answer_calls == 1
    assert response.answer == "grounded answer"
    assert response.refusal is None
    assert response.answerability.status == "SUPPORTED"
    assert response.answerability.method == "GROUNDED_ANSWER_LLM"
    assert response.answerability.supporting_hit_ids == [response.hits[0].chunk_id]
    assert response.answerability.lexical_passed is False
