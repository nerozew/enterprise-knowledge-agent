"""Configurable deterministic P4 retrieval pipeline."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.knowledge.providers import (
    EmbeddingProvider,
    LLMProvider,
    ProviderUnavailableError,
    RerankProvider,
    cosine_similarity,
    lexical_features,
)
from app.knowledge.types import ChunkKind, IndexedChunk, RetrievalResult, ScoredHit


class RetrievalMode(StrEnum):
    DENSE = "DENSE"
    SPARSE = "SPARSE"
    HYBRID = "HYBRID"


class FusionMode(StrEnum):
    RRF = "RRF"
    WEIGHTED = "WEIGHTED"


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    mode: RetrievalMode = RetrievalMode.HYBRID
    fusion: FusionMode = FusionMode.RRF
    top_k: int = 5
    candidate_k: int = 30
    rrf_k: int = 60
    dense_weight: float = 0.65
    sparse_weight: float = 0.35
    rerank: bool = True
    rewrite: bool = False
    multi_query: bool = False
    expand_context: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.top_k <= self.candidate_k <= 200:
            raise ValueError("require 1 <= top_k <= candidate_k <= 200")
        if self.rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        if self.dense_weight < 0 or self.sparse_weight < 0:
            raise ValueError("fusion weights cannot be negative")
        if self.fusion is FusionMode.WEIGHTED and not (self.dense_weight + self.sparse_weight > 0):
            raise ValueError("at least one fusion weight must be positive")


class BM25Index:
    """Small in-memory BM25 implementation used for L1 and fallback tests."""

    def __init__(self, chunks: Sequence[IndexedChunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.tokens = [lexical_features(item.chunk.text) for item in chunks]
        self.lengths = [len(tokens) for tokens in self.tokens]
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.document_frequency: Counter[str] = Counter()
        for tokens in self.tokens:
            self.document_frequency.update(set(tokens))

    def search(self, query: str, limit: int) -> list[tuple[IndexedChunk, float]]:
        query_terms = lexical_features(query)
        count = len(self.chunks)
        scored: list[tuple[IndexedChunk, float]] = []
        for item, tokens, length in zip(self.chunks, self.tokens, self.lengths, strict=True):
            frequencies = Counter(tokens)
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                document_frequency = self.document_frequency[term]
                idf = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
                normalizer = self.k1 * (
                    1 - self.b + self.b * length / max(self.average_length, 1.0)
                )
                score += idf * frequency * (self.k1 + 1) / (frequency + normalizer)
            if score > 0:
                scored.append((item, score))
        return sorted(scored, key=lambda pair: (-pair[1], pair[0].chunk.chunk_id))[:limit]


class HybridRetriever:
    """Dense + BM25 candidate generation, fusion, reranking, and context expansion."""

    def __init__(
        self,
        *,
        chunks: Sequence[IndexedChunk],
        embedding: EmbeddingProvider,
        reranker: RerankProvider | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self.embedding = embedding
        self.reranker = reranker
        self.llm = llm
        self.all_chunks = tuple(item for item in chunks if item.active)
        self.chunks = tuple(item for item in self.all_chunks if item.chunk.kind is ChunkKind.CHILD)
        self.by_id = {item.chunk.chunk_id: item for item in self.all_chunks}
        self.bm25 = BM25Index(self.chunks)

    async def search(self, query: str, config: RetrievalConfig | None = None) -> RetrievalResult:
        cfg = config or RetrievalConfig()
        query = query.strip()
        if not query or len(query) > 2_000:
            raise ValueError("query length must be between 1 and 2000 characters")
        queries = [query]
        warnings: list[str] = []
        degraded = False
        if cfg.rewrite:
            if self.llm is None:
                warnings.append("query rewrite requested but LLM provider is unavailable")
                degraded = True
            else:
                try:
                    max_rewrites = 2 if cfg.multi_query else 1
                    rewritten = await self.llm.rewrite(query, max_queries=max_rewrites)
                    queries.extend(item for item in rewritten if item not in queries)
                except ProviderUnavailableError as exc:
                    warnings.append(str(exc))
                    degraded = True
        dense_rankings: list[list[tuple[IndexedChunk, float]]] = []
        sparse_rankings: list[list[tuple[IndexedChunk, float]]] = []
        for actual_query in queries:
            if cfg.mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}:
                query_vector = await self.embedding.embed_query(actual_query)
                dense_rankings.append(self._dense(query_vector, cfg.candidate_k))
            if cfg.mode in {RetrievalMode.SPARSE, RetrievalMode.HYBRID}:
                sparse_rankings.append(self.bm25.search(actual_query, cfg.candidate_k))
        scores = self._fuse(dense_rankings, sparse_rankings, cfg)
        candidates = sorted(scores, key=lambda chunk_id: (-scores[chunk_id]["total"], chunk_id))
        candidates = candidates[: cfg.candidate_k]
        enabled = [cfg.mode.value, cfg.fusion.value]
        if cfg.rerank and candidates:
            if self.reranker is None:
                warnings.append("rerank requested but provider is unavailable")
                degraded = True
            else:
                documents = [self.by_id[chunk_id].chunk.text for chunk_id in candidates[:30]]
                rerank_scores = await self.reranker.rerank(query, documents)
                for chunk_id, rerank_score in zip(candidates[:30], rerank_scores, strict=True):
                    scores[chunk_id]["rerank"] = rerank_score
                    scores[chunk_id]["total"] = 0.4 * scores[chunk_id]["total"] + 0.6 * rerank_score
                candidates.sort(key=lambda chunk_id: (-scores[chunk_id]["total"], chunk_id))
                enabled.append("RERANK")
        hits: list[ScoredHit] = []
        for chunk_id in candidates[: cfg.top_k]:
            item = self.by_id[chunk_id]
            context = self._context(item) if cfg.expand_context else ()
            hits.append(
                ScoredHit(
                    chunk=item.chunk,
                    score=scores[chunk_id]["total"],
                    scores=dict(scores[chunk_id]),
                    context=context,
                )
            )
        if cfg.expand_context:
            enabled.append("CONTEXT_EXPANSION")
        if len(queries) > 1:
            enabled.append("QUERY_REWRITE")
        return RetrievalResult(
            hits=tuple(hits),
            queries=tuple(queries),
            enabled_components=tuple(enabled),
            provider_mode=self.embedding.mode.value,
            degraded=degraded,
            warnings=tuple(warnings),
        )

    def _dense(self, query_vector: Sequence[float], limit: int) -> list[tuple[IndexedChunk, float]]:
        scored = [(item, cosine_similarity(query_vector, item.vector)) for item in self.chunks]
        return sorted(scored, key=lambda pair: (-pair[1], pair[0].chunk.chunk_id))[:limit]

    @staticmethod
    def _fuse(
        dense_rankings: Sequence[Sequence[tuple[IndexedChunk, float]]],
        sparse_rankings: Sequence[Sequence[tuple[IndexedChunk, float]]],
        config: RetrievalConfig,
    ) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = defaultdict(
            lambda: {"dense": 0.0, "sparse": 0.0, "total": 0.0}
        )
        channels = (("dense", dense_rankings), ("sparse", sparse_rankings))
        for name, rankings in channels:
            for ranking in rankings:
                maximum = max((score for _, score in ranking), default=0.0)
                minimum = min((score for _, score in ranking), default=0.0)
                for rank, (item, score) in enumerate(ranking, start=1):
                    chunk_id = item.chunk.chunk_id
                    if config.fusion is FusionMode.RRF:
                        contribution = 1.0 / (config.rrf_k + rank)
                    else:
                        contribution = (
                            (score - minimum) / (maximum - minimum)
                            if maximum > minimum
                            else (1.0 if score > 0 else 0.0)
                        )
                        contribution *= (
                            config.dense_weight if name == "dense" else config.sparse_weight
                        )
                    result[chunk_id][name] += contribution
                    result[chunk_id]["total"] += contribution
        return result

    def _context(self, item: IndexedChunk) -> tuple:
        chunk = item.chunk
        candidates = [
            indexed.chunk
            for indexed in self.all_chunks
            if indexed.chunk.document_version_id == chunk.document_version_id
            and (
                indexed.chunk.chunk_id == chunk.parent_id
                or (
                    indexed.chunk.parent_id == chunk.parent_id
                    and abs(indexed.chunk.ordinal - chunk.ordinal) == 1
                )
            )
        ]
        return tuple(sorted(candidates, key=lambda candidate: candidate.ordinal))
