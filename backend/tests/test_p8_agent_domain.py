from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.models import TodoCreateInput, ToolProposal
from app.core.errors import AppError
from app.models.agent import (
    AgentCitation,
    AgentRun,
    AgentRunEvent,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Conversation,
    Todo,
)
from app.models.base import Base
from app.models.entities import AuditLog, MemberStatus, User, Workspace, WorkspaceMember
from app.models.knowledge import Document, DocumentChunk, DocumentVersion
from app.repositories.agent import AgentRepository
from app.services.agent import TodoApprovalService
from app.services.workspaces import canonical_request_hash

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)


def proposal(
    *,
    tool_call_id: UUID | None = None,
    title: str = "提交周报",
    assignee_id: UUID | None = None,
    arguments_hash: str | None = None,
) -> ToolProposal:
    arguments = TodoCreateInput(title=title, assignee_id=assignee_id)
    return ToolProposal(
        tool_call_id=tool_call_id or uuid4(),
        arguments=arguments,
        arguments_hash=arguments_hash or canonical_request_hash(arguments.model_dump(mode="json")),
    )


async def seed_run(
    session: AsyncSession,
    *,
    actor_id: UUID | None = None,
    workspace_id: UUID | None = None,
    role: str = "EDITOR",
    member_status: str = "ACTIVE",
) -> tuple[UUID, UUID, UUID]:
    actor_id = actor_id or uuid4()
    workspace_id = workspace_id or uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    existing_user = await session.get(User, actor_id)
    records: list[object] = [
        Workspace(
            id=workspace_id,
            name="P8 Workspace",
            type="TEAM",
            status="ACTIVE",
            version=1,
        )
    ]
    if existing_user is None:
        records.append(
            User(
                id=actor_id,
                email=f"{actor_id}@p8.example",
                password_hash="unused",
                display_name="P8 测试用户",
            )
        )
    session.add_all(records)
    await session.flush()
    session.add_all(
        [
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=actor_id,
                role=role,
                status=member_status,
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
            graph_version="p8-agent-mvp-v1-b1",
            input_text="帮我创建待办",
            version=1,
            started_at=NOW,
        )
    )
    await session.commit()
    return actor_id, workspace_id, run_id


async def create_pending(
    session: AsyncSession,
    actor_id: UUID,
    workspace_id: UUID,
    run_id: UUID,
    tool_proposal: ToolProposal,
    *,
    clock: datetime = NOW,
) -> ApprovalRequest:
    return await TodoApprovalService(
        session,
        actor_id,
        workspace_id,
        request_id="p8-test-request",
        clock=lambda: clock,
    ).create_approval(
        run_id=run_id,
        proposal=tool_proposal,
        expires_at=NOW + timedelta(minutes=10),
    )


@pytest.mark.asyncio
async def test_approval_precedes_todo_and_approval_is_idempotent(session_factory) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()

        first = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        replay = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)

        assert first.id == replay.id
        assert first.status == ApprovalStatus.PENDING.value
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None
        assert run.status == AgentRunStatus.WAITING_APPROVAL.value
        assert run.version == 2


