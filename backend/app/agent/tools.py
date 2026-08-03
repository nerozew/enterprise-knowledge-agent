from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.agent.models import (
    InspirationSearchInput,
    KnowledgeSearchInput,
    RuntimeContext,
    ToolResult,
)
from app.schemas.inspirations import InspirationResponse
from app.schemas.knowledge import SearchFilters, SearchRequest, SearchResponse


class KnowledgeServiceBoundary(Protocol):
    async def search(self, payload: SearchRequest) -> SearchResponse: ...


class InspirationServiceBoundary(Protocol):
    async def list(
        self,
        *,
        q: str | None = None,
        tag_ids: list[Any] | None = None,
        tag_match: str = "any",
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Any], int]: ...


KnowledgeServiceFactory = Callable[[RuntimeContext], KnowledgeServiceBoundary]
InspirationServiceFactory = Callable[[RuntimeContext], InspirationServiceBoundary]


class KnowledgeServiceToolAdapter:
    """Read-only adapter around the existing workspace-scoped KnowledgeService."""

    def __init__(self, service_factory: KnowledgeServiceFactory) -> None:
        self._service_factory = service_factory

    async def invoke(
        self, arguments: KnowledgeSearchInput, *, context: RuntimeContext
    ) -> ToolResult:
        service = self._service_factory(context)
        response = await service.search(
            SearchRequest(
                query=arguments.query,
                top_k=arguments.top_k,
                candidate_k=max(30, arguments.top_k),
                response_mode="ANSWER",
                filters=SearchFilters.model_validate(arguments.filters.model_dump()),
            )
        )
        return ToolResult(
            tool_name="knowledge_search",
            data=response.model_dump(mode="json"),
        )


class InspirationServiceToolAdapter:
    """Read-only adapter around the existing workspace-scoped InspirationService."""

    def __init__(self, service_factory: InspirationServiceFactory) -> None:
        self._service_factory = service_factory

    async def invoke(
        self, arguments: InspirationSearchInput, *, context: RuntimeContext
    ) -> ToolResult:
        service = self._service_factory(context)
        items, total = await service.list(
            q=arguments.query,
            tag_ids=arguments.tag_ids,
            tag_match="any",
            include_deleted=False,
            page=1,
            page_size=arguments.limit,
        )
        return ToolResult(
            tool_name="inspiration_search",
            data={
                "items": [
                    InspirationResponse.model_validate(item).model_dump(mode="json")
                    for item in items
                ],
                "total": total,
            },
        )
