from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.checkpoint import (
    TODO_CHECKPOINT_NAMESPACE,
    AsyncSqlAlchemyCheckpointSaver,
    CheckpointInUseError,
    checkpoint_config,
)
from app.agent.hitl import (
    ApprovalResumeDecision,
    TodoApprovalResumeWorker,
    TodoWorkflowInput,
)
from app.agent.models import AgentMessage, TodoCreateInput, ToolProposal
from app.core.errors import AppError
from app.models.agent import (
    AgentCheckpoint,
    AgentCheckpointWrite,
    AgentMessageRecord,
    AgentRun,
    AgentRunEvent,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Conversation,
    Todo,
)
from app.models.base import Base
from app.models.entities import AuditLog, User, Workspace, WorkspaceMember
from app.repositories.agent import AgentRepository
from app.services.agent import TodoApprovalService
from app.services.agent_checkpoint import AgentCheckpointBindingService
from app.services.agent_memory import ShortTermConversationMemory
from app.services.workspaces import canonical_request_hash

NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


def run_alembic(database_url: str, command: str, revision: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def make_proposal(*, title: str = "提交周报") -> ToolProposal:
    arguments = TodoCreateInput(title=title)
    return ToolProposal(
        tool_call_id=uuid4(),
        arguments=arguments,
        arguments_hash=canonical_request_hash(arguments.model_dump(mode="json")),
    )


async def seed_hitl_run(
    session_factory,
    *,
    workspace_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> tuple[UUID, UUID, UUID, UUID]:
    actor_id = actor_id or uuid4()
    workspace_id = workspace_id or uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=actor_id,
                    email=f"{actor_id}@hitl.example",
                    password_hash="unused",
                    display_name="HITL User",
                ),
                Workspace(
                    id=workspace_id,
                    name="HITL Workspace",
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
                status=AgentRunStatus.RUNNING.value,
                route="TODO_CREATE",
                reason_code="ACTION_REQUEST",
                graph_version="p8-agent-mvp-v1-b7",
                input_text="创建周报待办",
                version=1,
                started_at=NOW,
            )
        )
        await session.commit()
    return actor_id, workspace_id, conversation_id, run_id


def workflow_input(
    actor_id: UUID,
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    proposal: ToolProposal,
    *,
    expires_at: datetime | None = None,
) -> TodoWorkflowInput:
    return TodoWorkflowInput(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal=proposal,
        expires_at=expires_at or NOW + timedelta(minutes=10),
        short_term_messages=[AgentMessage(role="user", content="帮我创建周报待办").model_dump()],
    )


async def pending_approval(
    session_factory, workspace_id: UUID, run_id: UUID, tool_call_id: UUID
) -> ApprovalRequest:
    async with session_factory() as session:
        approval = await AgentRepository(session).get_approval_by_call(
            workspace_id, run_id, tool_call_id
        )
        assert approval is not None
        return approval


async def assert_no_hitl_side_effects(session_factory) -> None:
    async with session_factory() as session:
        for model in (
            AgentCheckpoint,
            AgentCheckpointWrite,
            ApprovalRequest,
            AgentRunEvent,
            Todo,
            AuditLog,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_start_authorizes_before_memory_or_any_durable_side_effect(
    session_factory, monkeypatch
) -> None:
    owner_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    viewer_id = uuid4()
    outsider_id = uuid4()
    other_workspace_id = uuid4()
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=viewer_id,
                    email=f"{viewer_id}@hitl.example",
                    password_hash="unused",
                    display_name="Viewer",
                ),
                User(
                    id=outsider_id,
                    email=f"{outsider_id}@hitl.example",
                    password_hash="unused",
                    display_name="Outsider",
                ),
                Workspace(
                    id=other_workspace_id,
                    name="Other Workspace",
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
                    user_id=viewer_id,
                    role="VIEWER",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=owner_id,
                ),
                WorkspaceMember(
                    workspace_id=other_workspace_id,
                    user_id=owner_id,
                    role="EDITOR",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=owner_id,
                ),
                AgentMessageRecord(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    author_user_id=owner_id,
                    role="USER",
                    content="private conversation content",
                    sequence_no=1,
                    version=1,
                ),
            ]
        )
        await session.commit()

    async def forbidden_memory_load(*_args, **_kwargs):
        raise AssertionError("memory must not be loaded before authorization")

    monkeypatch.setattr(ShortTermConversationMemory, "load", forbidden_memory_load)
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    proposal = make_proposal()
    attempts = [
        workflow_input(viewer_id, workspace_id, conversation_id, run_id, proposal),
        workflow_input(outsider_id, workspace_id, conversation_id, run_id, proposal),
        workflow_input(owner_id, other_workspace_id, conversation_id, run_id, proposal),
    ]
    expected_codes = ["permission_denied", "resource_not_found", "resource_not_found"]
    for workflow, expected_code in zip(attempts, expected_codes, strict=True):
        with pytest.raises(AppError) as exc_info:
            await worker.start(workflow)
        assert exc_info.value.code == expected_code
        await assert_no_hitl_side_effects(session_factory)


