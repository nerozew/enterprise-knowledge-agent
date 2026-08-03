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
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.checkpoint import (
    AGENT_CONTRACT_VERSION,
    TODO_CHECKPOINT_NAMESPACE,
    AsyncSqlAlchemyCheckpointSaver,
    CheckpointCorruptedError,
    CheckpointInUseError,
    CheckpointScopeError,
    WorkspaceCheckpointMaintenanceService,
    checkpoint_config,
)
from app.models.agent import (
    AgentCheckpoint,
    AgentCheckpointWrite,
    AgentRun,
    ApprovalRequest,
    Conversation,
)
from app.models.base import Base
from app.models.entities import User, Workspace


def make_checkpoint(checkpoint_id: str, value: str) -> Checkpoint:
    return {
        "v": 4,
        "id": checkpoint_id,
        "ts": datetime.now(UTC).isoformat(),
        "channel_values": {"state": {"value": value}},
        "channel_versions": {"state": "0001"},
        "versions_seen": {},
        "updated_channels": ["state"],
    }


async def seed_conversations(session_factory) -> tuple[UUID, UUID, UUID, UUID]:
    actor_id = uuid4()
    workspace_id = uuid4()
    first_conversation_id = uuid4()
    second_conversation_id = uuid4()
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=actor_id,
                    email=f"{actor_id}@checkpoint.example",
                    password_hash="unused",
                    display_name="Checkpoint User",
                ),
                Workspace(
                    id=workspace_id,
                    name="Checkpoint Workspace",
                    type="TEAM",
                    status="ACTIVE",
                    version=1,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Conversation(
                    id=first_conversation_id,
                    workspace_id=workspace_id,
                    created_by_user_id=actor_id,
                    status="ACTIVE",
                    version=1,
                ),
                Conversation(
                    id=second_conversation_id,
                    workspace_id=workspace_id,
                    created_by_user_id=actor_id,
                    status="ACTIVE",
                    version=1,
                ),
            ]
        )
        await session.commit()
    return actor_id, workspace_id, first_conversation_id, second_conversation_id


async def file_checkpoint_factory(
    path: Path,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.as_posix()}", connect_args={"timeout": 30}
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
async def test_async_saver_put_get_list_pending_writes_parent_and_reconstruction(
    session_factory,
) -> None:
    _, workspace_id, conversation_id, _ = await seed_conversations(session_factory)
    saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    root_config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    root_id = "00000000-0000-6000-8000-000000000001"
    child_id = "00000000-0000-6000-8000-000000000002"
    metadata = cast(CheckpointMetadata, {"source": "input", "step": -1})

    saved_root = await saver.aput(
        root_config,
        make_checkpoint(root_id, "root"),
        metadata,
        {"state": "0001"},
    )
    await saver.aput_writes(
        saved_root,
        [("result", {"ok": True}), ("__interrupt__", {"approval_id": "a1"})],
        "task-1",
        "approval",
    )
    # Normal writes keep the first value; special negative-index writes are replaceable.
    await saver.aput_writes(
        saved_root,
        [("result", {"ok": False}), ("__interrupt__", {"approval_id": "a2"})],
        "task-1",
        "approval",
    )
    child_metadata = cast(CheckpointMetadata, {"source": "loop", "step": 0})
    saved_child = await saver.aput(
        saved_root,
        make_checkpoint(child_id, "child"),
        child_metadata,
        {"state": "0002"},
    )

    reconstructed = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    latest = await reconstructed.aget_tuple(root_config)
    assert latest is not None
    assert latest.checkpoint["id"] == child_id
    assert latest.parent_config == saved_root
    root = await reconstructed.aget_tuple(saved_root)
    assert root is not None
    assert root.pending_writes == [
        ("task-1", "__interrupt__", {"approval_id": "a2"}),
        ("task-1", "result", {"ok": True}),
    ]
    listed = [item async for item in reconstructed.alist(root_config)]
    assert [item.checkpoint["id"] for item in listed] == [child_id, root_id]
    before = [item async for item in reconstructed.alist(root_config, before=saved_child)]
    assert [item.checkpoint["id"] for item in before] == [root_id]
    filtered = [
        item async for item in reconstructed.alist(root_config, filter={"step": -1}, limit=1)
    ]
    assert [item.checkpoint["id"] for item in filtered] == [root_id]


