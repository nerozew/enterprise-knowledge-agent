"""Shared immutable value objects for ingestion and retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    TXT = "txt"


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"


class ChunkKind(StrEnum):
    PARENT = "parent"
    CHILD = "child"


class ScoreAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EXPOSED = "NOT_EXPOSED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    query_index: int
    value: float
    rank: int | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class ScoreStage:
    availability: ScoreAvailability
    value: float | None = None
    rank: int | None = None
    method: str | None = None
    rrf_k: int | None = None
    per_query: tuple[ScoreContribution, ...] = ()
    contributions: tuple[ScoreContribution, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoreDetails:
    dense_similarity: ScoreStage
    bm25: ScoreStage
    channel_fusion: ScoreStage
    query_fusion: ScoreStage
    rerank: ScoreStage
    final: ScoreStage


@dataclass(frozen=True, slots=True)
class FusionScoring:
    method: str | None
    applied: bool
    rrf_k: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievalScoring:
    requested_retrieval_mode: str
    effective_retrieval_mode: str
    query_count: int
    channel_fusion: FusionScoring
    query_fusion: FusionScoring
    rerank_applied: bool
    version: int = 1


@dataclass(frozen=True, slots=True)
class SourceBlock:
    kind: BlockKind
    text: str
    page: int | None
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    format: DocumentFormat
    mime_type: str
    blocks: tuple[SourceBlock, ...]
    content_sha256: str
    parser_profile: str = "structure-v1"

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    document_version_id: str
    kind: ChunkKind
    ordinal: int
    text: str
    page: int | None
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int
    source_span_id: str
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk: KnowledgeChunk
    workspace_id: str
    vector: tuple[float, ...]
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredHit:
    chunk: KnowledgeChunk
    score: float
    scores: dict[str, float]
    score_details: ScoreDetails | None = None
    context: tuple[KnowledgeChunk, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: tuple[ScoredHit, ...]
    queries: tuple[str, ...]
    enabled_components: tuple[str, ...]
    provider_mode: str
    scoring: RetrievalScoring | None = None
    degraded: bool = False
    warnings: tuple[str, ...] = ()
