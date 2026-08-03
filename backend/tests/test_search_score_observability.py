from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.knowledge.providers import FixtureEmbeddingProvider, ProviderMode
from app.knowledge.retrieval import FusionMode, HybridRetriever, RetrievalConfig, RetrievalMode
from app.knowledge.types import (
    ChunkKind,
    IndexedChunk,
    KnowledgeChunk,
    ScoreAvailability,
    ScoreContribution,
)
from app.schemas.knowledge import SearchRequest
from app.services.knowledge import KnowledgeService
from tests.conftest import auth_headers, register
from tests.test_p2_p4_api import _upload, _workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(RetrievalMode))
@pytest.mark.parametrize("fusion", list(FusionMode))
@pytest.mark.parametrize("multi_query", [False, True])
async def test_fallback_fusion_observability_matches_single_pass_algorithm(
    mode: RetrievalMode,
    fusion: FusionMode,
    multi_query: bool,
) -> None:
    chunks = [
        KnowledgeChunk(
            chunk_id=name,
            document_id="document",
            document_version_id="version",
            kind=ChunkKind.CHILD,
            ordinal=index,
            text=f"{name} policy",
            page=1,
            heading_path=("Policy",),
            char_start=index * 20,
            char_end=index * 20 + len(name) + 7,
            source_span_id=f"span-{name}",
        )
        for index, name in enumerate(("alpha", "beta"))
    ]

    class Embedding:
        mode = ProviderMode.FIXTURE
        profile = "matrix-embedding"

        async def embed_query(self, query: str) -> tuple[float, float]:
            return (0.0, 1.0) if query == "beta" else (1.0, 0.0)

    class RewriteLLM:
        async def rewrite(self, query: str, *, max_queries: int) -> list[str]:
            return ["beta"]

    retriever = HybridRetriever(
        chunks=[
            IndexedChunk(chunks[0], "workspace", (1.0, 0.0)),
            IndexedChunk(chunks[1], "workspace", (0.0, 1.0)),
        ],
        embedding=Embedding(),
        llm=RewriteLLM(),
    )
    result = await retriever.search(
        "alpha",
        RetrievalConfig(
            mode=mode,
            fusion=fusion,
            top_k=2,
            candidate_k=2,
            rerank=False,
            rewrite=multi_query,
            multi_query=multi_query,
            expand_context=False,
        ),
    )

    query_count = 2 if multi_query else 1
    assert result.scoring is not None
    assert result.scoring.query_count == query_count
    if mode is RetrievalMode.HYBRID and multi_query:
        expected_channel = ("COMBINED_FUSION", True)
        expected_query = ("COMBINED_FUSION", True)
    elif mode is RetrievalMode.HYBRID:
        expected_channel = (fusion.value, True)
        expected_query = ("IDENTITY", False)
    else:
        expected_channel = (None, False)
        expected_query = (fusion.value, True)
    assert (
        result.scoring.channel_fusion.method,
        result.scoring.channel_fusion.applied,
    ) == expected_channel
    assert (
        result.scoring.query_fusion.method,
        result.scoring.query_fusion.applied,
    ) == expected_query
    expected_rrf_k = 60 if fusion is FusionMode.RRF else None
    if result.scoring.channel_fusion.applied:
        assert result.scoring.channel_fusion.rrf_k == expected_rrf_k
    else:
        assert result.scoring.channel_fusion.rrf_k is None
    assert result.scoring.query_fusion.rrf_k == (
        expected_rrf_k if result.scoring.query_fusion.applied else None
    )

    first = result.hits[0]
    assert first.score_details is not None
    if fusion is FusionMode.RRF:
        expected = {
            RetrievalMode.DENSE: 1 / 61 + (1 / 62 if multi_query else 0),
            RetrievalMode.SPARSE: 1 / 61,
            RetrievalMode.HYBRID: 2 / 61 + (1 / 62 if multi_query else 0),
        }[mode]
    else:
        expected = {
            RetrievalMode.DENSE: 0.65,
            RetrievalMode.SPARSE: 0.35,
            RetrievalMode.HYBRID: 1.0,
        }[mode]
    assert first.chunk.chunk_id == "alpha"
    assert first.score == pytest.approx(expected)
    assert first.score_details.query_fusion.value == pytest.approx(expected)
    assert first.score_details.query_fusion.method == expected_query[0]
    if expected_query[1]:
        assert first.score_details.query_fusion.contributions
    else:
        assert first.score_details.query_fusion.contributions == ()
    if mode is RetrievalMode.HYBRID:
        assert first.score_details.channel_fusion.value == pytest.approx(expected)
        assert first.score_details.channel_fusion.method == expected_channel[0]
    else:
        assert (
            first.score_details.channel_fusion.availability
            is ScoreAvailability.NOT_APPLICABLE
        )
        assert first.score_details.channel_fusion.value is None


