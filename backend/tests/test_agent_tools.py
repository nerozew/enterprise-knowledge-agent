from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.models import (
    InspirationSearchInput,
    KnowledgeFilters,
    KnowledgeSearchInput,
    RuntimeContext,
)
from app.agent.tools import InspirationServiceToolAdapter, KnowledgeServiceToolAdapter
from app.schemas.inspirations import InspirationResponse
from app.schemas.knowledge import SearchRequest, SearchResponse


def runtime_context() -> RuntimeContext:
    return RuntimeContext(
        workspace_id=uuid4(),
        actor_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
    )


def search_response(query: str) -> SearchResponse:
    return SearchResponse.model_validate(
        {
            "query": query,
            "response_mode": "ANSWER",
            "answer": "有依据的回答",
            "refusal": None,
            "hits": [],
            "provider": {
                "mode": "FIXTURE",
                "embedding": "fixture",
                "reranker": "disabled",
                "llm": "fixture",
                "profile": "fixture",
            },
            "scoring": {
                "version": 1,
                "requested_retrieval_mode": "HYBRID",
                "effective_retrieval_mode": "HYBRID",
                "query_count": 1,
                "channel_fusion": {"method": "RRF", "applied": True, "rrf_k": 60},
                "query_fusion": {"method": None, "applied": False, "rrf_k": None},
                "rerank_applied": False,
            },
            "query_trace": {"exposed": False, "items": [], "redaction_reason": "disabled"},
            "answerability": {
                "status": "SUPPORTED",
                "method": "HEURISTIC_GATE",
                "reason_code": "accepted",
                "precheck_code": "accepted",
                "provider_called": False,
                "raw_top_score": 1.0,
                "gate_score": 1.0,
                "score_threshold": 0.5,
                "lexical_coverage": 1.0,
                "lexical_threshold": 0.1,
                "lexical_passed": True,
                "checked_hit_ids": [],
                "supporting_hit_ids": [],
            },
            "components": ["dense", "bm25"],
            "timings_ms": {"total": 1.0},
            "warnings": [],
            "degraded": False,
        }
    )


class RecordingKnowledgeService:
    def __init__(self) -> None:
        self.payloads: list[SearchRequest] = []

    async def search(self, payload: SearchRequest) -> SearchResponse:
        self.payloads.append(payload)
        return search_response(payload.query)


class RecordingInspirationService:
    def __init__(self, item: InspirationResponse) -> None:
        self.item = item
        self.calls: list[dict[str, Any]] = []

    async def list(self, **kwargs: Any) -> tuple[list[Any], int]:
        self.calls.append(kwargs)
        return [self.item], 1


@pytest.mark.asyncio
async def test_knowledge_adapter_maps_context_query_limit_and_allowlisted_filters() -> None:
    context = runtime_context()
    recorded_contexts: list[RuntimeContext] = []
    service = RecordingKnowledgeService()

    def factory(received: RuntimeContext) -> RecordingKnowledgeService:
        recorded_contexts.append(received)
        return service

    adapter = KnowledgeServiceToolAdapter(factory)
    document_id = uuid4()
    published_after = datetime(2026, 7, 1, tzinfo=UTC)
    published_before = datetime(2026, 8, 1, tzinfo=UTC)

    result = await adapter.invoke(
        KnowledgeSearchInput(
            query="员工报销规则",
            top_k=7,
            filters=KnowledgeFilters(
                document_ids=[document_id],
                source_formats=["PDF"],
                published_after=published_after,
                published_before=published_before,
            ),
        ),
        context=context,
    )

    assert recorded_contexts == [context]
    payload = service.payloads[0]
    assert payload.query == "员工报销规则"
    assert payload.top_k == 7
    assert payload.candidate_k == 30
    assert payload.response_mode == "ANSWER"
    assert payload.filters.document_ids == [document_id]
    assert payload.filters.source_formats == ["PDF"]
    assert payload.filters.published_after == published_after
    assert payload.filters.published_before == published_before
    assert result.tool_name == "knowledge_search"
    assert result.data["answer"] == "有依据的回答"
    assert "workspace_id" not in payload.model_dump()
    assert "actor_id" not in payload.model_dump()


@pytest.mark.asyncio
async def test_inspiration_adapter_maps_context_pagination_and_read_only_flag() -> None:
    context = runtime_context()
    recorded_contexts: list[RuntimeContext] = []
    author_id = uuid4()
    item = InspirationResponse(
        id=uuid4(),
        workspace_id=context.workspace_id,
        user_id=author_id,
        title="灵感",
        content="内容",
        summary=None,
        expanded_content=None,
        source_url=None,
        version=1,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        deleted_at=None,
        tags=[],
    )
    service = RecordingInspirationService(item)

    def factory(received: RuntimeContext) -> RecordingInspirationService:
        recorded_contexts.append(received)
        return service

    adapter = InspirationServiceToolAdapter(factory)
    tag_id = uuid4()

    result = await adapter.invoke(
        InspirationSearchInput(query="产品创意", tag_ids=[tag_id], limit=17),
        context=context,
    )

    assert recorded_contexts == [context]
    assert service.calls == [
        {
            "q": "产品创意",
            "tag_ids": [tag_id],
            "tag_match": "any",
            "include_deleted": False,
            "page": 1,
            "page_size": 17,
        }
    ]
    assert result.tool_name == "inspiration_search"
    assert result.data["total"] == 1
    assert result.data["items"][0]["workspace_id"] == str(context.workspace_id)
    assert result.data["items"][0]["author_id"] == str(author_id)


@pytest.mark.parametrize("identity_field", ["workspace_id", "actor_id"])
def test_tool_argument_schemas_reject_runtime_identity_override(identity_field: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchInput.model_validate({"query": "恶意覆盖身份", identity_field: str(uuid4())})
    with pytest.raises(ValidationError):
        InspirationSearchInput.model_validate(
            {"query": "恶意覆盖身份", identity_field: str(uuid4())}
        )