@pytest.mark.asyncio
async def test_start_revocation_after_final_authorization_has_zero_durable_side_effects(
    session_factory, monkeypatch
) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    original_authorize = worker._authorize_start
    calls = 0

    async def revoke_after_preflight(workflow: TodoWorkflowInput) -> None:
        nonlocal calls
        await original_authorize(workflow)
        calls += 1
        if calls == 2:
            async with session_factory() as session:
                await session.execute(
                    update(WorkspaceMember)
                    .where(
                        WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.user_id == actor_id,
                    )
                    .values(role="VIEWER", version=2)
                )
                await session.commit()

    monkeypatch.setattr(worker, "_authorize_start", revoke_after_preflight)
    workflow = workflow_input(
        actor_id, workspace_id, conversation_id, run_id, make_proposal()
    ).model_copy(
        update={"short_term_messages": [{"role": "user", "content": "private-after-revoke"}]}
    )
    with pytest.raises(AppError) as exc_info:
        await worker.start(workflow)
    assert exc_info.value.code == "permission_denied"
    assert calls == 2
    await assert_no_hitl_side_effects(session_factory)
    async with session_factory() as session:
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None
        assert run.status == AgentRunStatus.RUNNING.value
        assert run.version == 1
        assert run.checkpoint_id is None
        blobs = list(await session.scalars(select(AgentCheckpoint.checkpoint_blob)))
    assert all(b"private-after-revoke" not in blob for blob in blobs)


