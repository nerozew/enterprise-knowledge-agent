from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import APIModel, Page

DocumentFormat = Literal["PDF", "DOCX", "PPTX", "MARKDOWN", "TXT"]
OCRMode = Literal["AUTO", "FORCE", "OFF"]
RetrievalMode = Literal["DENSE", "SPARSE", "HYBRID"]


class DocumentSummary(APIModel):
    id: UUID
    workspace_id: UUID
    title: str
    status: Literal["ACTIVE", "DELETED"]
    version: int
    published_version_id: UUID | None
    created_at: datetime
    updated_at: datetime


class DocumentList(Page[DocumentSummary]):
    pass


class DocumentPatch(APIModel):
    title: str = Field(min_length=1, max_length=240)
    version: int = Field(ge=1)


class DocumentVersionSummary(APIModel):
    id: UUID
    document_id: UUID
    format: DocumentFormat
    mime_type: str
    file_name: str
    file_size: int
    file_sha256: str
    status: Literal["PUBLISHING", "PUBLISHED", "SUPERSEDED", "FAILED"]
    parser_profile: str
    chunk_profile: str
    index_profile: str
    structure_schema_version: int = Field(ge=1)
    layout_profile: str | None = None
    ocr_profile: str | None = None
    page_count: int | None = Field(default=None, ge=0)
    slide_count: int | None = Field(default=None, ge=0)
    ocr_page_count: int = Field(default=0, ge=0)
    parse_warnings: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime
    published_at: datetime | None


class IngestionPages(APIModel):
    total: int = Field(default=0, ge=0)
    ocr_required: int = Field(default=0, ge=0)
    ocr_completed: int = Field(default=0, ge=0)


class IngestionJobSummary(APIModel):
    id: UUID
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    status: Literal[
        "QUEUED",
        "PARSING",
        "CHUNKING",
        "STORING",
        "INDEXING",
        "SUCCEEDED",
        "RETRY_WAIT",
        "COMPENSATING",
        "FAILED",
        "CANCELLED",
    ]
    stage: str
    stage_detail: str | None = None
    pages: IngestionPages = Field(default_factory=IngestionPages)
    warnings: list[dict[str, object]] = Field(default_factory=list)
    progress: int = Field(ge=0, le=100)
    attempt_count: int = Field(ge=0)
    retryable: bool
    error_code: str | None
    error_message: str | None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class UploadAccepted(APIModel):
    document: DocumentSummary
    version: DocumentVersionSummary
    job: IngestionJobSummary
    reused: bool = False


class ScoreBreakdown(APIModel):
    dense: float | None = None
    sparse: float | None = None
    fusion: float
    rerank: float | None = None


ScoreAvailability = Literal[
    "AVAILABLE",
    "NOT_APPLICABLE",
    "NOT_EXPOSED",
    "NOT_RUN",
]


class ScoreContribution(APIModel):
    query_index: int = Field(ge=0)
    value: float
    rank: int | None = Field(default=None, ge=1)
    source: str | None = None


class ScoreStage(APIModel):
    availability: ScoreAvailability
    value: float | None = None
    rank: int | None = Field(default=None, ge=1)
    method: str | None = None
    rrf_k: int | None = Field(default=None, ge=1)
    per_query: list[ScoreContribution] = Field(default_factory=list)
    contributions: list[ScoreContribution] = Field(default_factory=list)

    @model_validator(mode="after")
    def availability_matches_value(self) -> ScoreStage:
        if self.availability == "AVAILABLE" and self.value is None:
            raise ValueError("available score stages require a value")
        if self.availability != "AVAILABLE" and self.value is not None:
            raise ValueError("unavailable score stages cannot expose a value")
        return self


class ScoreDetails(APIModel):
    dense_similarity: ScoreStage
    bm25: ScoreStage
    channel_fusion: ScoreStage
    query_fusion: ScoreStage
    rerank: ScoreStage
    final: ScoreStage


class SourceSpanSummary(APIModel):
    source_span_id: str
    page_no: int | None = Field(default=None, ge=1)
    slide_no: int | None = Field(default=None, ge=1)
    bbox: dict[str, float] | None = None
    excerpt: str
    excerpt_sha256: str