@pytest.mark.parametrize(
    ("mode", "available", "unavailable", "channel_availability"),
    [
        ("DENSE", "dense_similarity", "bm25", ScoreAvailability.NOT_APPLICABLE),
        ("SPARSE", "bm25", "dense_similarity", ScoreAvailability.NOT_APPLICABLE),
        ("HYBRID", "channel_fusion", "dense_similarity", ScoreAvailability.AVAILABLE),
    ],
)
def test_milvus_score_details_identify_exposed_stage_without_fabricating_channels(
    mode: str,
    available: str,
    unavailable: str,
    channel_availability: ScoreAvailability,
) -> None:
    payload = SearchRequest(
        query="production access",
        retrieval_mode=mode,
        rerank="OFF",
        rewrite="OFF",
    )
    details = KnowledgeService._milvus_score_details(
        payload=payload,
        source_observations=(
            ScoreContribution(
                query_index=0,
                value=0.75,
                rank=2,
                source=available,
            ),
        ),
        query_contributions=(
            ScoreContribution(
                query_index=0,
                value=1 / 62,
                rank=2,
                source="query_fusion",
            ),
        ),
        query_fusion_score=1 / 62,
        query_fusion_rank=1,
        rerank_score=None,
        rerank_rank=None,
        final_score=1 / 62,
        final_rank=1,
    )

    assert getattr(details, available).availability is ScoreAvailability.AVAILABLE
    assert details.channel_fusion.availability is channel_availability
    assert details.query_fusion.value == pytest.approx(1 / 62)
    assert details.query_fusion.contributions[0].query_index == 0
    assert details.final.value == pytest.approx(1 / 62)
    assert details.final.rank == 1
    if mode == "HYBRID":
        assert details.dense_similarity.availability is ScoreAvailability.NOT_EXPOSED
        assert details.bm25.availability is ScoreAvailability.NOT_EXPOSED
        assert details.dense_similarity.value is None
        assert details.bm25.value is None
    else:
        assert getattr(details, unavailable).availability is ScoreAvailability.NOT_RUN
        assert getattr(details, unavailable).value is None