@pytest.mark.asyncio
async def test_failed_start_cleanup_preserves_concurrent_legal_run_namespace(
    tmp_path, monkeypatch
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'concurrent-cleanup.db').as_posix()}",
        connect_args={"timeout": 30},
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    revoked_actor, workspace_id, conversation_id, revoked_run = await seed_hitl_run(session_factory)
    legal_actor = uuid4()
    legal_run = uuid4()
    async with session_factory() as session:
        session.add(
            User(
                id=legal_actor,
                email=f"{legal_actor}@hitl.example",
                password_hash="unused",
                display_name="Concurrent Legal User",
            )
        )
        await session.flush()
        session.add_all(
            [
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=legal_actor,
                    role="EDITOR",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=revoked_actor,
                ),
                AgentRun(
                    id=legal_run,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    requested_by_user_id=legal_actor,
                    status=AgentRunStatus.RUNNING.value,
                    route="TODO_CREATE",
                    reason_code="ACTION_REQUEST",
                    graph_version="p8-agent-mvp-v1-b7",
                    input_text="concurrent legal run",
                    version=1,
                    started_at=NOW,
                ),
            ]
        )
        await session.commit()

    revoked_worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    legal_worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    original_authorize = revoked_worker._authorize_start
    calls = 0

    async def revoke_after_final_check(workflow: TodoWorkflowInput) -> None:
        nonlocal calls
        await original_authorize(workflow)
        calls += 1
        if calls == 2:
            async with session_factory() as session:
                await session.execute(
                    update(WorkspaceMember)
                    .where(
                        WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.user_id == revoked_actor,
                    )
                    .values(role="VIEWER", version=2)
                )
                await session.commit()

    monkeypatch.setattr(revoked_worker, "_authorize_start", revoke_after_final_check)
    legal_proposal = make_proposal(title="legal concurrent todo")
    revoked_proposal = make_proposal(title="revoked concurrent todo")
    results = await asyncio.gather(
        legal_worker.start(
            workflow_input(
                legal_actor,
                workspace_id,
                conversation_id,
                legal_run,
                legal_proposal,
            )
        ),
        revoked_worker.start(
            workflow_input(
                revoked_actor,
                workspace_id,
                conversation_id,
                revoked_run,
                revoked_proposal,
            )
        ),
        return_exceptions=True,
    )
    legal_result: object = results[0]
    revoked_result: object = results[1]
    assert isinstance(legal_result, dict) and "__interrupt__" in legal_result
    assert isinstance(revoked_result, AppError)
    assert revoked_result.code == "permission_denied"
    legal_namespace = legal_worker._checkpoint_namespace(legal_run)
    revoked_namespace = revoked_worker._checkpoint_namespace(revoked_run)
    async with session_factory() as session:
        legal_checkpoints = await session.scalar(
            select(func.count())
            .select_from(AgentCheckpoint)
            .where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.checkpoint_ns == legal_namespace,
            )
        )
        revoked_checkpoints = await session.scalar(
            select(func.count())
            .select_from(AgentCheckpoint)
            .where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.checkpoint_ns == revoked_namespace,
            )
        )
        legal_writes = await session.scalar(
            select(func.count())
            .select_from(AgentCheckpointWrite)
            .where(
                AgentCheckpointWrite.workspace_id == workspace_id,
                AgentCheckpointWrite.checkpoint_ns == legal_namespace,
            )
        )
        revoked_writes = await session.scalar(
            select(func.count())
            .select_from(AgentCheckpointWrite)
            .where(
                AgentCheckpointWrite.workspace_id == workspace_id,
                AgentCheckpointWrite.checkpoint_ns == revoked_namespace,
            )
        )
    assert legal_checkpoints and legal_checkpoints > 0
    assert legal_writes and legal_writes > 0
    assert revoked_checkpoints == revoked_writes == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_interrupt_reconstruct_resume_approve_and_duplicate_resume_are_exactly_once(
    session_factory,
) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    first_worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)

    interrupted = await first_worker.start(
        workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal)
    )
    assert "__interrupt__" in interrupted
    # Retrying before the interrupt is observed must replay the same approval fact.
    retried = await first_worker.start(
        workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal)
    )
    assert "__interrupt__" in retried
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentRunEvent)
                .where(AgentRunEvent.event_type == "approval.required")
            )
            == 1
        )

    # Reconstructing the worker and saver simulates a process restart.
    resumed_worker = TodoApprovalResumeWorker(
        session_factory, clock=lambda: NOW + timedelta(minutes=1)
    )
    decision = ApprovalResumeDecision(
        approval_id=approval.id,
        approval_version=approval.version,
        approved=True,
    )
    completed = await resumed_worker.resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=decision,
    )
    replayed = await resumed_worker.resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=decision,
    )
    assert completed["outcome"] == replayed["outcome"] == "APPROVED"
    assert completed["todo_id"] == replayed["todo_id"]
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Todo)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "todo.created")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_two_pending_runs_in_one_conversation_resume_their_exact_checkpoints(
    session_factory,
) -> None:
    actor_id, workspace_id, conversation_id, first_run_id = await seed_hitl_run(session_factory)
    second_run_id = uuid4()
    async with session_factory() as session:
        session.add(
            AgentRun(
                id=second_run_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                requested_by_user_id=actor_id,
                status=AgentRunStatus.RUNNING.value,
                route="TODO_CREATE",
                reason_code="ACTION_REQUEST",
                graph_version="p8-agent-mvp-v1-b7",
                input_text="create second todo",
                version=1,
                started_at=NOW,
            )
        )
        await session.commit()
    first_proposal = make_proposal(title="first pending todo")
    second_proposal = make_proposal(title="second pending todo")
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(
        workflow_input(
            actor_id,
            workspace_id,
            conversation_id,
            first_run_id,
            first_proposal,
        )
    )
    await worker.start(
        workflow_input(
            actor_id,
            workspace_id,
            conversation_id,
            second_run_id,
            second_proposal,
        )
    )
    first_approval = await pending_approval(
        session_factory, workspace_id, first_run_id, first_proposal.tool_call_id
    )
    second_approval = await pending_approval(
        session_factory, workspace_id, second_run_id, second_proposal.tool_call_id
    )
    assert first_approval.checkpoint_id
    assert second_approval.checkpoint_id
    assert first_approval.checkpoint_id != second_approval.checkpoint_id

    first_result = await worker.resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=ApprovalResumeDecision(
            approval_id=first_approval.id,
            approval_version=first_approval.version,
            approved=True,
        ),
    )
    assert first_result["outcome"] == "APPROVED"
    async with session_factory() as session:
        second_current = await AgentRepository(session).get_approval(
            workspace_id, second_approval.id
        )
        assert second_current is not None
        assert second_current.status == ApprovalStatus.PENDING.value
        assert await session.scalar(select(func.count()).select_from(Todo)) == 1
        # Simulate the recoverable crash window: checkpoint durable, binding commit absent.
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == second_approval.id)
            .values(checkpoint_ns=None, checkpoint_id=None)
        )
        await session.flush()
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == second_run_id)
            .values(checkpoint_ns=None, checkpoint_id=None)
        )
        await session.commit()

    second_result = await worker.resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=ApprovalResumeDecision(
            approval_id=second_approval.id,
            approval_version=second_approval.version,
            approved=False,
        ),
    )
    assert second_result["outcome"] == "REJECTED"
    async with session_factory() as session:
        first_todo = await AgentRepository(session).get_todo_by_approval(
            workspace_id, first_approval.id
        )
        rebound = await AgentRepository(session).get_approval(workspace_id, second_approval.id)
        assert first_todo is not None and first_todo.source_run_id == first_run_id
        assert rebound is not None and rebound.checkpoint_id == second_approval.checkpoint_id
        assert await session.scalar(select(func.count()).select_from(Todo)) == 1