@pytest.mark.asyncio
async def test_approve_creates_exactly_one_todo_and_replay_returns_same(session_factory) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        service = TodoApprovalService(
            session, actor_id, workspace_id, clock=lambda: NOW + timedelta(minutes=1)
        )

        resolved, first_todo = await service.resolve(
            approval.id,
            approval_version=1,
            proposal=tool_proposal,
            approved=True,
        )
        replayed, replay_todo = await service.resolve(
            approval.id,
            approval_version=1,
            proposal=tool_proposal,
            approved=True,
        )

        assert resolved.status == replayed.status == ApprovalStatus.APPROVED.value
        assert first_todo is not None and replay_todo is not None
        assert first_todo.id == replay_todo.id
        assert first_todo.assignee_user_id == actor_id
        assert first_todo.source_run_id == run_id
        assert first_todo.source_tool_call_id == tool_proposal.tool_call_id
        assert await session.scalar(select(func.count()).select_from(Todo)) == 1
        run = await AgentRepository(session).get_run(workspace_id, run_id)
        assert run is not None and run.status == AgentRunStatus.COMPLETED.value
        audit_actions = list(
            await session.scalars(
                select(AuditLog.action)
                .where(AuditLog.workspace_id == workspace_id)
                .order_by(AuditLog.created_at, AuditLog.action)
            )
        )
        assert "approval.requested" in audit_actions
        assert "approval.approved" in audit_actions
        assert "todo.created" in audit_actions
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
        assert event_types == [
            "tool.proposed",
            "approval.required",
            "approval.resolved",
            "tool.started",
            "todo.created",
            "tool.completed",
            "run.completed",
        ]


@pytest.mark.asyncio
async def test_reject_and_expire_never_create_todo(session_factory) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        service = TodoApprovalService(
            session, actor_id, workspace_id, clock=lambda: NOW + timedelta(minutes=1)
        )
        rejected, todo = await service.resolve(
            approval.id,
            approval_version=1,
            proposal=tool_proposal,
            approved=False,
        )
        replayed, replay_todo = await service.resolve(
            approval.id,
            approval_version=1,
            proposal=tool_proposal,
            approved=False,
        )
        assert rejected.status == replayed.status == ApprovalStatus.REJECTED.value
        assert todo is replay_todo is None
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0

    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        expired_service = TodoApprovalService(
            session, actor_id, workspace_id, clock=lambda: NOW + timedelta(minutes=11)
        )
        with pytest.raises(AppError, match="审批已过期") as exc_info:
            await expired_service.resolve(
                approval.id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )
        assert exc_info.value.code == "approval_expired"
        expired = await AgentRepository(session).get_approval(workspace_id, approval.id)
        assert expired is not None and expired.status == ApprovalStatus.EXPIRED.value
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


@pytest.mark.asyncio
async def test_viewer_suspended_member_and_outsider_cannot_create_approval(
    session_factory,
) -> None:
    for role, status, expected_code in (
        ("VIEWER", MemberStatus.ACTIVE.value, "permission_denied"),
        ("EDITOR", MemberStatus.SUSPENDED.value, "resource_not_found"),
    ):
        async with session_factory() as session:
            actor_id, workspace_id, run_id = await seed_run(
                session, role=role, member_status=status
            )
            with pytest.raises(AppError) as exc_info:
                await create_pending(session, actor_id, workspace_id, run_id, proposal())
            assert exc_info.value.code == expected_code
            assert await session.scalar(select(func.count()).select_from(Todo)) == 0

    async with session_factory() as session:
        owner_id, workspace_id, run_id = await seed_run(session)
        outsider_id = uuid4()
        session.add(
            User(
                id=outsider_id,
                email=f"{outsider_id}@p8.example",
                password_hash="unused",
                display_name="外部用户",
            )
        )
        await session.commit()
        with pytest.raises(AppError) as exc_info:
            await create_pending(session, outsider_id, workspace_id, run_id, proposal())
        assert exc_info.value.code == "resource_not_found"
        assert owner_id != outsider_id


@pytest.mark.asyncio
async def test_hash_tampering_version_and_assignee_validation(session_factory) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        with pytest.raises(AppError) as hash_error:
            await create_pending(
                session,
                actor_id,
                workspace_id,
                run_id,
                proposal(arguments_hash="0" * 64),
            )
        assert hash_error.value.code == "approval_parameters_tampered"
        with pytest.raises(AppError) as assignee_error:
            await create_pending(
                session,
                actor_id,
                workspace_id,
                run_id,
                proposal(assignee_id=uuid4()),
            )
        assert assignee_error.value.code == "unsupported_assignee"

        valid = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, valid)
        service = TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW)
        with pytest.raises(AppError) as version_error:
            await service.resolve(
                approval.id,
                approval_version=9,
                proposal=valid,
                approved=True,
            )
        assert version_error.value.code == "version_conflict"
        changed = proposal(tool_call_id=valid.tool_call_id, title="被篡改标题")
        with pytest.raises(AppError) as tamper_error:
            await service.resolve(
                approval.id,
                approval_version=1,
                proposal=changed,
                approved=True,
            )
        assert tamper_error.value.code == "approval_parameters_tampered"
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