@pytest.mark.asyncio
async def test_multi_query_and_rerank_trace_preserves_final_scores_and_ranks() -> None:
    chunks = [
        KnowledgeChunk(
            chunk_id=f"chunk-{index}",
            document_id="document",
            document_version_id="version",
            kind=ChunkKind.CHILD,
            ordinal=index,
            text=text,
            page=1,
            heading_path=("Access",),
            char_start=index * 100,
            char_end=index * 100 + len(text),
            source_span_id=f"span-{index}",
        )
        for index, text in enumerate(
            (
                "production access expires after thirty days",
                "owner approval is required for production access",
            )
        )
    ]
    embedding = FixtureEmbeddingProvider(dimensions=32)
    vectors = await embedding.embed_documents([chunk.text for chunk in chunks])

    class RewriteLLM:
        async def rewrite(self, query: str, *, max_queries: int) -> list[str]:
            assert max_queries == 2
            return ["production access duration"]

    class Reranker:
        profile = "trace-reranker"

        async def rerank(self, query: str, texts: list[str]) -> list[float]:
            return [0.2, 0.9]

    retriever = HybridRetriever(
        chunks=[
            IndexedChunk(chunk, "workspace", tuple(vector))
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
        embedding=embedding,
        reranker=Reranker(),
        llm=RewriteLLM(),
    )
    result = await retriever.search(
        "production access",
        RetrievalConfig(
            mode=RetrievalMode.HYBRID,
            fusion=FusionMode.RRF,
            top_k=2,
            candidate_k=2,
            rerank=True,
            rewrite=True,
            multi_query=True,
            expand_context=False,
        ),
    )

    assert result.scoring is not None
    assert result.scoring.query_count == 2
    assert result.scoring.channel_fusion.applied is True
    assert result.scoring.query_fusion.applied is True
    assert result.scoring.query_fusion.rrf_k == 60
    assert result.scoring.rerank_applied is True
    assert len(result.hits) == 2
    for rank, hit in enumerate(result.hits, start=1):
        assert hit.score_details is not None
        assert hit.score_details.final.value == pytest.approx(hit.score)
        assert hit.score_details.final.rank == rank
        assert hit.score_details.rerank.availability is ScoreAvailability.AVAILABLE
        assert {
            contribution.query_index
            for contribution in hit.score_details.query_fusion.contributions
        } == {0, 1}


@pytest.mark.asyncio
async def test_search_api_keeps_legacy_scores_and_scores_refusal_and_tenant_isolation(
    client: AsyncClient,
) -> None:
    owner_token, _ = await register(client, "score-owner@example.com")
    other_token, _ = await register(client, "score-other@example.com")
    owner_workspace = (await _workspace(client, owner_token))["id"]
    other_workspace = (await _workspace(client, other_token))["id"]
    await _upload(
        client,
        owner_token,
        owner_workspace,
        content=b"# Access\n\nProduction access requires approval.",
        key="score-observability-upload",
    )

    response = await client.post(
        f"/api/v1/workspaces/{owner_workspace}/search",
        headers=auth_headers(owner_token),
        json={
            "query": "production access approval",
            "retrieval_mode": "SPARSE",
            "rerank": "OFF",
            "rewrite": "OFF",
            "expand_context": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scoring"] == {
        "version": 1,
        "requested_retrieval_mode": "SPARSE",
        "effective_retrieval_mode": "SPARSE",
        "query_count": 1,
        "channel_fusion": {"method": None, "applied": False, "rrf_k": None},
        "query_fusion": {"method": "RRF", "applied": True, "rrf_k": 60},
        "rerank_applied": False,
    }
    hit = body["hits"][0]
    assert isinstance(hit["score"], float)
    assert set(hit["scores"]) == {"dense", "sparse", "fusion", "rerank"}
    assert hit["score_details"]["bm25"]["availability"] == "AVAILABLE"
    assert hit["score_details"]["dense_similarity"]["availability"] == "NOT_RUN"
    assert hit["score_details"]["final"]["value"] == pytest.approx(hit["score"])
    assert hit["score_details"]["final"]["rank"] == 1

    refused = await client.post(
        f"/api/v1/workspaces/{other_workspace}/search",
        headers=auth_headers(other_token),
        json={"query": "production access", "retrieval_mode": "SPARSE"},
    )
    assert refused.status_code == 200
    assert refused.json()["hits"] == []
    assert refused.json()["refusal"]["code"] == "insufficient_evidence"
    assert refused.json()["scoring"]["query_fusion"]["applied"] is False

    forbidden = await client.post(
        f"/api/v1/workspaces/{owner_workspace}/search",
        headers=auth_headers(other_token),
        json={
            "query": "production access",
            "retrieval_mode": "SPARSE",
            "include_query_trace": True,
        },
    )
    assert forbidden.status_code == 404
    assert "query_trace" not in forbidden.json()