@pytest.mark.asyncio
async def test_0009_legacy_pending_upgrade_resumes_exact_run_not_latest_other_run(
    tmp_path,
) -> None:
    database = tmp_path / "legacy-0009-resume.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    run_alembic(database_url, "upgrade", "head")
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    actor_id, workspace_id, conversation_id, first_run_id = await seed_hitl_run(factory)
    second_run_id = uuid4()
    async with factory() as session:
        session.add(
            AgentRun(
                id=second_run_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                requested_by_user_id=actor_id,
                status=AgentRunStatus.RUNNING.value,
                route="TODO_CREATE",
                reason_code="ACTION_REQUEST",
                graph_version="p8-agent-mvp-v1-b7",
                input_text="legacy second run",
                version=1,
                started_at=NOW,
            )
        )
        await session.commit()
    first_proposal = make_proposal(title="legacy exact first")
    second_proposal = make_proposal(title="legacy misleading latest")
    worker = TodoApprovalResumeWorker(factory, clock=lambda: NOW)
    await worker.start(
        workflow_input(
            actor_id,
            workspace_id,
            conversation_id,
            first_run_id,
            first_proposal,
        )
    )
    await worker.start(
        workflow_input(
            actor_id,
            workspace_id,
            conversation_id,
            second_run_id,
            second_proposal,
        )
    )
    first_approval = await pending_approval(
        factory, workspace_id, first_run_id, first_proposal.tool_call_id
    )
    second_approval = await pending_approval(
        factory, workspace_id, second_run_id, second_proposal.tool_call_id
    )
    namespaces = [first_approval.checkpoint_ns, second_approval.checkpoint_ns]
    assert all(namespace is not None for namespace in namespaces)
    async with factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.workspace_id == workspace_id)
            .values(checkpoint_ns=None, checkpoint_id=None)
        )
        await session.flush()
        await session.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == workspace_id)
            .values(checkpoint_ns=None, checkpoint_id=None)
        )
        await session.flush()
        await session.execute(
            update(AgentCheckpointWrite)
            .where(AgentCheckpointWrite.checkpoint_ns.in_(namespaces))
            .values(checkpoint_ns=TODO_CHECKPOINT_NAMESPACE)
        )
        await session.execute(
            update(AgentCheckpoint)
            .where(AgentCheckpoint.checkpoint_ns.in_(namespaces))
            .values(checkpoint_ns=TODO_CHECKPOINT_NAMESPACE)
        )
        # 0011 intentionally fences downgrades while recovery facts are active.
        # Quiesce them only long enough to remove the dispatch migration, then
        # restore the legacy pending facts before exercising the 0010 backfill.
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.workspace_id == workspace_id)
            .values(status=ApprovalStatus.REJECTED.value)
        )
        await session.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == workspace_id)
            .values(status=AgentRunStatus.FAILED.value)
        )
        await session.commit()
    await engine.dispose()

    run_alembic(database_url, "downgrade", "0010_p8_checkpoint_binding")
    legacy_engine = create_async_engine(database_url)
    legacy_factory = async_sessionmaker(
        legacy_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with legacy_factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.workspace_id == workspace_id)
            .values(status=ApprovalStatus.PENDING.value)
        )
        await session.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == workspace_id)
            .values(status=AgentRunStatus.WAITING_APPROVAL.value)
        )
        await session.commit()
    await legacy_engine.dispose()
    run_alembic(database_url, "downgrade", "0009_p8_agent_checkpoints")
    run_alembic(database_url, "upgrade", "head")
    resumed_engine = create_async_engine(database_url)
    resumed_factory = async_sessionmaker(
        resumed_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        result = await TodoApprovalResumeWorker(
            resumed_factory, clock=lambda: NOW + timedelta(minutes=1)
        ).resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=ApprovalResumeDecision(
                approval_id=first_approval.id,
                approval_version=first_approval.version,
                approved=True,
            ),
        )
        assert result["outcome"] == "APPROVED"
        async with resumed_factory() as session:
            first_todo = await AgentRepository(session).get_todo_by_approval(
                workspace_id, first_approval.id
            )
            second_current = await AgentRepository(session).get_approval(
                workspace_id, second_approval.id
            )
            rebound = await AgentRepository(session).get_approval(workspace_id, first_approval.id)
            assert first_todo is not None and first_todo.source_run_id == first_run_id
            assert rebound is not None
            assert rebound.checkpoint_ns == TODO_CHECKPOINT_NAMESPACE
            assert second_current is not None
            assert second_current.status == ApprovalStatus.PENDING.value
            assert second_current.checkpoint_id is None
    finally:
        await resumed_engine.dispose()


