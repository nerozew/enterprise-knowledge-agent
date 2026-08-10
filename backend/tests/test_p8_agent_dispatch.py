from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.graph import AgentKernel
from app.agent.hitl import ApprovalResumeDecision, TodoApprovalResumeWorker, TodoWorkflowInput
from app.agent.models import (
    AgentMessage,
    AgentState,
    RouteDecision,
    TodoCreateInput,
    ToolProposal,
    ToolResult,
)
from app.agent.providers import AgentProviderUnavailableError
from app.core.config import Settings
from app.core.errors import AppError
from app.models.agent import (
    AgentMessageRecord,
    AgentRun,
    AgentRunDispatch,
    AgentRunEvent,
    ApprovalRequest,
    Conversation,
)
from app.models.entities import User, Workspace, WorkspaceMember
from app.repositories.agent import AgentRepository
from app.services.agent_api import AgentEventStreamService
from app.services.agent_dispatch import AgentDispatchWorker, DispatchLeaseLost
from app.services.workspaces import canonical_request_hash

NOW = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)


class DirectModel:
    async def decide(self, **_kwargs):
        return RouteDecision(
            route="DIRECT_ANSWER",
            confidence=1,
            reason_code="CASUAL_OR_GENERAL",
            normalized_query="你好",
        )

    async def answer(self, **_kwargs):
        return "你好，已由 Worker 完成。"

    async def extract(self, **_kwargs):
        raise AssertionError("todo extraction must not run")


class FailingModel(DirectModel):
    async def decide(self, **_kwargs):
        raise AgentProviderUnavailableError("sanitized")


class TodoModel(DirectModel):
    async def decide(self, **_kwargs):
        return RouteDecision(
            route="TODO_CREATE",
            confidence=1,
            reason_code="ACTION_REQUEST",
            normalized_query="创建验收待办",
        )

    async def extract(self, **_kwargs):
        return TodoCreateInput(title="提交 P8 验收报告")


class UnusedTool:
    async def invoke(self, *_args, **_kwargs):
        return ToolResult(tool_name="inspiration_search", data={})


def kernel(model) -> AgentKernel:
    tool = UnusedTool()
    return AgentKernel(
        router=model,
        direct_answer=model,
        todo_arguments=model,
        knowledge_tool=tool,
        inspiration_tool=tool,
    )


