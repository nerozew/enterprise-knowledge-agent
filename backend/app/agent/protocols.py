from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.agent.models import (
    AgentMessage,
    InspirationSearchInput,
    KnowledgeSearchInput,
    RouteDecision,
    RuntimeContext,
    TodoCreateInput,
    ToolResult,
)

StructuredOutput = RouteDecision | Mapping[str, Any] | str
TodoStructuredOutput = TodoCreateInput | Mapping[str, Any] | str


class StructuredRouter(Protocol):
    async def decide(self, *, messages: Sequence[AgentMessage], query: str) -> StructuredOutput: ...


class DirectAnswerHandler(Protocol):
    async def answer(
        self,
        *,
        messages: Sequence[AgentMessage],
        query: str,
        context: RuntimeContext,
    ) -> str: ...


class TodoArgumentModel(Protocol):
    async def extract(
        self,
        *,
        messages: Sequence[AgentMessage],
        query: str,
    ) -> TodoStructuredOutput: ...


class KnowledgeSearchTool(Protocol):
    async def invoke(
        self, arguments: KnowledgeSearchInput, *, context: RuntimeContext
    ) -> ToolResult: ...


class InspirationSearchTool(Protocol):
    async def invoke(
        self, arguments: InspirationSearchInput, *, context: RuntimeContext
    ) -> ToolResult: ...