@pytest.mark.asyncio
async def test_unbound_multiple_exact_checkpoint_matches_fail_closed(session_factory) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    assert approval.checkpoint_ns is not None and approval.checkpoint_id is not None
    duplicate_id = "00000000-0000-6000-8000-000000000198"
    async with session_factory() as session:
        source = await session.scalar(
            select(AgentCheckpoint).where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.thread_id == conversation_id,
                AgentCheckpoint.checkpoint_ns == approval.checkpoint_ns,
                AgentCheckpoint.checkpoint_id == approval.checkpoint_id,
            )
        )
        assert source is not None
        writes = list(
            await session.scalars(
                select(AgentCheckpointWrite).where(
                    AgentCheckpointWrite.workspace_id == workspace_id,
                    AgentCheckpointWrite.thread_id == conversation_id,
                    AgentCheckpointWrite.checkpoint_ns == approval.checkpoint_ns,
                    AgentCheckpointWrite.checkpoint_id == approval.checkpoint_id,
                )
            )
        )
        session.add(
            AgentCheckpoint(
                workspace_id=workspace_id,
                thread_id=conversation_id,
                checkpoint_ns=approval.checkpoint_ns,
                checkpoint_id=duplicate_id,
                parent_checkpoint_id=source.parent_checkpoint_id,
                checkpoint_type=source.checkpoint_type,
                checkpoint_blob=source.checkpoint_blob,
                metadata_type=source.metadata_type,
                metadata_blob=source.metadata_blob,
            )
        )
        session.add_all(
            [
                AgentCheckpointWrite(
                    workspace_id=write.workspace_id,
                    thread_id=write.thread_id,
                    checkpoint_ns=write.checkpoint_ns,
                    checkpoint_id=duplicate_id,
                    task_id=write.task_id,
                    write_idx=write.write_idx,
                    task_path=write.task_path,
                    channel=write.channel,
                    value_type=write.value_type,
                    value_blob=write.value_blob,
                )
                for write in writes
            ]
        )
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval.id)
            .values(checkpoint_ns=None, checkpoint_id=None)
        )
        await session.flush()
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(checkpoint_ns=None, checkpoint_id=None)
        )
        await session.commit()

    with pytest.raises(AppError) as ambiguous:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=ApprovalResumeDecision(
                approval_id=approval.id,
                approval_version=approval.version,
                approved=True,
            ),
        )
    assert ambiguous.value.code == "checkpoint_ambiguous"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [False, True])
