"""Shared immutable value objects for ingestion and retrieval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    MARKDOWN = "markdown"
    TXT = "txt"


class BlockKind(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    IMAGE_OCR = "image_ocr"


class OCRMode(StrEnum):
    AUTO = "AUTO"
    FORCE = "FORCE"
    OFF = "OFF"


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
    block_key: str | None = None
    slide: int | None = None
    bbox: NormalizedBBox | None = None
    reading_order: int | None = None
    ocr_used: bool = False
    ocr_confidence: float | None = None
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedBBox:
    """Page-relative bounding box whose coordinates are normalized to ``0..1``."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if not (
            0.0 <= self.left <= self.right <= 1.0
            and 0.0 <= self.top <= self.bottom <= 1.0
        ):
            raise ValueError("bounding box coordinates must be normalized to 0..1")

    def to_dict(self) -> dict[str, float]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }


@dataclass(frozen=True, slots=True)
class LayoutBlock:
    """A stable, traceable block in the structure-v2 intermediate representation."""

    block_key: str
    kind: BlockKind
    text: str
    page_no: int | None
    slide_no: int | None
    bbox: NormalizedBBox | None
    reading_order: int
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int
    ocr_used: bool
    ocr_confidence: float | None
    content_sha256: str

    def __post_init__(self) -> None:
        if self.page_no is not None and self.page_no < 1:
            raise ValueError("page_no must be positive")
        if self.slide_no is not None and self.slide_no < 1:
            raise ValueError("slide_no must be positive")
        if self.page_no is not None and self.slide_no is not None:
            raise ValueError("a layout block cannot have both page_no and slide_no")
        if self.reading_order < 0 or self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError("invalid layout block offsets")
        if self.ocr_confidence is not None and not 0.0 <= self.ocr_confidence <= 1.0:
            raise ValueError("ocr_confidence must be normalized to 0..1")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match block text")

    def to_source_block(self) -> SourceBlock:
        return SourceBlock(
            kind=self.kind,
            text=self.text,
            page=self.page_no,
            heading_path=self.heading_path,
            char_start=self.char_start,
            char_end=self.char_end,
            block_key=self.block_key,
            slide=self.slide_no,
            bbox=self.bbox,
            reading_order=self.reading_order,
            ocr_used=self.ocr_used,
            ocr_confidence=self.ocr_confidence,
            content_sha256=self.content_sha256,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_key": self.block_key,
            "kind": self.kind.value,
            "text": self.text,
            "page_no": self.page_no,
            "slide_no": self.slide_no,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "reading_order": self.reading_order,
            "heading_path": list(self.heading_path),
            "char_start": self.char_start,
            "char_end": self.char_end,
            "ocr_used": self.ocr_used,
            "ocr_confidence": self.ocr_confidence,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class LayoutDocumentV2:
    """Serializable structure-v2 document used as the canonical parsing artifact."""

    format: DocumentFormat
    mime_type: str
    source_sha256: str
    blocks: tuple[LayoutBlock, ...]
    page_count: int | None = None
    slide_count: int | None = None
    parser_profile: str = "layout-v2"
    ocr_profile: str | None = None
    warnings: tuple[str, ...] = ()
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("LayoutDocumentV2 requires schema_version=2")
        if self.page_count is not None and self.page_count < 0:
            raise ValueError("page_count cannot be negative")
        if self.slide_count is not None and self.slide_count < 0:
            raise ValueError("slide_count cannot be negative")

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)

    @property
    def ocr_page_count(self) -> int:
        """Count distinct PDF pages containing OCR-derived blocks."""

        return len(
            {
                block.page_no
                for block in self.blocks
                if block.ocr_used and block.page_no is not None
            }
        )

    def to_parsed_document(self) -> ParsedDocument:
        return ParsedDocument(
            format=self.format,
            mime_type=self.mime_type,
            blocks=tuple(block.to_source_block() for block in self.blocks),
            content_sha256=self.source_sha256,
            parser_profile=self.parser_profile,
            layout=self,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "format": self.format.value,
            "mime_type": self.mime_type,
            "source_sha256": self.source_sha256,
            "page_count": self.page_count,
            "slide_count": self.slide_count,
            "ocr_page_count": self.ocr_page_count,
            "parser_profile": self.parser_profile,
            "ocr_profile": self.ocr_profile,
            "warnings": list(self.warnings),
            "blocks": [block.to_dict() for block in self.blocks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    format: DocumentFormat
    mime_type: str
    blocks: tuple[SourceBlock, ...]
    content_sha256: str
    parser_profile: str = "structure-v1"
    layout: LayoutDocumentV2 | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)

    @property
    def title(self) -> str | None:
        return next(
            (
                block.text
                for block in self.blocks
                if block.kind in {BlockKind.TITLE, BlockKind.HEADING}
            ),
            None,
        )

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.layout.warnings if self.layout else ()

    @property
    def page_count(self) -> int | None:
        return self.layout.page_count if self.layout else None

    @property
    def slide_count(self) -> int | None:
        return self.layout.slide_count if self.layout else None

    @property
    def ocr_page_count(self) -> int:
        return len({block.page for block in self.blocks if block.ocr_used and block.page})

    @property
    def ocr_profile(self) -> str | None:
        return self.layout.ocr_profile if self.layout else None

    def to_manifest_dict(self) -> dict[str, Any]:
        if self.layout:
            return self.layout.to_dict()
        return {
            "schema_version": 1,
            "format": self.format.value,
            "mime_type": self.mime_type,
            "source_sha256": self.content_sha256,
            "parser_profile": self.parser_profile,
            "page_count": self.page_count,
            "slide_count": self.slide_count,
            "ocr_page_count": self.ocr_page_count,
            "ocr_profile": self.ocr_profile,
            "warnings": list(self.warnings),
            "blocks": [
                {
                    "kind": block.kind.value,
                    "text": block.text,
                    "page_no": block.page,
                    "slide_no": block.slide,
                    "heading_path": list(block.heading_path),
                    "char_start": block.char_start,
                    "char_end": block.char_end,
                }
                for block in self.blocks
            ],
        }


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