@pytest.mark.asyncio
async def test_cross_workspace_approval_is_hidden(session_factory) -> None:
    async with session_factory() as session:
        actor_id, workspace_a, _run_a = await seed_run(session)
        workspace_b = uuid4()
        _actor, _workspace, run_b = await seed_run(
            session, actor_id=actor_id, workspace_id=workspace_b
        )
        tool_proposal = proposal()
        approval_b = await create_pending(session, actor_id, workspace_b, run_b, tool_proposal)

        with pytest.raises(AppError) as exc_info:
            await TodoApprovalService(session, actor_id, workspace_a, clock=lambda: NOW).resolve(
                approval_b.id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )
        assert exc_info.value.code == "resource_not_found"
        assert await session.scalar(select(func.count()).select_from(Todo)) == 0


async def file_session_factory(
    path: Path,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.as_posix()}",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


@pytest.mark.asyncio
async def test_concurrent_approval_creates_exactly_once(tmp_path) -> None:
    factory, engine = await file_session_factory(tmp_path / "p8-concurrent.db")
    try:
        async with factory() as seed_session:
            actor_id, workspace_id, run_id = await seed_run(seed_session)
            tool_proposal = proposal()
            approval = await create_pending(
                seed_session, actor_id, workspace_id, run_id, tool_proposal
            )

        async def approve_once() -> tuple[ApprovalRequest, Todo | None]:
            async with factory() as session:
                return await TodoApprovalService(
                    session,
                    actor_id,
                    workspace_id,
                    clock=lambda: NOW + timedelta(minutes=1),
                ).resolve(
                    approval.id,
                    approval_version=1,
                    proposal=tool_proposal,
                    approved=True,
                )

        first, second = await asyncio.gather(approve_once(), approve_once())
        assert first[1] is not None and second[1] is not None
        assert first[1].id == second[1].id
        async with factory() as verify_session:
            assert await verify_session.scalar(select(func.count()).select_from(Todo)) == 1
    finally:
        await engine.dispose()


