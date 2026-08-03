from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from app.agent.graph import CLARIFICATION_MESSAGE, AgentKernel
from app.agent.models import (
    AGENT_GRAPH_VERSION,
    AgentMessage,
    AgentState,
    InspirationSearchInput,
    KnowledgeSearchInput,
    RuntimeContext,
    TodoCreateInput,
    ToolResult,
)


class FakeRouter:
    def __init__(self, output: Any) -> None:
        self.output = output

    async def decide(self, **_kwargs: Any) -> Any:
        return self.output


_DEFAULT = object()


class FakeDirectAnswer:
    def __init__(self, output: Any = _DEFAULT) -> None:
        self.output = output

    async def answer(self, **kwargs: Any) -> str:
        context: RuntimeContext = kwargs["context"]
        if self.output is not _DEFAULT:
            return self.output
        return f"direct:{context.workspace_id}"


@dataclass
class FakeKnowledgeTool:
    calls: list[tuple[KnowledgeSearchInput, RuntimeContext]] = field(default_factory=list)
    output: Any = None

    async def invoke(self, arguments: KnowledgeSearchInput, *, context: RuntimeContext) -> Any:
        self.calls.append((arguments, context))
        if self.output is not None:
            return self.output
        return ToolResult(
            tool_name="knowledge_search",
            data={"answer": "grounded answer", "hits": []},
        )


@dataclass
class FakeInspirationTool:
    calls: list[tuple[InspirationSearchInput, RuntimeContext]] = field(default_factory=list)
    output: Any = None

    async def invoke(self, arguments: InspirationSearchInput, *, context: RuntimeContext) -> Any:
        self.calls.append((arguments, context))
        if self.output is not None:
            return self.output
        return ToolResult(
            tool_name="inspiration_search",
            data={"items": [], "total": 0},
        )


class FakeTodoArguments:
    def __init__(self, output: Any = None) -> None:
        self.output = output or {"title": "提交周报"}
        self.calls = 0

    async def extract(self, **_kwargs: Any) -> Any:
        self.calls += 1
        return self.output


def make_state() -> AgentState:
    return AgentState(
        messages=[AgentMessage(role="user", content="测试请求")],
        workspace_id=uuid4(),
        actor_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
    )


def route(route_name: str) -> dict[str, Any]:
    reason = {
        "DIRECT_ANSWER": "CASUAL_OR_GENERAL",
        "KNOWLEDGE_QA": "WORKSPACE_KNOWLEDGE",
        "INSPIRATION_SEARCH": "INSPIRATION_LOOKUP",
        "TODO_CREATE": "ACTION_REQUEST",
    }[route_name]
    return {
        "route": route_name,
        "confidence": 0.98,
        "reason_code": reason,
        "normalized_query": "规范化请求",
        "needs_clarification": False,
    }


def make_kernel(
    router_output: Any,
    *,
    todo_output: Any = None,
    direct_output: Any = _DEFAULT,
    knowledge_output: Any = None,
    inspiration_output: Any = None,
) -> tuple[AgentKernel, FakeKnowledgeTool, FakeInspirationTool, FakeTodoArguments]:
    knowledge = FakeKnowledgeTool(output=knowledge_output)
    inspiration = FakeInspirationTool(output=inspiration_output)
    todo = FakeTodoArguments(todo_output)
    kernel = AgentKernel(
        router=FakeRouter(router_output),
        direct_answer=FakeDirectAnswer(direct_output),
        knowledge_tool=knowledge,
        inspiration_tool=inspiration,
        todo_arguments=todo,
    )
    return kernel, knowledge, inspiration, todo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "expected_answer", "expected_tool"),
    [
        ("DIRECT_ANSWER", "direct:", None),
        ("KNOWLEDGE_QA", "grounded answer", "knowledge_search"),
        ("INSPIRATION_SEARCH", "灵感检索已完成。", "inspiration_search"),
        ("TODO_CREATE", "待办参数已生成", "todo_create"),
    ],
)
async def test_graph_exposes_all_four_routes(
    route_name: str, expected_answer: str, expected_tool: str | None
) -> None:
    state = make_state()
    kernel, knowledge, inspiration, _todo = make_kernel(route(route_name))

    result = await kernel.compile().ainvoke(state)

    assert expected_answer in result["final_answer"]
    if expected_tool == "knowledge_search":
        assert len(knowledge.calls) == 1
        assert not inspiration.calls
    elif expected_tool == "inspiration_search":
        assert len(inspiration.calls) == 1
        assert not knowledge.calls
    elif expected_tool == "todo_create":
        proposal = result["pending_tool_call"]
        assert proposal.tool_name == "todo_create"
        assert proposal.status == "REQUIRES_APPROVAL"
        assert proposal.requires_approval is True
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert not knowledge.calls and not inspiration.calls
    else:
        assert not knowledge.calls and not inspiration.calls
    assert result["graph_version"] == AGENT_GRAPH_VERSION


