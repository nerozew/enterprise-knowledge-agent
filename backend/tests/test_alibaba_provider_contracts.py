from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.knowledge.errors import ProviderUnavailableError
from app.knowledge.providers import (
    EvidenceItem,
    LLMJSONProfile,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLMProvider,
    OpenAICompatibleRerankProvider,
    ProviderMode,
    RerankAPIProfile,
)
from app.services.knowledge import _reranker, knowledge_capabilities


@pytest.mark.asyncio
async def test_alibaba_workspace_embedding_uses_openai_compatible_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-mode/v1/",
    ) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://workspace.example/compatible-mode/v1",
            model="text-embedding-v4",
            mode=ProviderMode.REMOTE,
            api_key="mock-api-key",
            client=client,
        )
        assert await provider.embed_query("enterprise knowledge") == [0.25, 0.75]

    assert requests[0].url.path == "/compatible-mode/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer mock-api-key"
    assert json.loads(requests[0].content) == {
        "model": "text-embedding-v4",
        "input": ["enterprise knowledge"],
    }


@pytest.mark.asyncio
async def test_alibaba_qwen3_rerank_uses_official_flat_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ],
                "model": "qwen3-rerank",
                "id": "mock-request-id",
                "usage": {"total_tokens": 10},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-api/v1/",
    ) as client:
        provider = OpenAICompatibleRerankProvider(
            base_url="https://workspace.example/compatible-api/v1",
            model="qwen3-rerank",
            mode=ProviderMode.REMOTE,
            api_key="mock-api-key",
            client=client,
            api_profile=RerankAPIProfile.ALIBABA_QWEN3,
        )
        assert await provider.rerank("query", ["first", "second"]) == [0.2, 0.9]

    assert requests[0].url.path == "/compatible-api/v1/reranks"
    assert requests[0].headers["Authorization"] == "Bearer mock-api-key"
    assert json.loads(requests[0].content) == {
        "model": "qwen3-rerank",
        "query": "query",
        "documents": ["first", "second"],
        "top_n": 2,
    }


@pytest.mark.asyncio
async def test_alibaba_qwen3_rerank_rejects_partial_or_duplicate_results() -> None:
    responses = [
        {
            "object": "list",
            "results": [{"index": 0, "relevance_score": 0.8}],
        },
        {
            "object": "list",
            "results": [
                {"index": 0, "relevance_score": 0.8},
                {"index": 0, "relevance_score": 0.7},
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-api/v1/",
    ) as client:
        provider = OpenAICompatibleRerankProvider(
            base_url="https://workspace.example/compatible-api/v1",
            model="qwen3-rerank",
            mode=ProviderMode.LOCAL,
            client=client,
            api_profile=RerankAPIProfile.ALIBABA_QWEN3,
        )
        with pytest.raises(ProviderUnavailableError, match="wrong item count"):
            await provider.rerank("query", ["first", "second"])
        with pytest.raises(ProviderUnavailableError, match="malformed"):
            await provider.rerank("query", ["first", "second"])


@pytest.mark.asyncio
async def test_alibaba_qwen_chat_uses_json_object_and_validates_schema_locally() -> None:
    requests: list[httpx.Request] = []
    responses = [
        {"queries": ["workspace access", "member permissions"]},
        {"queries": ["one", "two", "three"]},
        {"queries": "invalid-not-a-list"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(responses.pop(0))}},
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-mode/v1/",
    ) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://workspace.example/compatible-mode/v1",
            model="qwen-plus",
            mode=ProviderMode.REMOTE,
            api_key="mock-api-key",
            client=client,
            json_profile=LLMJSONProfile.ALIBABA_QWEN,
        )
        assert await provider.rewrite("permissions", max_queries=2) == [
            "workspace access",
            "member permissions",
        ]
        assert await provider.rewrite("permissions", max_queries=2) == [
            "one",
            "two",
        ]

        with pytest.raises(ProviderUnavailableError, match="required schema"):
            await provider.rewrite("permissions", max_queries=2)

    assert all(request.url.path == "/compatible-mode/v1/chat/completions" for request in requests)
    for request in requests:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "json_schema" not in payload["response_format"]
        assert "json object" in payload["messages"][0]["content"].casefold()


@pytest.mark.asyncio
async def test_alibaba_qwen_answer_normalizes_success_to_answered() -> None:
    responses = [
        {
            "status": "success",
            "text": "应在30分钟内通知IT服务台和直属负责人。",
            "cited_hit_ids": ["allowed-hit"],
        },
        {
            "status": "success",
            "text": "缺少引用的文本不能成为已回答结果。",
            "cited_hit_ids": [],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(responses.pop(0))
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-mode/v1/",
    ) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://workspace.example/compatible-mode/v1",
            model="qwen-plus",
            mode=ProviderMode.REMOTE,
            api_key="mock-api-key",
            client=client,
            json_profile=LLMJSONProfile.ALIBABA_QWEN,
        )

        answer = await provider.answer(
            "设备遗失后应在多长时间内通知谁？",
            [
                EvidenceItem(
                    "allowed-hit",
                    "设备遗失后应在30分钟内通知IT服务台和直属负责人。",
                )
            ],
        )
        with pytest.raises(
            ProviderUnavailableError,
            match="must contain text and citations",
        ):
            await provider.answer(
                "设备遗失后应在多长时间内通知谁？",
                [EvidenceItem("allowed-hit", "应通知IT服务台。")],
            )

    assert answer.status == "answered"
    assert answer.text == "应在30分钟内通知IT服务台和直属负责人。"
    assert answer.cited_hit_ids == ("allowed-hit",)