async def test_reject_and_expire_never_execute_todo(session_factory, decision: bool) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal(title="不能越过审批")
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(
        workflow_input(
            actor_id,
            workspace_id,
            conversation_id,
            run_id,
            proposal,
            expires_at=NOW + timedelta(minutes=1),
        )
    )
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    resume_worker = TodoApprovalResumeWorker(
        session_factory,
        clock=lambda: NOW + timedelta(minutes=2) if decision else NOW,
    )
    resume_decision = ApprovalResumeDecision(
        approval_id=approval.id,
        approval_version=approval.version,
        approved=decision,
    )
    if decision:
        with pytest.raises(AppError) as exc_info:
            await resume_worker.resume(
                workspace_id=workspace_id,
                actor_id=actor_id,
                conversation_id=conversation_id,
                decision=resume_decision,
            )
        assert exc_info.value.code == "approval_expired"
        expected_status = ApprovalStatus.EXPIRED.value
    else:
        result = await resume_worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=resume_decision,
        )
        assert result["outcome"] == "REJECTED"
        expected_status = ApprovalStatus.REJECTED.value
    async with session_factory() as session:
        current = await AgentRepository(session).get_approval(workspace_id, approval.id)
        assert current is not None and current.status == expected_status
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


@pytest.mark.asyncio
async def test_resume_rechecks_permission_and_checkpoint_scope(session_factory) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    async with session_factory() as session:
        await session.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == actor_id,
            )
            .values(role="VIEWER", version=2)
        )
        await session.commit()
    with pytest.raises(AppError) as permission_error:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=ApprovalResumeDecision(
                approval_id=approval.id,
                approval_version=approval.version,
                approved=True,
            ),
        )
    assert permission_error.value.code == "permission_denied"
    with pytest.raises(AppError) as actor_error:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=uuid4(),
            conversation_id=conversation_id,
            decision=ApprovalResumeDecision(
                approval_id=approval.id,
                approval_version=approval.version,
                approved=True,
            ),
        )
    assert actor_error.value.code == "resource_not_found"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "opposite_error"),
    [
        (True, "approval_already_approved"),
        (False, "approval_already_rejected"),
    ],
)
async def test_terminal_resume_still_enforces_decision_version_and_current_rbac(
    session_factory, approved: bool, opposite_error: str
) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    original = ApprovalResumeDecision(
        approval_id=approval.id,
        approval_version=approval.version,
        approved=approved,
    )
    completed = await worker.resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=original,
    )
    assert completed["outcome"] == ("APPROVED" if approved else "REJECTED")

    with pytest.raises(AppError) as opposite:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=original.model_copy(update={"approved": not approved}),
        )
    assert opposite.value.code == opposite_error
    with pytest.raises(AppError) as stale:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=original.model_copy(update={"approval_version": approval.version + 1}),
        )
    assert stale.value.code == "version_conflict"

    async with session_factory() as session:
        await session.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == actor_id,
            )
            .values(role="VIEWER", version=2)
        )
        await session.commit()
    with pytest.raises(AppError) as revoked:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=original,
        )
    assert revoked.value.code == "permission_denied"


@pytest.mark.asyncio
async def test_resume_uses_run_then_approval_lock_order(session_factory, monkeypatch) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    lock_order: list[str] = []
    original_get_run = AgentRepository.get_run
    original_get_approval = AgentRepository.get_approval

    async def ordered_get_run(self, workspace, target_run, *, for_update=False):
        if for_update:
            lock_order.append("run")
        return await original_get_run(self, workspace, target_run, for_update=for_update)

    async def ordered_get_approval(self, workspace, target_approval, *, for_update=False):
        if for_update:
            lock_order.append("approval")
        return await original_get_approval(self, workspace, target_approval, for_update=for_update)

    monkeypatch.setattr(AgentRepository, "get_run", ordered_get_run)
    monkeypatch.setattr(AgentRepository, "get_approval", ordered_get_approval)
    result = await worker.resume(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        decision=ApprovalResumeDecision(
            approval_id=approval.id,
            approval_version=approval.version,
            approved=False,
        ),
    )
    assert result["outcome"] == "REJECTED"
    assert lock_order[-2:] == ["run", "approval"]