async def seed_dispatch(session_factory):
    actor_id = uuid4()
    workspace_id = uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    dispatch_id = uuid4()
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=actor_id,
                    email=f"{actor_id}@dispatch.example",
                    password_hash="unused",
                    display_name="Dispatch User",
                ),
                Workspace(
                    id=workspace_id,
                    name="Dispatch Workspace",
                    type="TEAM",
                    status="ACTIVE",
                    version=1,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=actor_id,
                    role="EDITOR",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=actor_id,
                ),
                Conversation(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    created_by_user_id=actor_id,
                    status="ACTIVE",
                    version=1,
                ),
            ]
        )
        await session.flush()
        session.add(
            AgentRun(
                id=run_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                requested_by_user_id=actor_id,
                status="RUNNING",
                graph_version="p8-agent-mvp-v1-b7",
                input_text="你好",
                version=1,
                started_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            [
                AgentMessageRecord(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    author_user_id=actor_id,
                    role="USER",
                    content="你好",
                    sequence_no=1,
                    version=1,
                ),
                AgentRunEvent(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    sequence_no=1,
                    event_type="run.created",
                    payload_json={"status": "RUNNING"},
                ),
                AgentRunDispatch(
                    id=dispatch_id,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    status="PENDING",
                    attempts=0,
                    available_at=NOW,
                ),
            ]
        )
        await session.commit()
    return workspace_id, run_id, dispatch_id


def settings(**updates) -> Settings:
    return Settings(
        app_env="test",
        jwt_secret="test",
        agent_dispatch_renew_seconds=2,
        agent_dispatch_lease_seconds=10,
        **updates,
    )


@pytest.mark.asyncio
async def test_claim_token_fences_stale_renewal_and_expired_takeover(session_factory) -> None:
    _workspace_id, _run_id, dispatch_id = await seed_dispatch(session_factory)
    first = AgentDispatchWorker(
        session_factory, settings(), worker_id="worker-a", clock=lambda: NOW
    )
    claimed = await first.claim(limit=1)
    assert len(claimed) == 1
    first_token = claimed[0].lease_owner
    assert first_token and first_token.startswith("worker-a:")

    async with session_factory() as session:
        await session.execute(
            update(AgentRunDispatch)
            .where(AgentRunDispatch.id == dispatch_id)
            .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
        await session.commit()
    takeover = AgentDispatchWorker(session_factory, settings(), worker_id="worker-b")
    reclaimed = await takeover.claim(limit=1)
    assert len(reclaimed) == 1
    assert reclaimed[0].lease_owner != first_token
    assert reclaimed[0].attempts == 2
    assert not await first.renew(claimed[0])
    assert reclaimed[0].id == dispatch_id


@pytest.mark.asyncio
async def test_t3_terminal_commit_rejects_expired_claim_inside_transaction(session_factory) -> None:
    workspace_id, run_id, dispatch_id = await seed_dispatch(session_factory)
    worker = AgentDispatchWorker(session_factory, settings(), worker_id="worker-fenced")
    dispatch = (await worker.claim(limit=1))[0]
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None
        await session.execute(
            update(AgentRunDispatch)
            .where(AgentRunDispatch.id == dispatch_id)
            .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
        await session.commit()
    result = AgentState(
        messages=[AgentMessage(role="user", content="hello")],
        workspace_id=workspace_id,
        conversation_id=run.conversation_id,
        run_id=run_id,
        actor_id=run.requested_by_user_id,
        route=RouteDecision(
            route="DIRECT_ANSWER",
            confidence=1,
            reason_code="CASUAL_OR_GENERAL",
            normalized_query="hello",
        ),
        final_answer="done",
    )
    with pytest.raises(DispatchLeaseLost):
        await worker._persist_terminal(dispatch, result)
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None and run.status == "RUNNING"


@pytest.mark.asyncio
async def test_t3_approval_draft_rejects_lost_claim_and_new_claim_retries(
    session_factory,
) -> None:
    workspace_id, run_id, dispatch_id = await seed_dispatch(session_factory)
    worker = AgentDispatchWorker(session_factory, settings(), worker_id="worker-t4-fenced")
    dispatch = (await worker.claim(limit=1))[0]
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None
        run.route = "TODO_CREATE"
        run.reason_code = "ACTION_REQUEST"
        await session.execute(
            update(AgentRunDispatch)
            .where(AgentRunDispatch.id == dispatch_id)
            .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
        await session.commit()
    arguments = TodoCreateInput(title="fenced todo", assignee_id=run.requested_by_user_id)
    proposal = ToolProposal(
        tool_call_id=uuid4(),
        arguments=arguments,
        arguments_hash=canonical_request_hash(arguments.model_dump(mode="json")),
    )
    workflow = TodoWorkflowInput(
        workspace_id=workspace_id,
        actor_id=run.requested_by_user_id,
        conversation_id=run.conversation_id,
        run_id=run_id,
        proposal=proposal,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    with pytest.raises(AppError) as exc_info:
        await TodoApprovalResumeWorker(session_factory).start(
            workflow,
            dispatch_id=dispatch.id,
            claim_token=dispatch.lease_owner,
        )
    assert exc_info.value.code == "dispatch_lease_lost"
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None and run.status == "RUNNING"
        assert await session.scalar(select(func.count()).select_from(ApprovalRequest)) == 0

    takeover = AgentDispatchWorker(
        session_factory, settings(), worker_id="worker-t3-takeover"
    )
    reclaimed = (await takeover.claim(limit=1))[0]
    result = await TodoApprovalResumeWorker(session_factory).start(
        workflow,
        dispatch_id=reclaimed.id,
        claim_token=reclaimed.lease_owner,
    )
    assert "__interrupt__" in result
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None and run.status == "WAITING_APPROVAL"
        assert await session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1


@pytest.mark.asyncio
async def test_claimed_batch_starts_processing_concurrently(session_factory) -> None:
    await seed_dispatch(session_factory)
    await seed_dispatch(session_factory)
    worker = AgentDispatchWorker(session_factory, settings(), worker_id="worker-batch")
    started = 0
    both_started = asyncio.Event()

    async def observe_start(_dispatch: AgentRunDispatch) -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)

    worker._run_claim_with_renewal = observe_start  # type: ignore[method-assign]
    assert await worker.run_once() == 2
    assert started == 2


@pytest.mark.asyncio
async def test_worker_persists_first_route_answer_events_and_terminal_dispatch(
    session_factory,
) -> None:
    workspace_id, run_id, _dispatch_id = await seed_dispatch(session_factory)
    model = DirectModel()
    worker = AgentDispatchWorker(
        session_factory,
        settings(),
        worker_id="worker-direct",
        kernel_factory=lambda _session, _run: kernel(model),
        clock=lambda: NOW,
    )
    assert await worker.run_once() == 1
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        dispatch = await AgentRepository(session).get_dispatch_by_run(workspace_id, run_id)
        assert run is not None and run.status == "COMPLETED"
        assert run.route == "DIRECT_ANSWER"
        assert dispatch is not None and dispatch.status == "SUCCEEDED"
        event_types = list(
            await session.scalars(
                select(AgentRunEvent.event_type)
                .where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.run_id == run_id,
                )
                .order_by(AgentRunEvent.sequence_no)
            )
        )
        assert event_types == ["run.created", "route.selected", "run.completed"]


@pytest.mark.asyncio
async def test_retryable_provider_failure_is_rescheduled_without_failing_run(
    session_factory,
) -> None:
    workspace_id, run_id, _dispatch_id = await seed_dispatch(session_factory)
    model = FailingModel()
    worker = AgentDispatchWorker(
        session_factory,
        settings(),
        worker_id="worker-retry",
        kernel_factory=lambda _session, _run: kernel(model),
        clock=lambda: NOW,
        random_source=lambda: 0,
    )
    async with session_factory() as session:
        before = await AgentRepository(session).database_utc_now()
    assert await worker.run_once() == 1
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        dispatch = await AgentRepository(session).get_dispatch_by_run(workspace_id, run_id)
        assert run is not None and run.status == "RUNNING"
        assert dispatch is not None and dispatch.status == "PENDING"
        assert dispatch.attempts == 1
        available_at = dispatch.available_at.replace(tzinfo=UTC)
        assert before + timedelta(seconds=1) <= available_at <= before + timedelta(seconds=4)
        assert dispatch.last_error_code == "agentproviderunavailableerror"


@pytest.mark.asyncio
async def test_todo_worker_checkpoint_publish_resume_has_exact_event_order(
    session_factory,
) -> None:
    workspace_id, run_id, _dispatch_id = await seed_dispatch(session_factory)
    model = TodoModel()
    worker = AgentDispatchWorker(
        session_factory,
        settings(),
        worker_id="worker-todo",
        kernel_factory=lambda _session, _run: kernel(model),
        clock=lambda: NOW,
    )
    assert await worker.run_once() == 1
    async with session_factory() as session:
        repo = AgentRepository(session)
        run = await repo.get_run(workspace_id, run_id)
        approval = await repo.get_approval_for_run(workspace_id, run_id)
        assert run is not None and run.status == "WAITING_APPROVAL"
        assert approval is not None
        assert run.checkpoint_ns == approval.checkpoint_ns
        assert run.checkpoint_id == approval.checkpoint_id
        assert run.checkpoint_ns and f":todo:{run_id}" in run.checkpoint_ns
        actor_id = run.requested_by_user_id
        assert approval.arguments_json == {
            "title": "提交 P8 验收报告",
            "description": None,
            "due_at": None,
            "assignee_id": str(actor_id),
        }
        conversation_id = run.conversation_id
        visible_event_types = list(
            await session.scalars(
                select(AgentRunEvent.event_type)
                .where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.run_id == run_id,
                )
                .order_by(AgentRunEvent.sequence_no)
            )
        )
        assert visible_event_types == [
            "run.created",
            "route.selected",
            "tool.proposed",
            "approval.required",
        ]
        approval_id = approval.id
        approval_version = approval.version

    await TodoApprovalResumeWorker(
        session_factory, clock=lambda: NOW + timedelta(seconds=1)
    ).resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=ApprovalResumeDecision(
            approval_id=approval_id,
            approval_version=approval_version,
            approved=True,
        ),
    )
    async with session_factory() as session:
        repo = AgentRepository(session)
        run = await repo.get_run(workspace_id, run_id)
        todo = await repo.get_todo_by_approval(workspace_id, approval_id)
        assert run is not None and run.status == "COMPLETED"
        assert todo is not None
        events = list(
            await session.scalars(
                select(AgentRunEvent)
                .where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.run_id == run_id,
                )
                .order_by(AgentRunEvent.sequence_no)
            )
        )
        assert [event.sequence_no for event in events] == list(range(1, 10))
        assert [event.event_type for event in events] == [
            "run.created",
            "route.selected",
            "tool.proposed",
            "approval.required",
            "approval.resolved",
            "tool.started",
            "todo.created",
            "tool.completed",
            "run.completed",
        ]


