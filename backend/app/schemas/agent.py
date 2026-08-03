from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class ConversationCreate(AgentApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)


class ConversationPatch(AgentApiModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    status: Literal["ACTIVE", "ARCHIVED"] | None = None

    @model_validator(mode="after")
    def has_change(self) -> ConversationPatch:
        if self.title is None and self.status is None:
            raise ValueError("at least one conversation field must change")
        return self


class ConversationSummary(AgentApiModel):
    id: UUID
    workspace_id: UUID
    title: str | None
    status: Literal["ACTIVE", "ARCHIVED"]
    version: int
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationList(AgentApiModel):
    items: list[ConversationSummary]
    page: int
    page_size: int
    total: int


class AgentBudget(AgentApiModel):
    max_tokens: int = Field(default=4096, ge=256, le=100_000)
    max_cost: float | None = Field(default=None, ge=0, le=10_000)


class AgentRunCreate(AgentApiModel):
    conversation_version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=20_000)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=50)
    retrieval_strategy: Literal["AUTO", "BASIC", "LOCAL", "GLOBAL"] = "AUTO"
    budget: AgentBudget = Field(default_factory=AgentBudget)

    @field_validator("knowledge_base_ids")
    @classmethod
    def unique_knowledge_bases(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("knowledge_base_ids must be unique")
        return value


class AgentRunSummary(AgentApiModel):
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    requested_by_user_id: UUID
    status: Literal["CREATED", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"]
    route: Literal[
        "DIRECT_ANSWER",
        "KNOWLEDGE_QA",
        "INSPIRATION_SEARCH",
        "TODO_CREATE",
    ] | None
    reason_code: Literal[
        "CASUAL_OR_GENERAL",
        "WORKSPACE_KNOWLEDGE",
        "INSPIRATION_LOOKUP",
        "ACTION_REQUEST",
        "AMBIGUOUS",
    ] | None
    graph_version: str
    error_code: str | None
    version: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentRunCancel(AgentApiModel):
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class OperationAccepted(AgentApiModel):
    operation_id: UUID
    resource_type: Literal["agent_run"]
    resource_id: UUID
    status: Literal["RUNNING"]
    status_url: str
    events_url: str
    retry_after_seconds: int = 2
    created_at: datetime


class ApprovalSummary(AgentApiModel):
    id: UUID
    workspace_id: UUID
    run_id: UUID
    tool_call_id: UUID
    requested_by_user_id: UUID
    tool_name: str
    arguments: dict[str, Any]
    tool_args_hash: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
    version: int
    expires_at: datetime
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalList(AgentApiModel):
    items: list[ApprovalSummary]
    page: int
    page_size: int
    total: int


class ApprovalDecision(AgentApiModel):
    decision: Literal["APPROVED", "REJECTED"]
    version: int = Field(ge=1)
    tool_args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=1_000)


class ApprovalDecisionResult(AgentApiModel):
    approval_id: UUID
    status: Literal["APPROVED", "REJECTED"]
    decided_at: datetime
    run: AgentRunSummary
    todo_id: UUID | None = None


class TodoSummary(AgentApiModel):
    id: UUID
    workspace_id: UUID
    title: str
    description: str | None
    status: Literal["OPEN", "COMPLETED", "CANCELLED"]
    due_at: datetime | None
    assignee_id: UUID
    source_run_id: UUID
    approval_id: UUID
    version: int
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TodoList(AgentApiModel):
    items: list[TodoSummary]
    page: int
    page_size: int
    total: int


class TodoPatch(AgentApiModel):
    version: int = Field(ge=1)
    status: Literal["COMPLETED", "CANCELLED"]


class AgentMessageSummary(AgentApiModel):
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    run_id: UUID | None
    role: Literal["USER", "ASSISTANT", "SYSTEM"]
    content: str
    sequence_no: int = Field(ge=1)
    created_at: datetime


class AgentRefusal(AgentApiModel):
    code: str
    message: str


class AgentCitationSummary(AgentApiModel):
    id: UUID
    document_id: UUID
    document_version_id: UUID
    document_title: str
    page: int | None
    heading_path: list[str]
    quote: str
    source_span_id: str
    ordinal: int = Field(ge=1)


class InspirationAgentHit(AgentApiModel):
    id: UUID
    title: str | None
    content: str
    summary: str | None
    tag_names: list[str]
    score: float | None


class ToolProposalSummary(AgentApiModel):
    tool_call_id: UUID
    tool_name: Literal["todo_create"]
    arguments: dict[str, Any]
    tool_args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    impact_scope: str
    requires_approval: Literal[True] = True


class AgentRunSnapshot(AgentApiModel):
    run: AgentRunSummary
    messages: list[AgentMessageSummary]
    final_answer: str | None
    refusal: AgentRefusal | None
    citations: list[AgentCitationSummary]
    inspiration_hits: list[InspirationAgentHit]
    tool_proposal: ToolProposalSummary | None
    approval: ApprovalSummary | None
    todo: TodoSummary | None
    last_event_id: str | None
    last_sequence_no: int = Field(ge=0)
