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
from app.knowledge.types import (
    ChunkKind,
    FusionScoring,
    IndexedChunk,
    RetrievalResult,
    RetrievalScoring,
    ScoreAvailability,
    ScoreContribution,
    ScoreDetails,
    ScoredHit,
    ScoreStage,
)


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
        query_fusion_scores = {
            chunk_id: scores[chunk_id]["total"] for chunk_id in candidates
        }
        query_fusion_ranks = {
            chunk_id: rank for rank, chunk_id in enumerate(candidates, start=1)
        }
        source_observations, query_contributions = self._score_observations(
            dense_rankings,
            sparse_rankings,
            cfg,
        )
        channel_scoring, query_scoring = self._fusion_scoring(cfg, len(queries))
        enabled = [cfg.mode.value, cfg.fusion.value]
        rerank_scores_by_id: dict[str, float] = {}
        rerank_ranks_by_id: dict[str, int] = {}
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
                    rerank_scores_by_id[chunk_id] = rerank_score
                rerank_ranks_by_id = {
                    chunk_id: rank
                    for rank, chunk_id in enumerate(
                        sorted(
                            rerank_scores_by_id,
                            key=lambda value: (-rerank_scores_by_id[value], value),
                        ),
                        start=1,
                    )
                }
                candidates.sort(key=lambda chunk_id: (-scores[chunk_id]["total"], chunk_id))
                enabled.append("RERANK")
        hits: list[ScoredHit] = []
        for final_rank, chunk_id in enumerate(candidates[: cfg.top_k], start=1):
            item = self.by_id[chunk_id]
            context = self._context(item) if cfg.expand_context else ()
            hits.append(
                ScoredHit(
                    chunk=item.chunk,
                    score=scores[chunk_id]["total"],
                    scores=dict(scores[chunk_id]),
                    score_details=self._score_details(
                        chunk_id=chunk_id,
                        config=cfg,
                        source_observations=source_observations,
                        query_contributions=query_contributions,
                        query_fusion_score=query_fusion_scores[chunk_id],
                        query_fusion_rank=query_fusion_ranks[chunk_id],
                        rerank_score=rerank_scores_by_id.get(chunk_id),
                        rerank_rank=rerank_ranks_by_id.get(chunk_id),
                        final_score=scores[chunk_id]["total"],
                        final_rank=final_rank,
                        query_count=len(queries),
                    ),
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
            scoring=RetrievalScoring(
                requested_retrieval_mode=cfg.mode.value,
                effective_retrieval_mode=cfg.mode.value,
                query_count=len(queries),
                channel_fusion=channel_scoring,
                query_fusion=query_scoring,
                rerank_applied=bool(rerank_scores_by_id),
            ),
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

    @staticmethod
    def _score_observations(
        dense_rankings: Sequence[Sequence[tuple[IndexedChunk, float]]],
        sparse_rankings: Sequence[Sequence[tuple[IndexedChunk, float]]],
        config: RetrievalConfig,
    ) -> tuple[
        dict[str, dict[str, list[ScoreContribution]]],
        dict[str, list[ScoreContribution]],
    ]:
        sources: dict[str, dict[str, list[ScoreContribution]]] = defaultdict(
            lambda: {"dense_similarity": [], "bm25": [], "channel_fusion": []}
        )
        query_values: dict[tuple[int, str], float] = defaultdict(float)
        channels = (
            ("dense_similarity", dense_rankings, config.dense_weight),
            ("bm25", sparse_rankings, config.sparse_weight),
        )
        for source, rankings, weight in channels:
            for query_index, ranking in enumerate(rankings):
                maximum = max((score for _, score in ranking), default=0.0)
                minimum = min((score for _, score in ranking), default=0.0)
                for rank, (item, score) in enumerate(ranking, start=1):
                    chunk_id = item.chunk.chunk_id
                    sources[chunk_id][source].append(
                        ScoreContribution(
                            query_index=query_index,
                            value=score,
                            rank=rank,
                            source=source,
                        )
                    )
                    if config.fusion is FusionMode.RRF:
                        contribution = 1.0 / (config.rrf_k + rank)
                    else:
                        contribution = (
                            (score - minimum) / (maximum - minimum)
                            if maximum > minimum
                            else (1.0 if score > 0 else 0.0)
                        ) * weight
                    query_values[(query_index, chunk_id)] += contribution
        query_contributions: dict[str, list[ScoreContribution]] = defaultdict(list)
        for (query_index, chunk_id), contribution in query_values.items():
            observation = ScoreContribution(
                query_index=query_index,
                value=contribution,
                source="query_fusion",
            )
            query_contributions[chunk_id].append(observation)
            if config.mode is RetrievalMode.HYBRID:
                sources[chunk_id]["channel_fusion"].append(
                    ScoreContribution(
                        query_index=query_index,
                        value=contribution,
                        source="channel_fusion",
                    )
                )
        return sources, query_contributions

    @staticmethod
    def _score_details(
        *,
        chunk_id: str,
        config: RetrievalConfig,
        source_observations: dict[str, dict[str, list[ScoreContribution]]],
        query_contributions: dict[str, list[ScoreContribution]],
        query_fusion_score: float,
        query_fusion_rank: int,
        rerank_score: float | None,
        rerank_rank: int | None,
        final_score: float,
        final_rank: int,
        query_count: int,
    ) -> ScoreDetails:
        by_source = source_observations.get(chunk_id, {})

        def available_stage(source: str) -> ScoreStage:
            observations = tuple(by_source.get(source, ()))
            if not observations:
                return ScoreStage(availability=ScoreAvailability.NOT_RUN)
            return ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=max(
                    (observation.value for observation in observations),
                    default=None,
                ),
                rank=min(
                    (
                        observation.rank
                        for observation in observations
                        if observation.rank is not None
                    ),
                    default=None,
                ),
                method="COSINE" if source == "dense_similarity" else "BM25",
                per_query=observations,
            )

        unavailable = ScoreStage(availability=ScoreAvailability.NOT_RUN)
        not_applicable = ScoreStage(availability=ScoreAvailability.NOT_APPLICABLE)
        dense = (
            available_stage("dense_similarity")
            if config.mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}
            else unavailable
        )
        bm25 = (
            available_stage("bm25")
            if config.mode in {RetrievalMode.SPARSE, RetrievalMode.HYBRID}
            else unavailable
        )
        channel = not_applicable
        if config.mode is RetrievalMode.HYBRID:
            channel_observations = tuple(by_source.get("channel_fusion", ()))
            channel = ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=sum(item.value for item in channel_observations),
                method=(
                    config.fusion.value
                    if query_count == 1
                    else "COMBINED_FUSION"
                ),
                rrf_k=config.rrf_k if config.fusion is FusionMode.RRF else None,
                per_query=channel_observations,
            )
        contributions = tuple(query_contributions.get(chunk_id, ()))
        _, query_scoring = HybridRetriever._fusion_scoring(config, query_count)
        query_stage_contributions = contributions if query_scoring.applied else ()
        return ScoreDetails(
            dense_similarity=dense,
            bm25=bm25,
            channel_fusion=channel,
            query_fusion=ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=query_fusion_score,
                rank=query_fusion_rank,
                method=query_scoring.method,
                rrf_k=query_scoring.rrf_k,
                per_query=query_stage_contributions,
                contributions=query_stage_contributions,
            ),
            rerank=(
                ScoreStage(
                    availability=ScoreAvailability.AVAILABLE,
                    value=rerank_score,
                    rank=rerank_rank,
                    method="RERANKER",
                )
                if rerank_score is not None
                else unavailable
            ),
            final=ScoreStage(
                availability=ScoreAvailability.AVAILABLE,
                value=final_score,
                rank=final_rank,
                method="WEIGHTED_SUM" if rerank_score is not None else "IDENTITY",
            ),
        )

    @staticmethod
    def _fusion_scoring(
        config: RetrievalConfig,
        query_count: int,
    ) -> tuple[FusionScoring, FusionScoring]:
        uses_rrf = config.fusion is FusionMode.RRF
        if config.mode is not RetrievalMode.HYBRID:
            return (
                FusionScoring(method=None, applied=False),
                FusionScoring(
                    method=config.fusion.value,
                    applied=True,
                    rrf_k=config.rrf_k if uses_rrf else None,
                ),
            )
        if query_count == 1:
            return (
                FusionScoring(
                    method=config.fusion.value,
                    applied=True,
                    rrf_k=config.rrf_k if uses_rrf else None,
                ),
                FusionScoring(method="IDENTITY", applied=False),
            )
        combined = FusionScoring(
            method="COMBINED_FUSION",
            applied=True,
            rrf_k=config.rrf_k if uses_rrf else None,
        )
        return combined, combined

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
