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
    context: tuple[KnowledgeChunk, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: tuple[ScoredHit, ...]
    queries: tuple[str, ...]
    enabled_components: tuple[str, ...]
    provider_mode: str
    degraded: bool = False
    warnings: tuple[str, ...] = ()