class SourceSpanResolved(SourceSpanSummary):
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    block_ref: str
    block_char_start: int = Field(ge=0)
    block_char_end: int = Field(ge=1)
    source_sha256: str


class SearchHit(APIModel):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    page: int | None
    heading_path: list[str]
    quote: str
    source_span_id: str
    source_spans: list[SourceSpanSummary] = Field(default_factory=list)
    score: float
    scores: ScoreBreakdown
    score_details: ScoreDetails
    context: str


class SearchFilters(APIModel):
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_formats: list[DocumentFormat] = Field(default_factory=list, max_length=5)
    published_after: datetime | None = None
    published_before: datetime | None = None

    @model_validator(mode="after")
    def valid_dates(self) -> SearchFilters:
        if (
            self.published_after
            and self.published_before
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after 不能晚于 published_before")
        return self


class JobVersionRequest(APIModel):
    version: int = Field(ge=1)


class SearchRequest(APIModel):
    query: str = Field(min_length=1, max_length=1000)
    retrieval_mode: RetrievalMode = "HYBRID"
    top_k: int = Field(default=5, ge=1, le=20)
    candidate_k: int = Field(default=30, ge=1, le=100)
    fusion: Literal["RRF", "WEIGHTED"] = "RRF"
    rerank: Literal["OFF", "AUTO", "ON"] = "AUTO"
    rewrite: Literal["OFF", "AUTO", "ON"] = "AUTO"
    multi_query: bool = False
    expand_context: bool = True
    response_mode: Literal["HITS", "ANSWER"] = "HITS"
    dense_weight: float = Field(default=0.65, ge=0, le=1)
    include_query_trace: bool = False
    filters: SearchFilters = Field(default_factory=SearchFilters)

    @model_validator(mode="after")
    def valid_candidate_count(self) -> SearchRequest:
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k 必须大于等于 top_k")
        return self


class ProviderMetadata(APIModel):
    mode: Literal["REMOTE", "LOCAL", "FIXTURE", "DISABLED"]
    embedding: str
    reranker: str
    llm: str
    profile: str


class Refusal(APIModel):
    code: str
    message: str


class FusionScoring(APIModel):
    method: str | None = None
    applied: bool
    rrf_k: int | None = Field(default=None, ge=1)


class SearchScoring(APIModel):
    version: Literal[1] = 1
    requested_retrieval_mode: RetrievalMode
    effective_retrieval_mode: RetrievalMode
    query_count: int = Field(ge=1)
    channel_fusion: FusionScoring
    query_fusion: FusionScoring
    rerank_applied: bool


class QueryTraceItem(APIModel):
    index: int = Field(ge=0)
    kind: Literal["ORIGINAL", "REWRITE"]
    text: str


class QueryTrace(APIModel):
    exposed: bool
    items: list[QueryTraceItem] = Field(default_factory=list)
    redaction_reason: str | None = None


class Answerability(APIModel):
    status: Literal["SUPPORTED", "REJECTED", "NOT_EVALUATED"]
    method: Literal["GROUNDED_ANSWER_LLM", "HEURISTIC_GATE", "NOT_RUN"]
    reason_code: str
    precheck_code: str
    provider_called: bool
    raw_top_score: float
    gate_score: float
    score_threshold: float = Field(ge=0)
    lexical_coverage: float = Field(ge=0, le=1)
    lexical_threshold: float = Field(ge=0, le=1)
    lexical_passed: bool
    checked_hit_ids: list[UUID] = Field(default_factory=list)
    supporting_hit_ids: list[UUID] = Field(default_factory=list)


class SearchResponse(APIModel):
    query: str
    response_mode: Literal["HITS", "ANSWER"]
    answer: str | None = None
    refusal: Refusal | None = None
    hits: list[SearchHit]
    provider: ProviderMetadata
    scoring: SearchScoring
    query_trace: QueryTrace
    answerability: Answerability
    components: list[str]
    timings_ms: dict[str, float]
    warnings: list[str]
    degraded: bool