def test_agent_metadata_has_workspace_constraints_and_cas_versions() -> None:
    expected_tables = {
        "conversations",
        "messages",
        "agent_runs",
        "agent_run_events",
        "approval_requests",
        "todos",
        "agent_citations",
        "agent_checkpoints",
        "agent_checkpoint_writes",
    }
    assert expected_tables.issubset(Base.metadata.tables)
    for table_name in expected_tables:
        table = Base.metadata.tables[table_name]
        assert "workspace_id" in table.c
    approval_constraints = {
        constraint.name for constraint in Base.metadata.tables["approval_requests"].constraints
    }
    todo_constraints = {constraint.name for constraint in Base.metadata.tables["todos"].constraints}
    assert "uq_approval_workspace_run_tool_call" in approval_constraints
    assert "uq_approval_workspace_id_run_tool" in approval_constraints
    assert "uq_todos_source_call" in todo_constraints
    assert "uq_todos_workspace_approval" in todo_constraints
    assert "fk_todos_workspace_approval_source" in todo_constraints
    citation_constraints = {
        constraint.name for constraint in Base.metadata.tables["agent_citations"].constraints
    }
    assert "fk_agent_citations_workspace_chunk_span" in citation_constraints
    checkpoint_write_constraints = {
        constraint.name
        for constraint in Base.metadata.tables["agent_checkpoint_writes"].constraints
    }
    checkpoint_write_indexes = {
        index.name for index in Base.metadata.tables["agent_checkpoint_writes"].indexes
    }
    assert "fk_agent_checkpoint_writes_workspace_conversation" in checkpoint_write_constraints
    assert "ix_agent_checkpoint_writes_orphan_cleanup" in checkpoint_write_indexes
    chunk_constraints = {
        constraint.name for constraint in Base.metadata.tables["document_chunks"].constraints
    }
    assert "uq_chunks_workspace_version_id_span" in chunk_constraints
    for table_name in ("conversations", "messages", "agent_runs", "approval_requests", "todos"):
        assert "version" in Base.metadata.tables[table_name].c
    check_names = {
        constraint.name
        for table_name in expected_tables
        for constraint in Base.metadata.tables[table_name].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_conversations_conversation_status" in check_names
    assert "ck_approval_requests_approval_request_status" in check_names
    assert all("_ck_" not in str(name or "") for name in check_names)


@pytest.mark.asyncio
async def test_todo_database_rejects_approval_source_mismatch(tmp_path) -> None:
    factory, engine = await file_session_factory(tmp_path / "p8-todo-source-fk.db")
    try:
        async with factory() as session:
            actor_id, workspace_id, run_id = await seed_run(session)
            tool_proposal = proposal()
            approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
            session.add(
                Todo(
                    workspace_id=workspace_id,
                    created_by_user_id=actor_id,
                    assignee_user_id=actor_id,
                    source_run_id=run_id,
                    source_tool_call_id=uuid4(),
                    approval_id=approval.id,
                    title="错误来源",
                    status="OPEN",
                    version=1,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
            assert await session.scalar(select(func.count()).select_from(Todo)) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_citation_database_rejects_version_and_span_mismatch(tmp_path) -> None:
    factory, engine = await file_session_factory(tmp_path / "p8-citation-fk.db")
    try:
        async with factory() as session:
            actor_id, workspace_id, run_id = await seed_run(session)
            document = Document(
                workspace_id=workspace_id,
                created_by_user_id=actor_id,
                title="引用约束文档",
                status="ACTIVE",
                version=1,
            )
            session.add(document)
            await session.flush()
            version_one = DocumentVersion(
                workspace_id=workspace_id,
                document_id=document.id,
                created_by_user_id=actor_id,
                format="TXT",
                mime_type="text/plain",
                file_name="one.txt",
                file_size=3,
                file_sha256="1" * 64,
                object_key="p8/one.txt",
                status="PUBLISHED",
            )
            version_two = DocumentVersion(
                workspace_id=workspace_id,
                document_id=document.id,
                created_by_user_id=actor_id,
                format="TXT",
                mime_type="text/plain",
                file_name="two.txt",
                file_size=3,
                file_sha256="2" * 64,
                object_key="p8/two.txt",
                status="PUBLISHED",
            )
            session.add_all([version_one, version_two])
            await session.flush()
            chunk = DocumentChunk(
                workspace_id=workspace_id,
                document_id=document.id,
                document_version_id=version_one.id,
                kind="CHILD",
                ordinal=1,
                heading_path=[],
                page=1,
                char_start=0,
                char_end=3,
                source_span_id="span-1",
                content_sha256="3" * 64,
                content="正文",
                indexed=True,
            )
            session.add(chunk)
            await session.commit()
            version_one_id = version_one.id
            version_two_id = version_two.id
            chunk_id = chunk.id

            session.add(
                AgentCitation(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    document_version_id=version_one_id,
                    chunk_id=chunk_id,
                    source_span_id="span-1",
                    quote="正文",
                    ordinal=1,
                )
            )
            await session.commit()

            for wrong_version, wrong_span in (
                (version_two_id, "span-1"),
                (version_one_id, "span-tampered"),
            ):
                session.add(
                    AgentCitation(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        document_version_id=wrong_version,
                        chunk_id=chunk_id,
                        source_span_id=wrong_span,
                        quote="伪造引用",
                        ordinal=2,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()
            assert await session.scalar(select(func.count()).select_from(AgentCitation)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_role", "new_status", "expected_code"),
    [
        ("VIEWER", "ACTIVE", "permission_denied"),
        ("EDITOR", "SUSPENDED", "resource_not_found"),
        ("EDITOR", "REMOVED", "resource_not_found"),
    ],
)
async def test_permission_revoked_after_pending_blocks_resolution(
    tmp_path, new_role: str, new_status: str, expected_code: str
) -> None:
    factory, engine = await file_session_factory(tmp_path / f"p8-revoke-{new_role}-{new_status}.db")
    try:
        async with factory() as seed_session:
            actor_id, workspace_id, run_id = await seed_run(seed_session)
            tool_proposal = proposal()
            approval = await create_pending(
                seed_session, actor_id, workspace_id, run_id, tool_proposal
            )
            approval_id = approval.id
        async with factory() as revoke_session:
            await revoke_session.execute(
                update(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == actor_id,
                )
                .values(role=new_role, status=new_status, version=2)
            )
            await revoke_session.commit()
        async with factory() as resolve_session:
            with pytest.raises(AppError) as exc_info:
                await TodoApprovalService(
                    resolve_session, actor_id, workspace_id, clock=lambda: NOW
                ).resolve(
                    approval_id,
                    approval_version=1,
                    proposal=tool_proposal,
                    approved=True,
                )
            assert exc_info.value.code == expected_code
        async with factory() as verify_session:
            current = await AgentRepository(verify_session).get_approval(workspace_id, approval_id)
            assert current is not None and current.status == ApprovalStatus.PENDING.value
            assert await verify_session.scalar(select(func.count()).select_from(Todo)) == 0
            resolve_events = await verify_session.scalar(
                select(func.count())
                .select_from(AgentRunEvent)
                .where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.event_type.in_(["approval.resolved", "todo.created"]),
                )
            )
            todo_audits = await verify_session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.action.in_(["approval.approved", "todo.created"]),
                )
            )
            assert resolve_events == todo_audits == 0
    finally:
        await engine.dispose()


async def assert_pending_transaction_state(
    session: AsyncSession, workspace_id: UUID, run_id: UUID, approval_id: UUID
) -> None:
    session.expire_all()
    approval = await AgentRepository(session).get_approval(workspace_id, approval_id)
    run = await AgentRepository(session).get_run(workspace_id, run_id)
    assert approval is not None and approval.status == ApprovalStatus.PENDING.value
    assert run is not None and run.status == AgentRunStatus.WAITING_APPROVAL.value
    assert await session.scalar(select(func.count()).select_from(Todo)) == 0
    assert (
        await session.scalar(
            select(func.count())
            .select_from(AgentRunEvent)
            .where(
                AgentRunEvent.workspace_id == workspace_id,
                AgentRunEvent.event_type.in_(["approval.resolved", "todo.created"]),
            )
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action.in_(["approval.approved", "todo.created"]),
            )
        )
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["append_event", "add_audit", "commit"])
async def test_approval_side_effect_failure_rolls_back_entire_transaction(
    session_factory, monkeypatch, failure_point: str
) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id
        service = TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW)
        if failure_point == "append_event":
            monkeypatch.setattr(
                service.repo,
                "append_event",
                AsyncMock(side_effect=RuntimeError("event failure")),
            )
        elif failure_point == "add_audit":

            def fail_audit(*_args, **_kwargs) -> None:
                raise RuntimeError("audit failure")

            monkeypatch.setattr("app.services.agent.add_audit", fail_audit)
        else:
            monkeypatch.setattr(
                session,
                "commit",
                AsyncMock(side_effect=RuntimeError("commit failure")),
            )

        with pytest.raises(RuntimeError):
            await service.resolve(
                approval_id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )
        await assert_pending_transaction_state(session, workspace_id, run_id, approval_id)


