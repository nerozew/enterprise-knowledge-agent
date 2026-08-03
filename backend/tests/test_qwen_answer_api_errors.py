from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

import app.services.knowledge as knowledge_service_module
from app.core.config import Settings, get_settings
from app.knowledge.errors import ProviderUnavailableError
from app.knowledge.providers import ProviderMode, StructuredAnswer
from app.main import app
from tests.conftest import auth_headers, register


@pytest.mark.parametrize("failure_mode", ["provider_error", "unknown_status"])
async def test_search_maps_answer_provider_failure_to_sanitized_503_and_keeps_hits(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    token, _ = await register(client, "qwen-answer-error@example.com")
    workspaces = await client.get("/api/v1/workspaces", headers=auth_headers(token))
    workspace_id = workspaces.json()["items"][0]["id"]
    uploaded = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        headers={
            **auth_headers(token),
            "Idempotency-Key": "qwen-answer-error-upload",
        },
        files={
            "file": (
                "retention.txt",
                b"Production access retention is thirty days.",
                "text/plain",
            )
        },
        data={"sync": "true"},
    )
    assert uploaded.status_code == 202, uploaded.text

    class Embedding:
        mode = ProviderMode.LOCAL
        profile = "test-embedding"

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        async def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    class FailingAnswerLLM:
        profile = "alibaba-qwen-json:qwen-plus"
        answer_calls = 0

        async def rewrite(self, query: str, *, max_queries: int = 2) -> list[str]:
            return []

        async def answer(self, query: str, evidence: Any) -> Any:
            self.answer_calls += 1
            if failure_mode == "unknown_status":
                return StructuredAnswer(
                    "unexpected",
                    "must not be treated as an answer",
                    (),
                )
            raise ProviderUnavailableError(
                "upstream response included secret-api-key-that-must-not-leak"
            )

    llm = FailingAnswerLLM()
    settings = Settings(
        _env_file=None,
        app_env="test",
        jwt_secret="test-secret-that-is-longer-than-32-characters",
        rag_provider_mode="LOCAL",
        rag_evidence_min_score=0,
        rag_evidence_min_lexical_coverage=0,
        milvus_uri=None,
    )
    monkeypatch.setattr(knowledge_service_module, "_embedding", lambda value: Embedding())
    monkeypatch.setattr(knowledge_service_module, "_reranker", lambda value: None)
    monkeypatch.setattr(knowledge_service_module, "_llm", lambda value: llm)
    app.dependency_overrides[get_settings] = lambda: settings
    search_payload = {
        "query": "Production access retention",
        "retrieval_mode": "SPARSE",
        "rerank": "OFF",
        "rewrite": "OFF",
        "expand_context": False,
    }
    try:
        hits = await client.post(
            f"/api/v1/workspaces/{workspace_id}/search",
            headers=auth_headers(token),
            json={**search_payload, "response_mode": "HITS"},
        )
        answer = await client.post(
            f"/api/v1/workspaces/{workspace_id}/search",
            headers=auth_headers(token),
            json={**search_payload, "response_mode": "ANSWER"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert hits.status_code == 200, hits.text
    assert hits.json()["hits"]
    assert llm.answer_calls == 1
    assert answer.status_code == 503
    assert answer.json()["error"] == {
        "code": "llm_dependency_unavailable",
        "message": "The answer model provider is temporarily unavailable",
        "details": None,
    }
    assert "secret-api-key" not in answer.text