@pytest.mark.asyncio
async def test_alibaba_qwen_answer_requests_json_object_and_enforces_citation_rules() -> None:
    requests: list[httpx.Request] = []
    responses = [
        {
            "status": "refused",
            "text": "The supplied evidence is insufficient.",
            "cited_hit_ids": [],
        },
        {
            "status": "answered",
            "text": "This answer cites evidence that was not supplied.",
            "cited_hit_ids": ["unknown-hit"],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(responses.pop(0))}},
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-mode/v1/",
    ) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://workspace.example/compatible-mode/v1",
            model="qwen-plus",
            mode=ProviderMode.REMOTE,
            api_key="mock-api-key",
            client=client,
            json_profile=LLMJSONProfile.ALIBABA_QWEN,
        )
        refused = await provider.answer(
            "What is the retention period?",
            [EvidenceItem("allowed-hit", "The evidence does not state a retention period.")],
        )
        assert refused.status == "refused"
        assert refused.cited_hit_ids == ()

        with pytest.raises(ProviderUnavailableError, match="unknown evidence"):
            await provider.answer(
                "What is the retention period?",
                [EvidenceItem("allowed-hit", "The retention period is thirty days.")],
            )

    assert len(requests) == 2
    for request in requests:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "json object" in payload["messages"][0]["content"].casefold()
        assert "untrusted evidence" in payload["messages"][0]["content"].casefold()


@pytest.mark.parametrize(
    "endpoint",
    [
        "/reranks",
        r"nested\reranks",
        "./reranks",
        "../reranks",
        "nested/../reranks",
        "nested/./reranks",
        "%2e%2e/reranks",
        "nested%2freranks",
        "reranks%5cadmin",
        "reranks\nadmin",
        "\treranks",
        "reranks\r",
    ],
)
def test_alibaba_profile_rejects_endpoint_that_can_escape_base(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="relative URL path"):
        Settings(
            _env_file=None,
            app_env="test",
            rag_reranker_endpoint=endpoint,
        )
    with pytest.raises(ValueError, match="relative URL path"):
        OpenAICompatibleRerankProvider(
            base_url="https://workspace.example/compatible-api/v1",
            model="qwen3-rerank",
            mode=ProviderMode.LOCAL,
            endpoint=endpoint,
        )


def test_alibaba_profile_config_validation() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        rag_provider_mode="REMOTE",
        rag_reranker_url="https://workspace.example/compatible-api/v1",
        rag_reranker_api_key="mock-api-key",
        rag_reranker_model="qwen3-rerank",
        rag_reranker_profile="ALIBABA_QWEN3",
        rag_reranker_endpoint="custom/reranks",
        rag_llm_profile="ALIBABA_QWEN",
    )
    assert settings.rag_reranker_endpoint == "custom/reranks"

    with pytest.raises(ValidationError, match="qwen3-rerank"):
        Settings(
            _env_file=None,
            app_env="test",
            rag_reranker_profile="ALIBABA_QWEN3",
            rag_reranker_model="another-model",
        )


def test_alibaba_settings_factory_uses_qwen3_default_reranks_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        rag_provider_mode="LOCAL",
        rag_reranker_url="https://workspace.example/compatible-api/v1",
        rag_reranker_model="qwen3-rerank",
        rag_reranker_profile="ALIBABA_QWEN3",
        rag_reranker_endpoint="",
    )
    provider = _reranker(settings)
    assert isinstance(provider, OpenAICompatibleRerankProvider)
    assert settings.rag_reranker_endpoint is None
    assert provider.api_profile is RerankAPIProfile.ALIBABA_QWEN3
    assert provider.endpoint == "reranks"


@pytest.mark.asyncio
async def test_knowledge_capabilities_exposes_provider_profiles_without_network_calls() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        rag_provider_mode="REMOTE",
        rag_embedding_url="https://workspace.example/compatible-mode/v1",
        rag_embedding_api_key="mock-api-key",
        rag_embedding_model="text-embedding-v4",
        rag_reranker_url="https://workspace.example/compatible-api/v1",
        rag_reranker_api_key="mock-api-key",
        rag_reranker_model="qwen3-rerank",
        rag_reranker_profile="ALIBABA_QWEN3",
        rag_llm_url="https://workspace.example/compatible-mode/v1",
        rag_llm_api_key="mock-api-key",
        rag_llm_model="qwen-plus",
        rag_llm_profile="ALIBABA_QWEN",
        redis_url=None,
        minio_endpoint=None,
        milvus_uri=None,
    )
    capabilities = await knowledge_capabilities(settings)
    provider = capabilities["provider"]
    assert provider["configured"] is True
    assert provider["healthy"] is False
    assert provider["checked"] is False
    assert provider["status"] == "UNKNOWN"
    assert provider["components"] == {
        "embedding": True,
        "reranker": True,
        "llm": True,
    }
    assert provider["profiles"] == {
        "reranker": "ALIBABA_QWEN3",
        "llm": "ALIBABA_QWEN",
    }


@pytest.mark.asyncio
async def test_provider_sends_bearer_header_and_redacts_remote_error_details() -> None:
    api_key = "mock-secret-never-log"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {api_key}"
        return httpx.Response(
            401,
            json={"message": f"rejected credential {api_key}"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://workspace.example/compatible-api/v1/",
    ) as client:
        provider = OpenAICompatibleRerankProvider(
            base_url="https://workspace.example/compatible-api/v1",
            model="qwen3-rerank",
            mode=ProviderMode.REMOTE,
            api_key=api_key,
            client=client,
            api_profile=RerankAPIProfile.ALIBABA_QWEN3,
        )
        with pytest.raises(ProviderUnavailableError) as captured:
            await provider.rerank("query", ["document"])

    message = str(captured.value)
    assert message == "model provider request failed"
    assert api_key not in message
