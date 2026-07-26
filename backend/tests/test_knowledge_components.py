from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from app.knowledge.adapters import (
    FakeCacheStore,
    FakeObjectStore,
    FakeVectorStore,
    MilvusVectorStore,
    VectorRecord,
    collection_name,
)
from app.knowledge.chunking import ChunkingConfig, ParentChildChunker
from app.knowledge.errors import (
    AdapterUnavailableError,
    ProviderUnavailableError,
    UnsafeDocumentError,
)
from app.knowledge.metrics import ndcg_at_k, recall_at_k, reciprocal_rank_at_k
from app.knowledge.parsers import DocumentParser, ParserLimits
from app.knowledge.providers import (
    DisabledEmbeddingProvider,
    EvidenceItem,
    FixtureEmbeddingProvider,
    FixtureLLMProvider,
    LexicalRerankProvider,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLMProvider,
    OpenAICompatibleRerankProvider,
    ProviderMode,
)
from app.knowledge.retrieval import (
    FusionMode,
    HybridRetriever,
    RetrievalConfig,
    RetrievalMode,
)
from app.knowledge.types import (
    BlockKind,
    ChunkKind,
    IndexedChunk,
    KnowledgeChunk,
)


def _docx(
    *,
    paragraphs: list[tuple[str, str]],
    external_relationship: bool = False,
    extra_entry: str | None = None,
) -> bytes:
    body = []
    for text, style in paragraphs:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r></w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )
    rel = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="https://example.com/payload" TargetMode="External"/>'
        "</Relationships>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        if external_relationship:
            archive.writestr("word/_rels/document.xml.rels", rel)
        if extra_entry:
            archive.writestr(extra_entry, "unsafe")
    return stream.getvalue()


def _chunk(
    chunk_id: str,
    text: str,
    *,
    version: str = "ver-1",
    ordinal: int = 1,
    parent_id: str | None = None,
    kind: ChunkKind = ChunkKind.CHILD,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        document_version_id=version,
        kind=kind,
        ordinal=ordinal,
        text=text,
        page=1,
        heading_path=("知识库",),
        char_start=ordinal * 10,
        char_end=ordinal * 10 + len(text),
        source_span_id=f"span-{chunk_id}",
        parent_id=parent_id,
    )