@pytest.mark.asyncio
async def test_run_cas_conflict_rolls_back_approval_and_todo(session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id
        service = TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW)
        monkeypatch.setattr(service.repo, "cas_run_status", AsyncMock(return_value=False))

        with pytest.raises(AppError) as exc_info:
            await service.resolve(
                approval_id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )
        assert exc_info.value.code == "version_conflict"
        await assert_pending_transaction_state(session, workspace_id, run_id, approval_id)


@pytest.mark.asyncio
async def test_approval_cas_failure_uses_approved_winner_recovery(
    session_factory, monkeypatch
) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id
        service = TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW)
        winner_todo = Todo(
            id=uuid4(),
            workspace_id=workspace_id,
            created_by_user_id=actor_id,
            assignee_user_id=actor_id,
            source_run_id=run_id,
            source_tool_call_id=tool_proposal.tool_call_id,
            approval_id=approval_id,
            title="并发赢家",
            status="OPEN",
            version=1,
        )
        recover = AsyncMock(return_value=(approval, winner_todo))
        monkeypatch.setattr(service.repo, "cas_approval_status", AsyncMock(return_value=False))
        monkeypatch.setattr(service, "_recover_approved", recover)

        returned_approval, returned_todo = await service.resolve(
            approval_id,
            approval_version=1,
            proposal=tool_proposal,
            approved=True,
        )
        assert returned_approval.id == approval_id
        assert returned_todo is not None
        assert returned_todo.id == winner_todo.id
        recover.assert_awaited_once_with(approval_id)


