from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Literal, cast
from uuid import uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.models import (
    AgentState,
    Citation,
    InspirationSearchInput,
    KnowledgeSearchInput,
    RuntimeContext,
    ToolProposal,
    ToolResult,
)
from app.agent.protocols import (
    DirectAnswerHandler,
    InspirationSearchTool,
    KnowledgeSearchTool,
    StructuredRouter,
    TodoArgumentModel,
)
from app.agent.routing import StructuredOutputError, parse_route_decision, parse_todo_arguments

CLARIFICATION_MESSAGE = "我还不能安全确定你的意图或必要参数，请补充更具体的要求。"
INVALID_OUTPUT_MESSAGE = "Agent 依赖返回了无效结果，已安全终止本次运行。"


def _runtime_context(state: AgentState) -> RuntimeContext:
    return RuntimeContext(
        workspace_id=state.workspace_id,
        actor_id=state.actor_id,
        conversation_id=state.conversation_id,
        run_id=state.run_id,
    )


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _failure_patch() -> dict[str, Any]:
    return {
        "pending_tool_call": None,
        "approval_id": None,
        "retrieval_result": None,
        "citations": [],
        "error_code": "AGENT_OUTPUT_INVALID",
        "final_answer": INVALID_OUTPUT_MESSAGE,
    }


def _fixed_citations(data: dict[str, Any]) -> list[Citation]:
    hits = data.get("hits")
    if not isinstance(hits, list):
        raise ValueError("knowledge tool result must contain a hits list")
    citations: list[Citation] = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            raise ValueError("knowledge hit must be an object")
        required = {"document_version_id", "chunk_id", "source_span_id", "quote"}
        if not required.issubset(hit):
            raise ValueError("knowledge hit is missing a fixed citation field")
        citations.append(Citation.model_validate({key: hit[key] for key in required}))
    return citations


def _validated_tool_result(raw: Any, expected_tool: str) -> ToolResult:
    result = ToolResult.model_validate(raw)
    if result.tool_name != expected_tool:
        raise ValueError("tool result name does not match the selected tool")
    return result


def _validated_terminal_state(state: AgentState) -> AgentState:
    """Rebuild the complete state and enforce terminal cross-field invariants."""

    terminal = AgentState.model_validate(state.model_dump(mode="python"), strict=True)
    if not terminal.final_answer or not terminal.final_answer.strip():
        raise ValueError("terminal state requires a non-empty answer")
    if terminal.error_code == "AGENT_OUTPUT_INVALID":
        return terminal
    if terminal.route is None:
        if terminal.error_code != "ROUTE_CLARIFICATION_REQUIRED":
            raise ValueError("terminal state without a route must require clarification")
        return terminal
    if terminal.route.route == "KNOWLEDGE_QA":
        if terminal.retrieval_result is None:
            raise ValueError("knowledge route requires a retrieval result")
        _validated_tool_result(terminal.retrieval_result, "knowledge_search")
    elif terminal.route.route == "INSPIRATION_SEARCH":
        if terminal.retrieval_result is None:
            raise ValueError("inspiration route requires a retrieval result")
        _validated_tool_result(terminal.retrieval_result, "inspiration_search")
    elif terminal.route.route == "TODO_CREATE":
        if terminal.error_code == "APPROVAL_REQUIRED" and terminal.pending_tool_call is None:
            raise ValueError("approval-required state must contain a tool proposal")
        if terminal.error_code == "TODO_ARGUMENTS_REQUIRED" and terminal.pending_tool_call:
            raise ValueError("invalid todo arguments cannot contain a tool proposal")
    return terminal


@dataclass(frozen=True, slots=True)
class AgentKernel:
    router: StructuredRouter
    direct_answer: DirectAnswerHandler
    knowledge_tool: KnowledgeSearchTool
    inspiration_tool: InspirationSearchTool
    todo_arguments: TodoArgumentModel

    def compile(self) -> CompiledStateGraph:
        return build_agent_graph(self)