@pytest.mark.asyncio
async def test_saver_fails_closed_and_does_not_cross_workspace_or_conversation(
    session_factory,
) -> None:
    _, workspace_id, conversation_id, other_conversation_id = await seed_conversations(
        session_factory
    )
    saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    await saver.aput(
        config,
        make_checkpoint("00000000-0000-6000-8000-000000000003", "private"),
        cast(CheckpointMetadata, {"source": "input", "step": -1}),
        {"state": "0001"},
    )

    missing_workspace = cast(
        RunnableConfig,
        {
            "configurable": {
                "thread_id": str(conversation_id),
                "checkpoint_ns": TODO_CHECKPOINT_NAMESPACE,
            }
        },
    )
    with pytest.raises(CheckpointScopeError, match="workspace_id is required"):
        await saver.aget_tuple(missing_workspace)
    with pytest.raises(CheckpointScopeError, match="bound saver"):
        await saver.aget_tuple(
            checkpoint_config(
                workspace_id=uuid4(),
                conversation_id=conversation_id,
            )
        )
    assert (
        await saver.aget_tuple(
            checkpoint_config(
                workspace_id=workspace_id,
                conversation_id=other_conversation_id,
            )
        )
        is None
    )
    with pytest.raises(CheckpointScopeError):
        _ = [item async for item in saver.alist(None)]


@pytest.mark.asyncio
async def test_delete_is_bound_to_workspace_thread_and_namespace(session_factory) -> None:
    _, workspace_id, conversation_id, _ = await seed_conversations(session_factory)
    other_namespace = f"{AGENT_CONTRACT_VERSION}:other-graph-v1"
    primary = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    secondary = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=other_namespace,
    )
    primary_config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    secondary_config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        checkpoint_ns=other_namespace,
    )
    metadata = cast(CheckpointMetadata, {"source": "input", "step": -1})
    primary_saved = await primary.aput(
        primary_config,
        make_checkpoint("00000000-0000-6000-8000-000000000004", "primary"),
        metadata,
        {"state": "0001"},
    )
    await primary.aput_writes(primary_saved, [("result", "pending")], "task-1")
    await secondary.aput(
        secondary_config,
        make_checkpoint("00000000-0000-6000-8000-000000000005", "secondary"),
        metadata,
        {"state": "0001"},
    )

    await primary.adelete_thread(str(conversation_id))
    assert await primary.aget_tuple(primary_config) is None
    assert await secondary.aget_tuple(secondary_config) is not None


@pytest.mark.asyncio
async def test_missing_pending_checkpoint_and_corrupted_payload_fail_closed(
    session_factory,
) -> None:
    _, workspace_id, conversation_id, _ = await seed_conversations(session_factory)
    saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    missing = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        checkpoint_id="00000000-0000-6000-8000-000000000099",
    )
    # LangGraph 4.1.1 can persist pending writes before the related checkpoint
    # row; the scoped composite key keeps those writes isolated until a put.
    await saver.aput_writes(missing, [("result", "value")], "task-1")
    assert await saver.aget_tuple(missing) is None

    saved = await saver.aput(
        config,
        make_checkpoint("00000000-0000-6000-8000-000000000006", "safe"),
        cast(CheckpointMetadata, {"source": "input", "step": -1}),
        {"state": "0001"},
    )
    async with session_factory() as session:
        await session.execute(
            update(AgentCheckpoint)
            .where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.thread_id == conversation_id,
                AgentCheckpoint.checkpoint_ns == TODO_CHECKPOINT_NAMESPACE,
            )
            .values(checkpoint_blob=b"\xc1")
        )
        await session.commit()
    with pytest.raises(CheckpointCorruptedError):
        await saver.aget_tuple(saved)


