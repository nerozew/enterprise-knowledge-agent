from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AGENT_GRAPH_VERSION: Final = "p8-agent-mvp-v1-b7"

RouteName = Literal[
    "DIRECT_ANSWER",
    "KNOWLEDGE_QA",
    "INSPIRATION_SEARCH",
    "TODO_CREATE",
]
ReasonCode = Literal[
    "CASUAL_OR_GENERAL",
    "WORKSPACE_KNOWLEDGE",
    "INSPIRATION_LOOKUP",
    "ACTION_REQUEST",
    "AMBIGUOUS",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class RuntimeContext(StrictModel):
    """Trusted values supplied by the authenticated application boundary."""

    workspace_id: UUID
    actor_id: UUID
    conversation_id: UUID
    run_id: UUID


class RouteDecision(StrictModel):
    route: RouteName
    confidence: float = Field(ge=0, le=1)
    reason_code: ReasonCode
    normalized_query: str = Field(min_length=1, max_length=1_000)
    needs_clarification: bool = False

    @model_validator(mode="after")
    def route_matches_reason(self) -> RouteDecision:
        expected = {
            "DIRECT_ANSWER": {"CASUAL_OR_GENERAL", "AMBIGUOUS"},
            "KNOWLEDGE_QA": {"WORKSPACE_KNOWLEDGE", "AMBIGUOUS"},
            "INSPIRATION_SEARCH": {"INSPIRATION_LOOKUP", "AMBIGUOUS"},
            "TODO_CREATE": {"ACTION_REQUEST", "AMBIGUOUS"},
        }
        if self.reason_code not in expected[self.route]:
            raise ValueError("reason_code does not match route")
        return self


class KnowledgeFilters(StrictModel):
    """Allowlisted business filters; never accepts raw SQL or Milvus expressions."""

    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_formats: list[Literal["PDF", "DOCX", "MARKDOWN", "TXT"]] = Field(
        default_factory=list, max_length=4
    )
    published_after: datetime | None = None
    published_before: datetime | None = None

    @model_validator(mode="after")
    def valid_dates(self) -> KnowledgeFilters:
        for value in (self.published_after, self.published_before):
            if value is not None and value.tzinfo is None:
                raise ValueError("published date filters must include a timezone")
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not be later than published_before")
        return self


class KnowledgeSearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: KnowledgeFilters = Field(default_factory=KnowledgeFilters)


class InspirationSearchInput(StrictModel):
    query: str | None = Field(default=None, max_length=1_000)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tag_ids")
    @classmethod
    def unique_tags(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("tag_ids must be unique")
        return value


class TodoCreateInput(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4_000)
    due_at: datetime | None = None
    assignee_id: UUID | None = None

    @field_validator("due_at")
    @classmethod
    def due_at_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("due_at must include a timezone")
        return value


class Citation(StrictModel):
    document_version_id: UUID
    chunk_id: UUID
    source_span_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(max_length=1_000)


class ToolResult(StrictModel):
    tool_name: Literal["knowledge_search", "inspiration_search"]
    data: dict[str, Any]


class ToolProposal(StrictModel):
    tool_name: Literal["todo_create"] = "todo_create"
    tool_call_id: UUID
    arguments: TodoCreateInput
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requires_approval: Literal[True] = True
    status: Literal["REQUIRES_APPROVAL"] = "REQUIRES_APPROVAL"


class AgentState(StrictModel):
    messages: list[AgentMessage] = Field(min_length=1, max_length=100)
    workspace_id: UUID
    conversation_id: UUID
    run_id: UUID
    actor_id: UUID
    graph_version: Literal["p8-agent-mvp-v1-b7"] = AGENT_GRAPH_VERSION
    route: RouteDecision | None = None
    pending_tool_call: ToolProposal | None = None
    approval_id: UUID | None = None
    retrieval_result: ToolResult | None = None
    citations: list[Citation] = Field(default_factory=list)
    final_answer: str | None = Field(default=None, min_length=1, max_length=20_000)
    error_code: str | None = None

    @property
    def latest_user_query(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                return message.content
        raise ValueError("at least one user message is required")