@pytest.mark.asyncio
async def test_commit_integrity_error_recovers_or_fails_closed(
    session_factory, monkeypatch
) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id
        service = TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW)
        monkeypatch.setattr(
            session,
            "commit",
            AsyncMock(side_effect=IntegrityError("commit", {}, RuntimeError("duplicate"))),
        )

        with pytest.raises(AppError) as exc_info:
            await service.resolve(
                approval_id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )
        assert exc_info.value.code == "version_conflict"
        await assert_pending_transaction_state(session, workspace_id, run_id, approval_id)


@pytest.mark.asyncio
async def test_database_deadlock_is_mapped_to_safe_concurrency_error(
    session_factory, monkeypatch
) -> None:
    async with session_factory() as session:
        actor_id, workspace_id, run_id = await seed_run(session)
        tool_proposal = proposal()
        approval = await create_pending(session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id
        service = TodoApprovalService(session, actor_id, workspace_id, clock=lambda: NOW)
        monkeypatch.setattr(
            service.repo,
            "cas_approval_status",
            AsyncMock(side_effect=OperationalError("update", {}, RuntimeError("deadlock"))),
        )

        with pytest.raises(AppError) as exc_info:
            await service.resolve(
                approval_id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )
        assert exc_info.value.code == "approval_concurrency_conflict"
        await assert_pending_transaction_state(session, workspace_id, run_id, approval_id)


def mysql_session_factory() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    assert "charset=utf8mb4" in database_url
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def cleanup_mysql_agent_fixture(
    engine: AsyncEngine, *, workspace_id: UUID, actor_id: UUID
) -> None:
    async with engine.begin() as connection:
        for table_name in (
            "agent_citations",
            "todos",
            "agent_run_events",
            "approval_requests",
            "messages",
            "agent_runs",
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


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL approve/approve 竞态测试",
)
async def test_mysql_concurrent_approve_approve_returns_one_todo() -> None:
    factory, engine = mysql_session_factory()
    async with factory() as seed_session:
        actor_id, workspace_id, run_id = await seed_run(seed_session)
        tool_proposal = proposal()
        approval = await create_pending(seed_session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id

    async def approve_once() -> tuple[ApprovalRequest, Todo | None]:
        async with factory() as session:
            return await TodoApprovalService(
                session,
                actor_id,
                workspace_id,
                clock=lambda: NOW + timedelta(minutes=1),
            ).resolve(
                approval_id,
                approval_version=1,
                proposal=tool_proposal,
                approved=True,
            )

    try:
        first, second = await asyncio.gather(approve_once(), approve_once())
        assert first[1] is not None and second[1] is not None
        assert first[1].id == second[1].id
        async with factory() as verify_session:
            assert await verify_session.scalar(select(func.count()).select_from(Todo)) == 1
    finally:
        await cleanup_mysql_agent_fixture(engine, workspace_id=workspace_id, actor_id=actor_id)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL approve/reject 竞态测试",
)
async def test_mysql_concurrent_approve_reject_has_one_terminal_decision() -> None:
    factory, engine = mysql_session_factory()
    async with factory() as seed_session:
        actor_id, workspace_id, run_id = await seed_run(seed_session)
        tool_proposal = proposal()
        approval = await create_pending(seed_session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id

    async def resolve_once(decision: bool) -> tuple[ApprovalRequest, Todo | None] | AppError:
        async with factory() as session:
            try:
                return await TodoApprovalService(
                    session,
                    actor_id,
                    workspace_id,
                    clock=lambda: NOW + timedelta(minutes=1),
                ).resolve(
                    approval_id,
                    approval_version=1,
                    proposal=tool_proposal,
                    approved=decision,
                )
            except AppError as exc:
                return exc

    try:
        results = await asyncio.gather(resolve_once(True), resolve_once(False))
        assert sum(not isinstance(result, AppError) for result in results) == 1
        async with factory() as verify_session:
            current = await AgentRepository(verify_session).get_approval(workspace_id, approval_id)
            assert current is not None
            todo_count = await verify_session.scalar(select(func.count()).select_from(Todo))
            if current.status == ApprovalStatus.APPROVED.value:
                assert todo_count == 1
            else:
                assert current.status == ApprovalStatus.REJECTED.value
                assert todo_count == 0
    finally:
        await cleanup_mysql_agent_fixture(engine, workspace_id=workspace_id, actor_id=actor_id)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL 撤权/审批双连接测试",
)
async def test_mysql_revocation_committed_by_competing_connection_blocks_approval() -> None:
    factory, engine = mysql_session_factory()
    async with factory() as seed_session:
        actor_id, workspace_id, run_id = await seed_run(seed_session)
        tool_proposal = proposal()
        approval = await create_pending(seed_session, actor_id, workspace_id, run_id, tool_proposal)
        approval_id = approval.id
    revocation_committed = asyncio.Event()

    async def revoke() -> None:
        async with factory() as session:
            await session.execute(
                update(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == actor_id,
                )
                .values(status=MemberStatus.SUSPENDED.value, version=2)
            )
            await session.commit()
            revocation_committed.set()

    async def approve_after_revocation() -> AppError:
        await revocation_committed.wait()
        async with factory() as session:
            with pytest.raises(AppError) as exc_info:
                await TodoApprovalService(
                    session,
                    actor_id,
                    workspace_id,
                    clock=lambda: NOW + timedelta(minutes=1),
                ).resolve(
                    approval_id,
                    approval_version=1,
                    proposal=tool_proposal,
                    approved=True,
                )
            return exc_info.value

    try:
        _, error = await asyncio.gather(revoke(), approve_after_revocation())
        assert error.code == "resource_not_found"
        async with factory() as verify_session:
            current = await AgentRepository(verify_session).get_approval(workspace_id, approval_id)
            assert current is not None and current.status == ApprovalStatus.PENDING.value
            assert await verify_session.scalar(select(func.count()).select_from(Todo)) == 0
    finally:
        await cleanup_mysql_agent_fixture(engine, workspace_id=workspace_id, actor_id=actor_id)
        await engine.dispose()