@pytest.mark.asyncio
async def test_orphan_writes_are_hidden_and_ttl_cleanup_preserves_valid_writes(
    session_factory,
) -> None:
    _, workspace_id, conversation_id, _ = await seed_conversations(session_factory)
    saver = AsyncSqlAlchemyCheckpointSaver(
        session_factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    orphan_id = "00000000-0000-6000-8000-000000000097"
    orphan_config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        checkpoint_id=orphan_id,
    )
    await saver.aput_writes(orphan_config, [("result", "orphan")], "orphan-task")
    valid_config = await saver.aput(
        config,
        make_checkpoint("00000000-0000-6000-8000-000000000098", "valid"),
        cast(CheckpointMetadata, {"source": "input", "step": -1}),
        {"state": "0001"},
    )
    await saver.aput_writes(valid_config, [("result", "valid")], "valid-task")
    async with session_factory() as session:
        await session.execute(
            update(AgentCheckpointWrite)
            .where(
                AgentCheckpointWrite.workspace_id == workspace_id,
                AgentCheckpointWrite.thread_id == conversation_id,
                AgentCheckpointWrite.checkpoint_ns == TODO_CHECKPOINT_NAMESPACE,
                AgentCheckpointWrite.checkpoint_id == orphan_id,
            )
            .values(
                created_at=datetime.now(UTC) - timedelta(days=2),
                updated_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        await session.commit()

    assert await saver.aget_tuple(orphan_config) is None
    assert [item async for item in saver.alist(orphan_config)] == []
    valid = await saver.aget_tuple(valid_config)
    assert valid is not None
    assert valid.pending_writes == [("valid-task", "result", "valid")]

    deleted = await saver.adelete_orphan_writes(older_than=datetime.now(UTC) - timedelta(days=1))
    assert deleted == 1
    async with session_factory() as session:
        remaining = await session.scalar(select(func.count()).select_from(AgentCheckpointWrite))
    assert remaining == 1
    valid_after_cleanup = await saver.aget_tuple(valid_config)
    assert valid_after_cleanup is not None
    assert valid_after_cleanup.pending_writes == [("valid-task", "result", "valid")]


@pytest.mark.asyncio
async def test_sqlite_concurrent_checkpoint_and_write_upserts_are_atomic(tmp_path) -> None:
    factory, engine = await file_checkpoint_factory(tmp_path / "checkpoint-upsert.db")
    try:
        _, workspace_id, conversation_id, _ = await seed_conversations(factory)
        config = checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        checkpoint_id = "00000000-0000-6000-8000-000000000110"

        async def put(value: str) -> RunnableConfig:
            saver = AsyncSqlAlchemyCheckpointSaver(
                factory,
                workspace_id=workspace_id,
                checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
            )
            return await saver.aput(
                config,
                make_checkpoint(checkpoint_id, value),
                cast(CheckpointMetadata, {"source": "input", "step": -1}),
                {"state": "0001"},
            )

        first_config, second_config = await asyncio.gather(put("first"), put("second"))
        assert first_config == second_config
        saver = AsyncSqlAlchemyCheckpointSaver(
            factory,
            workspace_id=workspace_id,
            checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
        )

        async def put_write(value: str) -> None:
            await saver.aput_writes(first_config, [("result", value)], "same-task")

        await asyncio.gather(put_write("first"), put_write("second"))
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(AgentCheckpoint)) == 1
            assert await session.scalar(select(func.count()).select_from(AgentCheckpointWrite)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_orphan_cleanup_rechecks_after_competing_checkpoint_put(
    tmp_path, monkeypatch
) -> None:
    factory, engine = await file_checkpoint_factory(tmp_path / "checkpoint-cleanup-race.db")
    try:
        _, workspace_id, conversation_id, _ = await seed_conversations(factory)
        checkpoint_id = "00000000-0000-6000-8000-000000000111"
        exact_config = checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            checkpoint_id=checkpoint_id,
        )
        cleaner = WorkspaceCheckpointMaintenanceService(factory, workspace_id=workspace_id)
        writer = AsyncSqlAlchemyCheckpointSaver(
            factory,
            workspace_id=workspace_id,
            checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
        )
        await writer.aput_writes(exact_config, [("result", "pending")], "task-1")
        old = datetime.now(UTC) - timedelta(days=2)
        async with factory() as session:
            await session.execute(
                update(AgentCheckpointWrite)
                .where(AgentCheckpointWrite.checkpoint_id == checkpoint_id)
                .values(created_at=old, updated_at=old)
            )
            await session.commit()

        cleanup_ready = asyncio.Event()
        writer_done = asyncio.Event()
        original_lock = cleaner._lock_conversation

        async def delayed_cleanup_lock(session, thread_id) -> bool:
            cleanup_ready.set()
            await writer_done.wait()
            return await original_lock(session, thread_id)

        monkeypatch.setattr(cleaner, "_lock_conversation", delayed_cleanup_lock)

        async def cleanup() -> int:
            return await cleaner.delete_orphan_writes(
                older_than=datetime.now(UTC) - timedelta(days=1),
                checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
            )

        async def put_checkpoint() -> None:
            await cleanup_ready.wait()
            await writer.aput(
                checkpoint_config(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                ),
                make_checkpoint(checkpoint_id, "durable"),
                cast(CheckpointMetadata, {"source": "input", "step": -1}),
                {"state": "0001"},
            )
            writer_done.set()

        deleted, _ = await asyncio.gather(cleanup(), put_checkpoint())
        assert deleted == 0
        loaded = await writer.aget_tuple(exact_config)
        assert loaded is not None
        assert loaded.pending_writes == [("task-1", "result", "pending")]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL CheckpointSaver 集成测试",
)
async def test_mysql_saver_round_trip_pending_writes_and_delete() -> None:
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    assert "charset=utf8mb4" in database_url
    backend_dir = Path(__file__).resolve().parents[1]
    migration_env = os.environ.copy()
    migration_env["DATABASE_URL"] = database_url
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=migration_env,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert migration.returncode == 0, (migration.stdout + migration.stderr).decode(
        "utf-8", errors="replace"
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    actor_id, workspace_id, conversation_id, _ = await seed_conversations(factory)
    saver = AsyncSqlAlchemyCheckpointSaver(
        factory,
        workspace_id=workspace_id,
        checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
    )
    config = checkpoint_config(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    try:
        saved = await saver.aput(
            config,
            make_checkpoint("00000000-0000-6000-8000-000000000101", "mysql"),
            cast(CheckpointMetadata, {"source": "input", "step": -1}),
            {"state": "0001"},
        )
        await saver.aput_writes(saved, [("__interrupt__", {"approval": 1})], "task-1")
        reconstructed = AsyncSqlAlchemyCheckpointSaver(
            factory,
            workspace_id=workspace_id,
            checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
        )
        loaded = await reconstructed.aget_tuple(config)
        assert loaded is not None
        assert loaded.pending_writes == [("task-1", "__interrupt__", {"approval": 1})]

        concurrent_id = "00000000-0000-6000-8000-000000000102"

        async def concurrent_put(value: str) -> RunnableConfig:
            isolated = AsyncSqlAlchemyCheckpointSaver(
                factory,
                workspace_id=workspace_id,
                checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
            )
            return await isolated.aput(
                config,
                make_checkpoint(concurrent_id, value),
                cast(CheckpointMetadata, {"source": "input", "step": -1}),
                {"state": "0001"},
            )

        concurrent_configs = await asyncio.gather(
            concurrent_put("mysql-first"), concurrent_put("mysql-second")
        )

        async def concurrent_write(value: str) -> None:
            isolated = AsyncSqlAlchemyCheckpointSaver(
                factory,
                workspace_id=workspace_id,
                checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
            )
            await isolated.aput_writes(concurrent_configs[0], [("result", value)], "same-task")

        await asyncio.gather(concurrent_write("first"), concurrent_write("second"))
        async with factory() as session:
            checkpoint_count = await session.scalar(
                select(func.count())
                .select_from(AgentCheckpoint)
                .where(
                    AgentCheckpoint.workspace_id == workspace_id,
                    AgentCheckpoint.checkpoint_id == concurrent_id,
                )
            )
            write_count = await session.scalar(
                select(func.count())
                .select_from(AgentCheckpointWrite)
                .where(
                    AgentCheckpointWrite.workspace_id == workspace_id,
                    AgentCheckpointWrite.checkpoint_id == concurrent_id,
                    AgentCheckpointWrite.task_id == "same-task",
                )
            )
        assert checkpoint_count == write_count == 1
        await reconstructed.adelete_thread(str(conversation_id))
        assert await reconstructed.aget_tuple(config) is None

        bound_id = "00000000-0000-6000-8000-000000000103"
        bound_config = await reconstructed.aput(
            config,
            make_checkpoint(bound_id, "mysql-bound"),
            cast(CheckpointMetadata, {"source": "input", "step": -1}),
            {"state": "0001"},
        )
        run_id = uuid4()
        approval_id = uuid4()
        tool_call_id = uuid4()
        async with factory() as session:
            session.add(
                AgentRun(
                    id=run_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    requested_by_user_id=actor_id,
                    status="WAITING_APPROVAL",
                    route="TODO_CREATE",
                    reason_code="ACTION_REQUEST",
                    graph_version="mysql-checkpoint-delete-test",
                    checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
                    checkpoint_id=bound_id,
                    input_text="mysql bound delete",
                    version=1,
                )
            )
            await session.flush()
            session.add(
                ApprovalRequest(
                    id=approval_id,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    requested_by_user_id=actor_id,
                    tool_name="todo_create",
                    arguments_json={"title": "mysql bound"},
                    arguments_hash="0" * 64,
                    checkpoint_ns=TODO_CHECKPOINT_NAMESPACE,
                    checkpoint_id=bound_id,
                    status="PENDING",
                    version=1,
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
            await session.commit()
        with pytest.raises(CheckpointInUseError):
            await reconstructed.adelete_thread(str(conversation_id))
        assert await reconstructed.aget_tuple(bound_config) is not None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM approval_requests WHERE workspace_id=:workspace_id"),
                {"workspace_id": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM agent_runs WHERE workspace_id=:workspace_id"),
                {"workspace_id": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM agent_checkpoint_writes WHERE workspace_id=:workspace_id"),
                {"workspace_id": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM agent_checkpoints WHERE workspace_id=:workspace_id"),
                {"workspace_id": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM conversations WHERE workspace_id=:workspace_id"),
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
