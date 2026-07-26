from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import APIModel, Page

DocumentFormat = Literal["PDF", "DOCX", "MARKDOWN", "TXT"]
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
    created_at: datetime
    published_at: datetime | None


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


class SearchHit(APIModel):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    page: int | None
    heading_path: list[str]
    quote: str
    source_span_id: str
    score: float
    scores: ScoreBreakdown
    context: str


class SearchFilters(APIModel):
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_formats: list[DocumentFormat] = Field(default_factory=list, max_length=4)
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


class SearchResponse(APIModel):
    query: str
    response_mode: Literal["HITS", "ANSWER"]
    answer: str | None = None
    refusal: Refusal | None = None
    hits: list[SearchHit]
    provider: ProviderMetadata
    components: list[str]
    timings_ms: dict[str, float]
    warnings: list[str]
    degraded: bool
