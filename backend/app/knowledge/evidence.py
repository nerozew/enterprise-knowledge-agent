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
    lexical_coverage: float
    usable_hit_ids: tuple[str, ...]


class EvidenceGate:
    """Reject weak or untraceable hits with explicit, reproducible reasons."""

    def __init__(self, config: EvidenceGateConfig | None = None) -> None:
        self.config = config or EvidenceGateConfig()

    def evaluate(self, query: str, hits: Sequence[ScoredHit]) -> EvidenceDecision:
        traceable = tuple(
            hit for hit in hits if hit.chunk.document_version_id and hit.chunk.source_span_id
        )
        top_score = max((hit.score for hit in traceable), default=0.0)
        coverage = self._lexical_coverage(query, traceable)
        usable = tuple(hit.chunk.chunk_id for hit in traceable)
        if len(traceable) < self.config.min_hits:
            return EvidenceDecision(False, "no_traceable_evidence", top_score, coverage, usable)
        if top_score < self.config.min_top_score:
            return EvidenceDecision(False, "score_below_threshold", top_score, coverage, usable)
        if coverage < self.config.min_lexical_coverage:
            return EvidenceDecision(
                False,
                "lexical_coverage_below_threshold",
                top_score,
                coverage,
                usable,
            )
        return EvidenceDecision(True, "accepted", top_score, coverage, usable)

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