@pytest.mark.asyncio
async def test_resume_rechecks_approval_version(session_factory) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    with pytest.raises(AppError) as exc_info:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=ApprovalResumeDecision(
                approval_id=approval.id,
                approval_version=approval.version + 1,
                approved=True,
            ),
        )
    assert exc_info.value.code == "version_conflict"
    async with session_factory() as session:
        current = await AgentRepository(session).get_approval(workspace_id, approval.id)
        assert current is not None and current.status == ApprovalStatus.PENDING.value
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


@pytest.mark.asyncio
async def test_resume_missing_and_corrupted_checkpoint_fail_closed(session_factory) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    proposal = make_proposal()
    async with session_factory() as session:
        approval = await TodoApprovalService(
            session, actor_id, workspace_id, clock=lambda: NOW
        ).create_approval(
            run_id=run_id,
            proposal=proposal,
            expires_at=NOW + timedelta(minutes=10),
        )
    decision = ApprovalResumeDecision(approval_id=approval.id, approval_version=1, approved=True)
    with pytest.raises(AppError) as missing_error:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=decision,
        )
    assert missing_error.value.code == "checkpoint_not_ready"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0

    await worker.start(
        workflow_input(
            actor_id,
            workspace_id,
            conversation_id,
            run_id,
            proposal,
        )
    )
    async with session_factory() as session:
        rebound = await AgentRepository(session).get_approval(workspace_id, approval.id)
        rebound_run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert rebound is not None and rebound.checkpoint_id is not None
        assert rebound_run is not None
        assert rebound_run.checkpoint_id == rebound.checkpoint_id
        await session.execute(
            update(AgentCheckpoint)
            .where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.thread_id == conversation_id,
                AgentCheckpoint.checkpoint_ns == rebound.checkpoint_ns,
            )
            .values(checkpoint_blob=b"\xc1")
        )
        await session.commit()
    with pytest.raises(AppError) as corrupted_error:
        await worker.resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=decision,
        )
    assert corrupted_error.value.code == "checkpoint_corrupted"


@pytest.mark.asyncio
async def test_bound_checkpoint_rejects_generic_thread_deletion(session_factory) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    proposal = make_proposal()
    worker = TodoApprovalResumeWorker(session_factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    assert approval.checkpoint_ns is not None and approval.checkpoint_id is not None
    saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=approval.checkpoint_ns,
    )
    with pytest.raises(CheckpointInUseError):
        await saver.adelete_thread(str(conversation_id))
    exact = await saver.aget_tuple(
        checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            checkpoint_ns=approval.checkpoint_ns,
            checkpoint_id=approval.checkpoint_id,
        )
    )
    assert exact is not None