def build_agent_graph(kernel: AgentKernel) -> CompiledStateGraph:
    graph = StateGraph(AgentState)

    async def classify_route(state: AgentState) -> dict[str, Any]:
        if state.route is not None:
            return {"route": state.route, "error_code": None, "final_answer": None}
        try:
            raw = await kernel.router.decide(
                messages=state.messages,
                query=state.latest_user_query,
            )
            decision = parse_route_decision(raw)
        except (StructuredOutputError, ValueError, TypeError):
            return {
                "route": None,
                "error_code": "ROUTE_CLARIFICATION_REQUIRED",
                "final_answer": CLARIFICATION_MESSAGE,
            }
        return {"route": decision, "error_code": None, "final_answer": None}

    def select_route(
        state: AgentState,
    ) -> Literal[
        "clarify", "direct_answer", "knowledge_search", "inspiration_search", "todo_proposal"
    ]:
        if state.route is None:
            return "clarify"
        return cast(
            Any,
            {
                "DIRECT_ANSWER": "direct_answer",
                "KNOWLEDGE_QA": "knowledge_search",
                "INSPIRATION_SEARCH": "inspiration_search",
                "TODO_CREATE": "todo_proposal",
            }[state.route.route],
        )

    async def direct_answer(state: AgentState) -> dict[str, Any]:
        answer = await kernel.direct_answer.answer(
            messages=state.messages,
            query=state.route.normalized_query if state.route else state.latest_user_query,
            context=_runtime_context(state),
        )
        if not isinstance(answer, str) or not answer.strip():
            return _failure_patch()
        return {"final_answer": answer.strip()}

    async def knowledge_search(state: AgentState) -> dict[str, Any]:
        assert state.route is not None
        raw_result = await kernel.knowledge_tool.invoke(
            KnowledgeSearchInput(query=state.route.normalized_query),
            context=_runtime_context(state),
        )
        try:
            result = _validated_tool_result(raw_result, "knowledge_search")
            data = result.data
            citations = _fixed_citations(data)
            answer = data.get("answer")
            refusal = data.get("refusal")
            if isinstance(answer, str) and answer.strip():
                final_answer = answer.strip()
            elif (
                isinstance(refusal, Mapping)
                and isinstance(refusal.get("message"), str)
                and refusal["message"].strip()
            ):
                final_answer = refusal["message"].strip()
            else:
                raise ValueError("knowledge result requires an answer or refusal")
        except (TypeError, ValueError):
            return _failure_patch()
        return {
            "retrieval_result": result,
            "citations": citations,
            "final_answer": final_answer,
        }

    async def inspiration_search(state: AgentState) -> dict[str, Any]:
        assert state.route is not None
        raw_result = await kernel.inspiration_tool.invoke(
            InspirationSearchInput(query=state.route.normalized_query),
            context=_runtime_context(state),
        )
        try:
            result = _validated_tool_result(raw_result, "inspiration_search")
            items = result.data.get("items")
            total = result.data.get("total")
            if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
                raise ValueError("inspiration result items must be objects")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise ValueError("inspiration result total must be a non-negative integer")
        except (TypeError, ValueError):
            return _failure_patch()
        return {"retrieval_result": result, "final_answer": "灵感检索已完成。"}

    async def todo_proposal(state: AgentState) -> dict[str, Any]:
        assert state.route is not None
        try:
            raw = await kernel.todo_arguments.extract(
                messages=state.messages,
                query=state.route.normalized_query,
            )
            arguments = parse_todo_arguments(raw)
        except (StructuredOutputError, ValueError, TypeError):
            return {
                "pending_tool_call": None,
                "error_code": "TODO_ARGUMENTS_REQUIRED",
                "final_answer": "创建待办所需参数不足，请至少提供明确的标题。",
            }
        arguments = arguments.model_copy(
            update={
                "assignee_id": arguments.assignee_id or state.actor_id,
                "due_at": (
                    arguments.due_at.astimezone(UTC) if arguments.due_at is not None else None
                ),
            }
        )
        arguments_hash = _canonical_hash(arguments.model_dump(mode="json"))
        proposal = ToolProposal(
            tool_call_id=uuid5(state.run_id, f"todo_create:{arguments_hash}"),
            arguments=arguments,
            arguments_hash=arguments_hash,
        )
        return {
            "pending_tool_call": proposal,
            "error_code": "APPROVAL_REQUIRED",
            "final_answer": "待办参数已生成，必须经过人工审批后才能执行。",
        }

    def finalize(state: AgentState) -> dict[str, Any]:
        try:
            terminal = _validated_terminal_state(state)
        except (TypeError, ValueError):
            terminal = AgentState.model_validate(
                state.model_dump(mode="python") | _failure_patch(), strict=True
            )
        return {field_name: getattr(terminal, field_name) for field_name in AgentState.model_fields}

    graph.add_node("classify_route", classify_route)
    graph.add_node("direct_answer", direct_answer)
    graph.add_node("knowledge_search", knowledge_search)
    graph.add_node("inspiration_search", inspiration_search)
    graph.add_node("todo_proposal", todo_proposal)
    graph.add_node("clarify", lambda _state: {})
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "classify_route")
    graph.add_conditional_edges(
        "classify_route",
        select_route,
        {
            "clarify": "clarify",
            "direct_answer": "direct_answer",
            "knowledge_search": "knowledge_search",
            "inspiration_search": "inspiration_search",
            "todo_proposal": "todo_proposal",
        },
    )
    for node in (
        "clarify",
        "direct_answer",
        "knowledge_search",
        "inspiration_search",
        "todo_proposal",
    ):
        graph.add_edge(node, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