@pytest.mark.asyncio
async def test_runtime_identity_is_injected_and_not_part_of_tool_arguments() -> None:
    state = make_state()
    kernel, knowledge, _inspiration, _todo = make_kernel(route("KNOWLEDGE_QA"))

    await kernel.compile().ainvoke(state)

    arguments, context = knowledge.calls[0]
    assert context.workspace_id == state.workspace_id
    assert context.actor_id == state.actor_id
    assert "workspace_id" not in arguments.model_dump()
    assert "actor_id" not in arguments.model_dump()


@pytest.mark.asyncio
async def test_model_cannot_override_runtime_identity() -> None:
    state = make_state()
    malicious = route("KNOWLEDGE_QA") | {
        "workspace_id": str(uuid4()),
        "actor_id": str(uuid4()),
    }
    kernel, knowledge, _inspiration, _todo = make_kernel(malicious)

    result = await kernel.compile().ainvoke(state)

    assert result["error_code"] == "ROUTE_CLARIFICATION_REQUIRED"
    assert result["final_answer"] == CLARIFICATION_MESSAGE
    assert not knowledge.calls
    assert result["workspace_id"] == state.workspace_id
    assert result["actor_id"] == state.actor_id


@pytest.mark.asyncio
async def test_todo_route_only_creates_proposal_without_write_side_effect() -> None:
    kernel, knowledge, inspiration, todo = make_kernel(
        route("TODO_CREATE"),
        todo_output=TodoCreateInput(title="创建安全待办"),
    )

    result = await kernel.compile().ainvoke(make_state())

    assert todo.calls == 1
    assert result["pending_tool_call"].arguments.title == "创建安全待办"
    assert len(result["pending_tool_call"].arguments_hash) == 64
    assert result.get("approval_id") is None
    assert not knowledge.calls and not inspiration.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_output",
    [
        "not-json",
        {"route": "UNKNOWN", "confidence": 1.0},
        route("KNOWLEDGE_QA") | {"confidence": 0.20},
        route("KNOWLEDGE_QA") | {"needs_clarification": True},
    ],
)
async def test_invalid_or_unsafe_route_output_fails_closed(invalid_output: Any) -> None:
    kernel, knowledge, inspiration, todo = make_kernel(invalid_output)

    result = await kernel.compile().ainvoke(make_state())

    assert result["error_code"] == "ROUTE_CLARIFICATION_REQUIRED"
    assert result["final_answer"] == CLARIFICATION_MESSAGE
    assert not knowledge.calls and not inspiration.calls
    assert todo.calls == 0


@pytest.mark.asyncio
async def test_missing_todo_title_requests_clarification() -> None:
    kernel, _knowledge, _inspiration, _todo = make_kernel(
        route("TODO_CREATE"), todo_output={"description": "没有标题"}
    )

    result = await kernel.compile().ainvoke(make_state())

    assert result["pending_tool_call"] is None
    assert result["error_code"] == "TODO_ARGUMENTS_REQUIRED"
    assert "参数不足" in result["final_answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_answer", [None, "", "   ", 123, {"answer": "wrong type"}])
async def test_direct_handler_invalid_type_or_empty_value_fails_closed(
    invalid_answer: Any,
) -> None:
    kernel, _knowledge, _inspiration, _todo = make_kernel(
        route("DIRECT_ANSWER"), direct_output=invalid_answer
    )

    result = await kernel.compile().ainvoke(make_state())

    assert result["error_code"] == "AGENT_OUTPUT_INVALID"
    assert "安全终止" in result["final_answer"]
    assert result["retrieval_result"] is None
    assert result["citations"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_result",
    [
        123,
        {"tool_name": "inspiration_search", "data": {"answer": "wrong tool", "hits": []}},
        {"tool_name": "knowledge_search", "data": "not an object"},
        {"tool_name": "knowledge_search", "data": {"answer": "missing hits"}},
        {
            "tool_name": "knowledge_search",
            "data": {"answer": "bad citation", "hits": [{"chunk_id": str(uuid4())}]},
        },
    ],
)
async def test_malformed_knowledge_tool_result_fails_closed(malformed_result: Any) -> None:
    kernel, _knowledge, _inspiration, _todo = make_kernel(
        route("KNOWLEDGE_QA"), knowledge_output=malformed_result
    )

    result = await kernel.compile().ainvoke(make_state())

    assert result["error_code"] == "AGENT_OUTPUT_INVALID"
    assert result["retrieval_result"] is None
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_malformed_inspiration_tool_result_fails_closed() -> None:
    kernel, _knowledge, _inspiration, _todo = make_kernel(
        route("INSPIRATION_SEARCH"),
        inspiration_output={
            "tool_name": "inspiration_search",
            "data": {"items": "not-a-list", "total": True},
        },
    )

    result = await kernel.compile().ainvoke(make_state())

    assert result["error_code"] == "AGENT_OUTPUT_INVALID"
    assert result["retrieval_result"] is None


@pytest.mark.asyncio
async def test_finalize_returns_complete_strictly_validated_state() -> None:
    kernel, _knowledge, _inspiration, _todo = make_kernel(route("DIRECT_ANSWER"))

    result = await kernel.compile().ainvoke(make_state())

    terminal = AgentState.model_validate(result, strict=True)
    assert set(result) == set(AgentState.model_fields)
    assert terminal.final_answer
