"""Shared evidence sufficiency gate for runtime and evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.knowledge.providers import lexical_features
from app.knowledge.types import ScoredHit


@dataclass(frozen=True, slots=True)
class EvidenceGateConfig:
    min_top_score: float = 0.015
    min_lexical_coverage: float = 0.2
    min_hits: int = 1
    max_hits_for_coverage: int = 3

    def __post_init__(self) -> None:
        if self.min_top_score < 0:
            raise ValueError("min_top_score cannot be negative")
        if not 0 <= self.min_lexical_coverage <= 1:
            raise ValueError("min_lexical_coverage must be between zero and one")
        if self.min_hits < 1 or self.max_hits_for_coverage < 1:
            raise ValueError("hit counts must be positive")


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    accepted: bool
    code: str
    top_score: float
    gate_score: float
    lexical_coverage: float
    usable_hit_ids: tuple[str, ...]
    query_count: int
    score_threshold: float
    lexical_threshold: float
    lexical_passed: bool


class EvidenceGate:
    """Reject weak or untraceable hits with explicit, reproducible reasons."""

    def __init__(self, config: EvidenceGateConfig | None = None) -> None:
        self.config = config or EvidenceGateConfig()

    def evaluate(
        self,
        query: str,
        hits: Sequence[ScoredHit],
        *,
        query_count: int = 1,
        enforce_lexical_coverage: bool = True,
    ) -> EvidenceDecision:
        if query_count < 1:
            raise ValueError("query_count must be positive")
        traceable = tuple(
            hit for hit in hits if hit.chunk.document_version_id and hit.chunk.source_span_id
        )
        top_score = max((hit.score for hit in traceable), default=0.0)
        gate_score = max(
            (self._gate_score(hit, query_count) for hit in traceable),
            default=0.0,
        )
        coverage = self._lexical_coverage(query, traceable)
        usable = tuple(hit.chunk.chunk_id for hit in traceable)
        if len(traceable) < self.config.min_hits:
            return self._decision(
                False,
                "no_traceable_evidence",
                top_score,
                gate_score,
                coverage,
                usable,
                query_count,
            )
        if gate_score < self.config.min_top_score:
            return self._decision(
                False,
                "score_below_threshold",
                top_score,
                gate_score,
                coverage,
                usable,
                query_count,
            )
        if enforce_lexical_coverage and coverage < self.config.min_lexical_coverage:
            return self._decision(
                False,
                "lexical_coverage_below_threshold",
                top_score,
                gate_score,
                coverage,
                usable,
                query_count,
            )
        return self._decision(
            True,
            "accepted",
            top_score,
            gate_score,
            coverage,
            usable,
            query_count,
        )

    def _decision(
        self,
        accepted: bool,
        code: str,
        top_score: float,
        gate_score: float,
        lexical_coverage: float,
        usable_hit_ids: tuple[str, ...],
        query_count: int,
    ) -> EvidenceDecision:
        return EvidenceDecision(
            accepted=accepted,
            code=code,
            top_score=top_score,
            gate_score=gate_score,
            lexical_coverage=lexical_coverage,
            usable_hit_ids=usable_hit_ids,
            query_count=query_count,
            score_threshold=self.config.min_top_score,
            lexical_threshold=self.config.min_lexical_coverage,
            lexical_passed=lexical_coverage >= self.config.min_lexical_coverage,
        )

    @staticmethod
    def _gate_score(hit: ScoredHit, query_count: int) -> float:
        details = hit.score_details
        if details is None or details.query_fusion.value is None:
            return hit.score / query_count
        normalized_query_score = details.query_fusion.value / query_count
        rerank = details.rerank
        if rerank.availability == "AVAILABLE" and rerank.value is not None:
            return 0.4 * normalized_query_score + 0.6 * rerank.value
        return normalized_query_score

    def _lexical_coverage(self, query: str, hits: Sequence[ScoredHit]) -> float:
        query_terms = _meaningful_features(query)
        if not query_terms:
            return 0.0
        evidence_terms: set[str] = set()
        for hit in hits[: self.config.max_hits_for_coverage]:
            evidence_terms.update(_meaningful_features(hit.chunk.text))
        return len(query_terms & evidence_terms) / len(query_terms)


def _meaningful_features(text: str) -> set[str]:
    return {item for item in lexical_features(text) if len(item) >= 2 or item.isascii()}