@pytest.mark.asyncio
async def test_sse_rechecks_membership_before_query_and_rejects_expired_token(
    session_factory,
) -> None:
    workspace_id, run_id, _dispatch_id = await seed_dispatch(session_factory)
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None
        actor_id = run.requested_by_user_id
    service = AgentEventStreamService(
        session_factory,
        actor_id,
        workspace_id,
        run_id,
        token_expires_at=NOW + timedelta(minutes=1),
        clock=lambda: NOW,
    )
    cursor, _ = await service.prepare("0")
    async with session_factory() as session:
        await session.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == actor_id,
            )
            .values(status="SUSPENDED", version=2)
        )
        await session.commit()
    stream = service.stream(cursor)
    close_frame = await anext(stream)
    assert "event: stream.closed" in close_frame
    assert '"reason":"authorization_revoked"' in close_frame
    assert "id:" not in close_frame and '"seq"' not in close_frame
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    expired = AgentEventStreamService(
        session_factory,
        actor_id,
        workspace_id,
        run_id,
        token_expires_at=NOW,
        clock=lambda: NOW,
    )
    with pytest.raises(AppError) as exc_info:
        await expired.prepare("0")
    assert exc_info.value.code == "not_authenticated"

    expired_after_prepare = AgentEventStreamService(
        session_factory,
        actor_id,
        workspace_id,
        run_id,
        token_expires_at=NOW + timedelta(seconds=1),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    frame = await anext(expired_after_prepare.stream(cursor))
    assert '"reason":"token_expired"' in frame


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL 双 Worker SKIP LOCKED 测试",
)
async def test_mysql_two_workers_only_one_claims_a_dispatch() -> None:
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    assert "charset=utf8mb4" in database_url
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        await seed_dispatch(factory)
        first = AgentDispatchWorker(factory, settings(), worker_id="mysql-worker-a")
        second = AgentDispatchWorker(factory, settings(), worker_id="mysql-worker-b")
        claims = await asyncio.gather(first.claim(limit=1), second.claim(limit=1))
        assert sorted(len(rows) for rows in claims) == [0, 1]
        token = next(rows[0].lease_owner for rows in claims if rows)
        assert token and token.count(":") == 1
    finally:
        await engine.dispose()