@pytest.mark.asyncio
async def test_start_cleans_other_run_orphans_but_preserves_valid_writes(
    session_factory,
) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    abandoned_namespace = f"{TODO_CHECKPOINT_NAMESPACE}:{uuid4()}"
    abandoned_id = "00000000-0000-6000-8000-000000000199"
    abandoned_saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=abandoned_namespace,
    )
    await abandoned_saver.aput_writes(
        checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            checkpoint_ns=abandoned_namespace,
            checkpoint_id=abandoned_id,
        ),
        [("result", "abandoned")],
        "abandoned-task",
    )
    old = NOW - timedelta(days=2)
    async with session_factory() as session:
        await session.execute(
            update(AgentCheckpointWrite)
            .where(AgentCheckpointWrite.checkpoint_id == abandoned_id)
            .values(created_at=old, updated_at=old)
        )
        await session.commit()

    proposal = make_proposal()
    await TodoApprovalResumeWorker(session_factory, clock=lambda: NOW).start(
        workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal)
    )
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    async with session_factory() as session:
        orphan_count = await session.scalar(
            select(func.count())
            .select_from(AgentCheckpointWrite)
            .where(
                AgentCheckpointWrite.workspace_id == workspace_id,
                AgentCheckpointWrite.checkpoint_ns == abandoned_namespace,
            )
        )
        valid_count = await session.scalar(
            select(func.count())
            .select_from(AgentCheckpointWrite)
            .where(
                AgentCheckpointWrite.workspace_id == workspace_id,
                AgentCheckpointWrite.checkpoint_ns == approval.checkpoint_ns,
                AgentCheckpointWrite.checkpoint_id == approval.checkpoint_id,
            )
        )
    assert orphan_count == 0
    assert valid_count and valid_count > 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL 审批锁序并发测试",
)
async def test_mysql_checkpoint_binding_and_resolution_share_lock_order() -> None:
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    assert "charset=utf8mb4" in database_url
    run_alembic(database_url, "upgrade", "head")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(factory)
    proposal = make_proposal(title="mysql lock order")
    worker = TodoApprovalResumeWorker(factory, clock=lambda: NOW)
    await worker.start(workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal))
    approval = await pending_approval(factory, workspace_id, run_id, proposal.tool_call_id)
    assert approval.checkpoint_ns is not None and approval.checkpoint_id is not None

    async def bind_checkpoint() -> None:
        async with factory() as session:
            await AgentCheckpointBindingService(session, actor_id).bind(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=run_id,
                approval_id=approval.id,
                checkpoint_ns=cast(str, approval.checkpoint_ns),
                checkpoint_id=cast(str, approval.checkpoint_id),
            )

    async def reject_approval() -> None:
        async with factory() as session:
            await TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW).resolve(
                approval.id,
                approval_version=approval.version,
                proposal=proposal,
                approved=False,
            )

    try:
        await asyncio.wait_for(asyncio.gather(bind_checkpoint(), reject_approval()), timeout=15)
    finally:
        async with engine.begin() as connection:
            for table_name in (
                "todos",
                "approval_requests",
                "agent_run_events",
                "agent_checkpoint_writes",
                "agent_runs",
                "agent_checkpoints",
                "messages",
                "conversations",
                "audit_logs",
                "workspace_members",
            ):
                await connection.execute(
                    text(f"DELETE FROM {table_name} WHERE workspace_id=:workspace_id"),
                    {"workspace_id": workspace_id},
                )
            await connection.execute(
                text("DELETE FROM workspaces WHERE id=:workspace_id"),
                {"workspace_id": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM users WHERE id=:actor_id"),
                {"actor_id": actor_id},
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_short_term_memory_is_bounded_and_conversation_scoped(session_factory) -> None:
    actor_id, workspace_id, conversation_id, run_id = await seed_hitl_run(session_factory)
    other_conversation_id = uuid4()
    async with session_factory() as session:
        session.add(
            Conversation(
                id=other_conversation_id,
                workspace_id=workspace_id,
                created_by_user_id=actor_id,
                status="ACTIVE",
                version=1,
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
                    content=f"message-{index}-" + "x" * 80,
                    sequence_no=index,
                    version=1,
                )
                for index in range(1, 6)
            ]
            + [
                AgentMessageRecord(
                    workspace_id=workspace_id,
                    conversation_id=other_conversation_id,
                    run_id=None,
                    author_user_id=actor_id,
                    role="USER",
                    content="other-conversation-secret",
                    sequence_no=1,
                    version=1,
                )
            ]
        )
        await session.commit()
    async with session_factory() as session:
        messages = await ShortTermConversationMemory(
            session, max_messages=3, max_characters=256
        ).load(workspace_id, conversation_id)
        cross_workspace = await ShortTermConversationMemory(session).load(uuid4(), conversation_id)
    assert len(messages) == 3
    assert sum(len(message.content) for message in messages) <= 256
    assert messages[-1].content.startswith("message-5-")
    assert all("other-conversation-secret" not in message.content for message in messages)
    assert cross_workspace == []

    proposal = make_proposal()
    await TodoApprovalResumeWorker(session_factory, clock=lambda: NOW).start(
        workflow_input(actor_id, workspace_id, conversation_id, run_id, proposal)
    )
    approval = await pending_approval(session_factory, workspace_id, run_id, proposal.tool_call_id)
    assert approval.checkpoint_ns is not None
    saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=approval.checkpoint_ns,
    )
    checkpoint = await saver.aget_tuple(
        checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            checkpoint_ns=approval.checkpoint_ns,
        )
    )
    assert checkpoint is not None
    checkpoint_messages = checkpoint.checkpoint["channel_values"]["short_term_messages"]
    assert len(checkpoint_messages) <= 12
    assert all("other-conversation-secret" not in item["content"] for item in checkpoint_messages)