def test_text_markdown_and_docx_structure_and_safety() -> None:
    parser = DocumentParser()
    text = parser.parse(
        filename="policy.txt",
        content="第一段。\n\n第二段。".encode(),
        mime_type="text/plain; charset=utf-8",
    )
    assert [block.kind for block in text.blocks] == [
        BlockKind.PARAGRAPH,
        BlockKind.PARAGRAPH,
    ]
    markdown = parser.parse(
        filename="guide.md",
        content="# 部署\n\n- Redis\n- Milvus".encode(),
        mime_type="text/markdown",
    )
    assert markdown.blocks[0].kind is BlockKind.HEADING
    assert markdown.blocks[1].heading_path == ("部署",)

    docx = parser.parse(
        filename="guide.docx",
        content=_docx(paragraphs=[("总则", "Heading1"), ("只发布稳定版本", "")]),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert [(block.kind, block.heading_path) for block in docx.blocks] == [
        (BlockKind.HEADING, ("总则",)),
        (BlockKind.PARAGRAPH, ("总则",)),
    ]

    with pytest.raises(UnsafeDocumentError, match="external relationships"):
        parser.parse(
            filename="bad.docx",
            content=_docx(paragraphs=[("内容", "")], external_relationship=True),
        )
    with pytest.raises(UnsafeDocumentError, match="unsafe archive path"):
        parser.parse(
            filename="bad.docx",
            content=_docx(paragraphs=[("内容", "")], extra_entry="../payload.xml"),
        )
    with pytest.raises(UnsafeDocumentError, match="active content"):
        parser.parse(
            filename="bad.docx",
            content=_docx(paragraphs=[("内容", "")], extra_entry="word/vbaProject.bin"),
        )
    with pytest.raises(UnsafeDocumentError, match="MIME"):
        parser.parse(filename="bad.txt", content=b"text", mime_type="application/pdf")
    with pytest.raises(UnsafeDocumentError, match="binary NUL"):
        parser.parse(filename="bad.txt", content=b"text\x00binary")


def test_pdf_page_limit_encryption_and_page_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        encrypted = False
        page_count = 2

        def __init__(self, stream: io.BytesIO, strict: bool) -> None:
            assert stream.read(5) == b"%PDF-"
            self.is_encrypted = self.encrypted
            self.pages = [Page(f"第{index}页证据") for index in range(1, self.page_count + 1)]

    monkeypatch.setattr(
        "app.knowledge.parsers.importlib.import_module",
        lambda name: SimpleNamespace(PdfReader=Reader),
    )
    parsed = DocumentParser().parse(
        filename="evidence.pdf", content=b"%PDF-1.7 fixture", mime_type="application/pdf"
    )
    assert [block.page for block in parsed.blocks] == [1, 2]
    assert parsed.blocks[0].char_start == 0
    assert parsed.blocks[1].char_start > parsed.blocks[0].char_end

    Reader.encrypted = True
    with pytest.raises(UnsafeDocumentError, match="encrypted"):
        DocumentParser().parse(filename="secret.pdf", content=b"%PDF-1.7 fixture")
    Reader.encrypted = False
    Reader.page_count = 2
    with pytest.raises(UnsafeDocumentError, match="page limit"):
        DocumentParser(ParserLimits(max_pdf_pages=1)).parse(
            filename="long.pdf", content=b"%PDF-1.7 fixture"
        )


def test_parent_child_chunking_is_stable_and_preserves_source_ranges() -> None:
    parsed = DocumentParser().parse(
        filename="guide.md",
        content=(
            "# 发布\n\n第一条规则要求先完成校验。第二条规则要求保存来源。"
            "第三条规则要求记录版本。第四条规则要求支持重试。"
        ).encode(),
        mime_type="text/markdown",
    )
    chunker = ParentChildChunker(
        ChunkingConfig(
            parent_max_tokens=100,
            child_target_tokens=12,
            child_min_tokens=4,
            child_max_tokens=16,
            child_overlap_tokens=3,
        )
    )
    first = chunker.chunk(parsed, document_id="doc-1", document_version_id="ver-1")
    second = chunker.chunk(parsed, document_id="doc-1", document_version_id="ver-1")
    assert first == second
    parents = [chunk for chunk in first if chunk.kind is ChunkKind.PARENT]
    children = [chunk for chunk in first if chunk.kind is ChunkKind.CHILD]
    assert parents and len(children) >= 2
    assert all(child.parent_id in {parent.chunk_id for parent in parents} for child in children)
    assert all(child.char_start < child.char_end for child in children)
    assert all(len(child.source_span_id) == 64 for child in children)
    changed = chunker.chunk(parsed, document_id="doc-1", document_version_id="ver-2")
    assert {chunk.source_span_id for chunk in first}.isdisjoint(
        chunk.source_span_id for chunk in changed
    )


@pytest.mark.asyncio
async def test_fixture_provider_hybrid_retrieval_rewrite_rerank_and_context() -> None:
    embedding = FixtureEmbeddingProvider(dimensions=64)
    parent = _chunk(
        "parent",
        "Redis 用于缓存和进度通知",
        ordinal=0,
        kind=ChunkKind.PARENT,
    )
    source_chunks = [
        parent,
        _chunk("redis", "Redis 缓存必须使用 redis asyncio 客户端", parent_id="parent"),
        _chunk(
            "milvus",
            "Milvus 混合检索结合 Dense 向量与 BM25 稀疏检索",
            ordinal=2,
            parent_id="parent",
        ),
        _chunk("minio", "MinIO 保存私有文档原文件", ordinal=3, parent_id="parent"),
    ]
    vectors = await embedding.embed_documents([chunk.text for chunk in source_chunks])
    indexed = [
        IndexedChunk(chunk=chunk, workspace_id="ws-1", vector=tuple(vector))
        for chunk, vector in zip(source_chunks, vectors, strict=True)
    ]
    retriever = HybridRetriever(
        chunks=indexed,
        embedding=embedding,
        reranker=LexicalRerankProvider(),
        llm=FixtureLLMProvider({"向量稀疏融合": ["Dense BM25 混合检索"]}),
    )
    result = await retriever.search(
        "向量稀疏融合",
        RetrievalConfig(
            mode=RetrievalMode.HYBRID,
            fusion=FusionMode.RRF,
            top_k=2,
            candidate_k=4,
            rewrite=True,
            multi_query=True,
            expand_context=True,
        ),
    )
    assert result.hits[0].chunk.chunk_id == "milvus"
    assert result.provider_mode == ProviderMode.FIXTURE
    assert result.queries == ("向量稀疏融合", "Dense BM25 混合检索")
    assert {"dense", "sparse", "rerank", "total"} <= result.hits[0].scores.keys()
    assert {item.chunk_id for item in result.hits[0].context} == {
        "parent",
        "redis",
        "minio",
    }
    assert "QUERY_REWRITE" in result.enabled_components
    weighted = await retriever.search(
        "Milvus BM25",
        RetrievalConfig(
            mode=RetrievalMode.HYBRID,
            fusion=FusionMode.WEIGHTED,
            top_k=1,
            candidate_k=4,
            rerank=False,
            expand_context=False,
        ),
    )
    assert weighted.hits[0].chunk.chunk_id == "milvus"


@pytest.mark.asyncio
async def test_retrieval_reports_degradation_instead_of_silent_fallback() -> None:
    embedding = FixtureEmbeddingProvider(dimensions=16)
    chunk = _chunk("one", "发布版本才可检索")
    vector = await embedding.embed_query(chunk.text)
    retriever = HybridRetriever(
        chunks=[IndexedChunk(chunk, "ws-1", tuple(vector))],
        embedding=embedding,
    )
    result = await retriever.search(
        "发布版本",
        RetrievalConfig(top_k=1, candidate_k=1, rerank=True, rewrite=True),
    )
    assert result.degraded is True
    assert len(result.warnings) == 2
    with pytest.raises(ProviderUnavailableError):
        await DisabledEmbeddingProvider().embed_query("query")


@pytest.mark.asyncio
async def test_openai_compatible_http_providers_and_citation_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0, 1]},
                        {"index": 0, "embedding": [1, 0]},
                    ][: len(payload["input"])]
                },
            )
        if request.url.path.endswith("/rerank"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.2},
                    ]
                },
            )
        schema_name = payload["response_format"]["json_schema"]["name"]
        if schema_name == "query_rewrite":
            content = {"queries": ["企业权限保留周期", "权限保存多久"]}
        else:
            content = {
                "status": "answered",
                "text": "权限最多保留九十天。",
                "cited_hit_ids": ["hit-1"],
            }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://provider/v1/"
    )
    embedding = OpenAICompatibleEmbeddingProvider(
        base_url="http://provider/v1",
        model="embedding-model",
        mode=ProviderMode.LOCAL,
        client=client,
    )
    assert await embedding.embed_documents(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    reranker = OpenAICompatibleRerankProvider(
        base_url="http://provider/v1",
        model="rerank-model",
        mode=ProviderMode.LOCAL,
        client=client,
    )
    assert await reranker.rerank("q", ["a", "b"]) == [0.2, 0.9]
    llm = OpenAICompatibleLLMProvider(
        base_url="http://provider/v1",
        model="chat-model",
        mode=ProviderMode.LOCAL,
        client=client,
    )
    assert await llm.rewrite("权限多久") == ["企业权限保留周期", "权限保存多久"]
    answer = await llm.answer("权限多久", [EvidenceItem("hit-1", "最多保留九十天")])
    assert answer.cited_hit_ids == ("hit-1",)
    await client.aclose()

    with pytest.raises(ValueError, match="API key"):
        OpenAICompatibleEmbeddingProvider(
            base_url="https://provider.example/v1",
            model="embedding-model",
            mode=ProviderMode.REMOTE,
        )


@pytest.mark.asyncio
async def test_openai_compatible_answer_rejects_unknown_citation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "status": "answered",
                                    "text": "伪造引用",
                                    "cited_hit_ids": ["not-a-candidate"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://provider/v1/"
    )
    llm = OpenAICompatibleLLMProvider(
        base_url="http://provider/v1",
        model="chat-model",
        mode=ProviderMode.LOCAL,
        client=client,
    )
    with pytest.raises(ProviderUnavailableError, match="unknown evidence"):
        await llm.answer("query", [EvidenceItem("hit-1", "evidence")])
    await client.aclose()


def test_ranking_metrics() -> None:
    ranked = ["a", "b", "c"]
    assert recall_at_k(ranked, {"a", "c"}, k=2) == 0.5
    assert reciprocal_rank_at_k(ranked, {"b"}) == 0.5
    assert ndcg_at_k(ranked, {"a": 2.0, "c": 1.0}) == pytest.approx(0.96394, rel=1e-4)
    assert recall_at_k([], set()) == 1.0


@pytest.mark.asyncio
async def test_fake_adapters_workspace_filter_deactivate_and_close() -> None:
    objects = FakeObjectStore()
    await objects.put("knowledge-source", "ws/one", b"source", "text/plain")
    assert await objects.get("knowledge-source", "ws/one") == b"source"

    cache = FakeCacheStore()
    await cache.set("progress", b"50", ttl_seconds=10)
    assert await cache.get("progress") == b"50"

    vectors = FakeVectorStore()
    await vectors.ensure_schema("fixture_v1", dimensions=2)
    await vectors.upsert(
        "fixture_v1",
        [
            VectorRecord(
                "a",
                "ws-1",
                "doc",
                "ver",
                None,
                "one",
                (1.0, 0.0),
                True,
                source_format="TXT",
                page=1,
                created_at_ms=123,
                metadata={},
            ),
            VectorRecord(
                "b",
                "ws-2",
                "doc",
                "ver",
                None,
                "two",
                (1.0, 0.0),
                True,
                source_format="TXT",
                page=2,
                created_at_ms=124,
                metadata={},
            ),
        ],
    )
    stored = vectors.collections["knowledge_chunks__fixture_v1"]["a"]
    assert stored.active is False
    assert (stored.source_format, stored.page, stored.created_at_ms) == ("TXT", 1, 123)
    assert (
        await vectors.search(
            "fixture_v1",
            workspace_id="ws-1",
                query_vector=(1.0, 0.0),
                query_text="one",
                limit=5,
                retrieval_mode="DENSE",
        )
        == []
    )
    await vectors.activate_version("fixture_v1", "ws-1", "ver")
    hits = await vectors.search(
        "fixture_v1",
        workspace_id="ws-1",
        query_vector=(1.0, 0.0),
        query_text="one",
        limit=5,
        retrieval_mode="DENSE",
    )
    assert hits == [("a", 1.0)]
    await vectors.deactivate_version("fixture_v1", "ws-1", "ver")
    assert (
        await vectors.search(
            "fixture_v1",
            workspace_id="ws-1",
                query_vector=(1.0, 0.0),
                query_text="one",
                limit=5,
                retrieval_mode="DENSE",
        )
        == []
    )
    await objects.close()
    with pytest.raises(AdapterUnavailableError, match="closed"):
        await objects.get("knowledge-source", "ws/one")
    assert collection_name("My_Profile") == "knowledge_chunks__my_profile"
    with pytest.raises(ValueError):
        collection_name("raw-filter == true")


@pytest.mark.asyncio
async def test_milvus_search_builds_mode_and_fusion_specific_requests() -> None:
    class FakeMilvusModule:
        @staticmethod
        def AnnSearchRequest(**kwargs):
            return kwargs

        @staticmethod
        def RRFRanker(*, k: int):
            return ("RRF", k)

        @staticmethod
        def WeightedRanker(dense_weight: float, sparse_weight: float):
            return ("WEIGHTED", dense_weight, sparse_weight)

    class FakeMilvusClient:
        def __init__(self) -> None:
            self.search_calls: list[dict] = []
            self.hybrid_calls: list[dict] = []

        async def search(self, **kwargs):
            self.search_calls.append(kwargs)
            return [[{"id": "chunk-1", "distance": 0.9}]]

        async def hybrid_search(self, **kwargs):
            self.hybrid_calls.append(kwargs)
            return [[{"id": "chunk-2", "distance": 0.8}]]

    client = FakeMilvusClient()
    store = object.__new__(MilvusVectorStore)
    store._pymilvus = FakeMilvusModule()
    store._client = client

    await store.search(
        "fixture_v1",
        workspace_id="ws-1",
        query_vector=(1.0, 0.0),
        query_text="权限",
        limit=5,
        retrieval_mode="DENSE",
    )
    assert client.search_calls[-1]["anns_field"] == "dense_vector"
    assert client.search_calls[-1]["data"] == [[1.0, 0.0]]

    await store.search(
        "fixture_v1",
        workspace_id="ws-1",
        query_vector=(1.0, 0.0),
        query_text="权限",
        limit=5,
        retrieval_mode="SPARSE",
    )
    assert client.search_calls[-1]["anns_field"] == "sparse_vector"
    assert client.search_calls[-1]["data"] == ["权限"]

    await store.search(
        "fixture_v1",
        workspace_id="ws-1",
        query_vector=(1.0, 0.0),
        query_text="权限",
        limit=5,
        retrieval_mode="HYBRID",
        fusion="WEIGHTED",
        dense_weight=0.7,
        sparse_weight=0.3,
    )
    hybrid = client.hybrid_calls[-1]
    assert [request["anns_field"] for request in hybrid["reqs"]] == [
        "dense_vector",
        "sparse_vector",
    ]
    assert hybrid["ranker"] == ("WEIGHTED", 0.7, 0.3)
