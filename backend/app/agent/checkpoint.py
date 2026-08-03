from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import (
    AgentCheckpoint,
    AgentCheckpointWrite,
    AgentRun,
    ApprovalRequest,
    Conversation,
)
from app.models.base import utc_now

AGENT_CONTRACT_VERSION = "p8-agent-mvp-v1"
TODO_HITL_GRAPH_VERSION = "p8-agent-mvp-v1-b7"
TODO_CHECKPOINT_NAMESPACE = f"{AGENT_CONTRACT_VERSION}:{TODO_HITL_GRAPH_VERSION}"


class CheckpointScopeError(ValueError):
    """The checkpoint request is missing or conflicts with its trusted scope."""


class CheckpointCorruptedError(RuntimeError):
    """A stored checkpoint cannot be safely decoded."""


class CheckpointInUseError(RuntimeError):
    """A checkpoint bound to a durable run/approval cannot be generically deleted."""


@dataclass(frozen=True, slots=True)
class CheckpointScope:
    workspace_id: UUID
    thread_id: UUID
    checkpoint_ns: str
    checkpoint_id: str | None = None


def checkpoint_config(
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    checkpoint_ns: str = TODO_CHECKPOINT_NAMESPACE,
    checkpoint_id: str | None = None,
) -> RunnableConfig:
    configurable: dict[str, Any] = {
        "workspace_id": str(workspace_id),
        "thread_id": str(conversation_id),
        "checkpoint_ns": checkpoint_ns,
        # LangGraph reserves checkpoint_ns and rewrites it to the current
        # subgraph path ("" for the root graph). Preserve the application
        # namespace separately so the saver can still validate its bound
        # contract/graph version on every internal callback.
        "agent_checkpoint_ns": checkpoint_ns,
    }
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


class AsyncSqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """Async LangGraph saver bound to one trusted Workspace and graph namespace.

    LangGraph's ``adelete_thread`` API only accepts a thread ID. Binding the saver
    instance to a Workspace and namespace keeps deletion scoped without relying on
    a globally unique conversation ID. Every config-based method still requires
    explicit matching ``workspace_id``, ``thread_id`` and ``checkpoint_ns`` values.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        workspace_id: UUID,
        checkpoint_ns: str,
    ) -> None:
        if not checkpoint_ns.startswith(f"{AGENT_CONTRACT_VERSION}:"):
            raise CheckpointScopeError(
                "checkpoint_ns must include the frozen contract and graph version"
            )
        if not checkpoint_ns.removeprefix(f"{AGENT_CONTRACT_VERSION}:"):
            raise CheckpointScopeError("checkpoint_ns is missing a graph version")
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.session_factory = session_factory
        self.workspace_id = workspace_id
        self.checkpoint_ns = checkpoint_ns

    def get_next_version(self, current: str | None, channel: None) -> str:
        del channel
        current_version = int(current.split(".", maxsplit=1)[0]) if current else 0
        return f"{current_version + 1:032}.{uuid4().hex}"

    def _scope(self, config: RunnableConfig | None) -> CheckpointScope:
        if not isinstance(config, Mapping):
            raise CheckpointScopeError("checkpoint config is required")
        configurable = config.get("configurable")
        if not isinstance(configurable, Mapping):
            raise CheckpointScopeError("checkpoint configurable scope is required")
        raw_workspace_id = configurable.get("workspace_id")
        raw_thread_id = configurable.get("thread_id")
        raw_namespace = configurable.get("checkpoint_ns")
        application_namespace = configurable.get("agent_checkpoint_ns")
        if raw_workspace_id is None:
            raise CheckpointScopeError("workspace_id is required")
        if raw_thread_id is None:
            raise CheckpointScopeError("thread_id is required")
        if not isinstance(raw_namespace, str):
            raise CheckpointScopeError("checkpoint_ns is required")
        if application_namespace is None:
            application_namespace = raw_namespace
        if not isinstance(application_namespace, str) or not application_namespace:
            raise CheckpointScopeError("checkpoint_ns is required")
        try:
            workspace_id = UUID(str(raw_workspace_id))
            thread_id = UUID(str(raw_thread_id))
        except (TypeError, ValueError) as exc:
            raise CheckpointScopeError("workspace_id and thread_id must be UUIDs") from exc
        if (
            workspace_id != self.workspace_id
            or application_namespace != self.checkpoint_ns
            or raw_namespace not in {"", self.checkpoint_ns}
        ):
            raise CheckpointScopeError("checkpoint scope does not match the bound saver")
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id is not None:
            checkpoint_id = str(checkpoint_id)
            if not checkpoint_id or len(checkpoint_id) > 128:
                raise CheckpointScopeError("checkpoint_id is invalid")
        return CheckpointScope(
            workspace_id=workspace_id,
            thread_id=thread_id,
            checkpoint_ns=application_namespace,
            checkpoint_id=checkpoint_id,
        )

    def _config(self, scope: CheckpointScope, checkpoint_id: str) -> RunnableConfig:
        return checkpoint_config(
            workspace_id=scope.workspace_id,
            conversation_id=scope.thread_id,
            checkpoint_ns=scope.checkpoint_ns,
            checkpoint_id=checkpoint_id,
        )

    def _deserialize(self, value_type: str, value_blob: bytes) -> Any:
        try:
            return self.serde.loads_typed((value_type, value_blob))
        except Exception as exc:
            raise CheckpointCorruptedError("stored checkpoint payload is invalid") from exc

    async def _lock_conversation(self, session: AsyncSession, scope: CheckpointScope) -> None:
        conversation_id = await session.scalar(
            select(Conversation.id)
            .where(
                Conversation.workspace_id == scope.workspace_id,
                Conversation.id == scope.thread_id,
            )
            .with_for_update()
        )
        if conversation_id is None:
            raise CheckpointScopeError("checkpoint conversation does not exist")

    async def _pending_writes(
        self, session: AsyncSession, scope: CheckpointScope, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        rows = list(
            await session.scalars(
                select(AgentCheckpointWrite)
                .where(
                    AgentCheckpointWrite.workspace_id == scope.workspace_id,
                    AgentCheckpointWrite.thread_id == scope.thread_id,
                    AgentCheckpointWrite.checkpoint_ns == scope.checkpoint_ns,
                    AgentCheckpointWrite.checkpoint_id == checkpoint_id,
                )
                .order_by(AgentCheckpointWrite.task_id, AgentCheckpointWrite.write_idx)
            )
        )
        return [
            (
                row.task_id,
                row.channel,
                self._deserialize(row.value_type, row.value_blob),
            )
            for row in rows
        ]

    async def _to_tuple(
        self, session: AsyncSession, scope: CheckpointScope, row: AgentCheckpoint
    ) -> CheckpointTuple:
        checkpoint = cast(Checkpoint, self._deserialize(row.checkpoint_type, row.checkpoint_blob))
        metadata = cast(CheckpointMetadata, self._deserialize(row.metadata_type, row.metadata_blob))
        parent_config = (
            self._config(scope, row.parent_checkpoint_id)
            if row.parent_checkpoint_id is not None
            else None
        )
        return CheckpointTuple(
            config=self._config(scope, row.checkpoint_id),
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=await self._pending_writes(session, scope, row.checkpoint_id),
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        scope = self._scope(config)
        query = select(AgentCheckpoint).where(
            AgentCheckpoint.workspace_id == scope.workspace_id,
            AgentCheckpoint.thread_id == scope.thread_id,
            AgentCheckpoint.checkpoint_ns == scope.checkpoint_ns,
        )
        if scope.checkpoint_id is not None:
            query = query.where(AgentCheckpoint.checkpoint_id == scope.checkpoint_id)
        else:
            query = query.order_by(
                AgentCheckpoint.created_at.desc(), AgentCheckpoint.checkpoint_id.desc()
            ).limit(1)
        async with self.session_factory() as session:
            row = await session.scalar(query)
            if row is None:
                return None
            return await self._to_tuple(session, scope, row)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        scope = self._scope(config)
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return
        before_id: str | None = None
        if before is not None:
            before_scope = self._scope(before)
            if (
                before_scope.workspace_id,
                before_scope.thread_id,
                before_scope.checkpoint_ns,
            ) != (scope.workspace_id, scope.thread_id, scope.checkpoint_ns):
                raise CheckpointScopeError("before config must use the same checkpoint scope")
            before_id = before_scope.checkpoint_id
            if before_id is None:
                raise CheckpointScopeError("before config requires checkpoint_id")
        query = (
            select(AgentCheckpoint)
            .where(
                AgentCheckpoint.workspace_id == scope.workspace_id,
                AgentCheckpoint.thread_id == scope.thread_id,
                AgentCheckpoint.checkpoint_ns == scope.checkpoint_ns,
            )
            .order_by(AgentCheckpoint.created_at.desc(), AgentCheckpoint.checkpoint_id.desc())
        )
        if scope.checkpoint_id is not None:
            query = query.where(AgentCheckpoint.checkpoint_id == scope.checkpoint_id)
        if before_id is not None:
            query = query.where(AgentCheckpoint.checkpoint_id < before_id)
        async with self.session_factory() as session:
            rows = list(await session.scalars(query))
            yielded = 0
            for row in rows:
                checkpoint_tuple = await self._to_tuple(session, scope, row)
                if filter and not all(
                    checkpoint_tuple.metadata.get(key) == value for key, value in filter.items()
                ):
                    continue
                yield checkpoint_tuple
                yielded += 1
                if limit is not None and yielded >= limit:
                    break

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions  # Full checkpoint payload is persisted in this MVP saver.
        scope = self._scope(config)
        checkpoint_id = str(checkpoint.get("id", ""))
        if not checkpoint_id or len(checkpoint_id) > 128:
            raise CheckpointScopeError("checkpoint payload requires a valid id")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        parent_checkpoint_id = scope.checkpoint_id
        async with self.session_factory() as session, session.begin():
            await self._lock_conversation(session, scope)
            now = utc_now()
            await session.execute(
                update(AgentCheckpointWrite)
                .where(
                    AgentCheckpointWrite.workspace_id == scope.workspace_id,
                    AgentCheckpointWrite.thread_id == scope.thread_id,
                    AgentCheckpointWrite.checkpoint_ns == scope.checkpoint_ns,
                    AgentCheckpointWrite.checkpoint_id == checkpoint_id,
                )
                .values(updated_at=now)
            )
            values = {
                "workspace_id": scope.workspace_id,
                "thread_id": scope.thread_id,
                "checkpoint_ns": scope.checkpoint_ns,
                "checkpoint_id": checkpoint_id,
                "parent_checkpoint_id": parent_checkpoint_id,
                "checkpoint_type": checkpoint_type,
                "checkpoint_blob": checkpoint_blob,
                "metadata_type": metadata_type,
                "metadata_blob": metadata_blob,
                "created_at": now,
                "updated_at": now,
            }
            update_values = {
                key: value
                for key, value in values.items()
                if key
                not in {
                    "workspace_id",
                    "thread_id",
                    "checkpoint_ns",
                    "checkpoint_id",
                    "created_at",
                }
            }
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                sqlite_statement = sqlite_insert(AgentCheckpoint).values(**values)
                await session.execute(
                    sqlite_statement.on_conflict_do_update(
                        index_elements=[
                            "workspace_id",
                            "thread_id",
                            "checkpoint_ns",
                            "checkpoint_id",
                        ],
                        set_=update_values,
                    )
                )
            elif dialect == "mysql":
                mysql_statement = mysql_insert(AgentCheckpoint).values(**values)
                await session.execute(mysql_statement.on_duplicate_key_update(**update_values))
            else:
                await session.merge(AgentCheckpoint(**values))
        return self._config(scope, checkpoint_id)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        scope = self._scope(config)
        if scope.checkpoint_id is None:
            raise CheckpointScopeError("checkpoint_id is required for pending writes")
        if not task_id or len(task_id) > 128 or len(task_path) > 512:
            raise CheckpointScopeError("pending write task identity is invalid")
        async with self.session_factory() as session, session.begin():
            await self._lock_conversation(session, scope)
            checkpoint_key = (
                scope.workspace_id,
                scope.thread_id,
                scope.checkpoint_ns,
                scope.checkpoint_id,
            )
            for index, (channel, value) in enumerate(writes):
                if not channel or len(channel) > 255:
                    raise CheckpointScopeError("pending write channel is invalid")
                write_idx = WRITES_IDX_MAP.get(channel, index)
                key = checkpoint_key + (task_id, write_idx)
                value_type, value_blob = self.serde.dumps_typed(value)
                now = utc_now()
                values = {
                    "workspace_id": scope.workspace_id,
                    "thread_id": scope.thread_id,
                    "checkpoint_ns": scope.checkpoint_ns,
                    "checkpoint_id": scope.checkpoint_id,
                    "task_id": task_id,
                    "write_idx": write_idx,
                    "task_path": task_path,
                    "channel": channel,
                    "value_type": value_type,
                    "value_blob": value_blob,
                    "created_at": now,
                    "updated_at": now,
                }
                dialect = session.get_bind().dialect.name
                if dialect == "sqlite":
                    sqlite_write = sqlite_insert(AgentCheckpointWrite).values(**values)
                    if write_idx < 0:
                        sqlite_write = sqlite_write.on_conflict_do_update(
                            index_elements=[
                                "workspace_id",
                                "thread_id",
                                "checkpoint_ns",
                                "checkpoint_id",
                                "task_id",
                                "write_idx",
                            ],
                            set_={
                                "task_path": task_path,
                                "channel": channel,
                                "value_type": value_type,
                                "value_blob": value_blob,
                                "updated_at": now,
                            },
                        )
                    else:
                        sqlite_write = sqlite_write.on_conflict_do_nothing(
                            index_elements=[
                                "workspace_id",
                                "thread_id",
                                "checkpoint_ns",
                                "checkpoint_id",
                                "task_id",
                                "write_idx",
                            ]
                        )
                    await session.execute(sqlite_write)
                elif dialect == "mysql":
                    mysql_write = mysql_insert(AgentCheckpointWrite).values(**values)
                    if write_idx < 0:
                        mysql_write = mysql_write.on_duplicate_key_update(
                            task_path=task_path,
                            channel=channel,
                            value_type=value_type,
                            value_blob=value_blob,
                            updated_at=now,
                        )
                    else:
                        mysql_write = mysql_write.on_duplicate_key_update(
                            write_idx=AgentCheckpointWrite.write_idx
                        )
                    await session.execute(mysql_write)
                else:
                    existing = await session.get(AgentCheckpointWrite, key)
                    if existing is None:
                        session.add(AgentCheckpointWrite(**values))
                    elif write_idx < 0:
                        existing.task_path = task_path
                        existing.channel = channel
                        existing.value_type = value_type
                        existing.value_blob = value_blob
                        existing.updated_at = now

    async def adelete_thread(self, thread_id: str) -> None:
        try:
            parsed_thread_id = UUID(thread_id)
        except (TypeError, ValueError) as exc:
            raise CheckpointScopeError("thread_id must be a conversation UUID") from exc
        try:
            async with self.session_factory() as session, session.begin():
                await self._lock_conversation(
                    session,
                    CheckpointScope(
                        workspace_id=self.workspace_id,
                        thread_id=parsed_thread_id,
                        checkpoint_ns=self.checkpoint_ns,
                    ),
                )
                bound_run = await session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.workspace_id == self.workspace_id,
                        AgentRun.conversation_id == parsed_thread_id,
                        AgentRun.checkpoint_ns == self.checkpoint_ns,
                        AgentRun.checkpoint_id.is_not(None),
                    )
                )
                bound_approval = await session.scalar(
                    select(ApprovalRequest.id).where(
                        ApprovalRequest.workspace_id == self.workspace_id,
                        ApprovalRequest.checkpoint_ns == self.checkpoint_ns,
                        ApprovalRequest.checkpoint_id.is_not(None),
                        ApprovalRequest.run_id.in_(
                            select(AgentRun.id).where(
                                AgentRun.workspace_id == self.workspace_id,
                                AgentRun.conversation_id == parsed_thread_id,
                            )
                        ),
                    )
                )
                if bound_run is not None or bound_approval is not None:
                    raise CheckpointInUseError(
                        "checkpoint namespace is bound to a durable run or approval"
                    )
                scope_filter = (
                    AgentCheckpointWrite.workspace_id == self.workspace_id,
                    AgentCheckpointWrite.thread_id == parsed_thread_id,
                    AgentCheckpointWrite.checkpoint_ns == self.checkpoint_ns,
                )
                await session.execute(delete(AgentCheckpointWrite).where(*scope_filter))
                await session.execute(
                    delete(AgentCheckpoint).where(
                        AgentCheckpoint.workspace_id == self.workspace_id,
                        AgentCheckpoint.thread_id == parsed_thread_id,
                        AgentCheckpoint.checkpoint_ns == self.checkpoint_ns,
                    )
                )
        except IntegrityError as exc:
            raise CheckpointInUseError(
                "checkpoint namespace became bound while deletion was in progress"
            ) from exc

    async def adelete_unbound_run(self, *, thread_id: UUID, run_id: UUID) -> None:
        """Remove a failed start's namespace only while no durable approval can use it."""

        try:
            async with self.session_factory() as session, session.begin():
                scope = CheckpointScope(
                    workspace_id=self.workspace_id,
                    thread_id=thread_id,
                    checkpoint_ns=self.checkpoint_ns,
                )
                await self._lock_conversation(session, scope)
                run = await session.scalar(
                    select(AgentRun)
                    .where(
                        AgentRun.workspace_id == self.workspace_id,
                        AgentRun.id == run_id,
                        AgentRun.conversation_id == thread_id,
                    )
                    .with_for_update()
                )
                approval_id = await session.scalar(
                    select(ApprovalRequest.id).where(
                        ApprovalRequest.workspace_id == self.workspace_id,
                        ApprovalRequest.run_id == run_id,
                    )
                )
                bound_run_id = await session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.workspace_id == self.workspace_id,
                        AgentRun.conversation_id == thread_id,
                        AgentRun.checkpoint_ns == self.checkpoint_ns,
                        AgentRun.checkpoint_id.is_not(None),
                    )
                )
                if (
                    run is None
                    or run.status != "RUNNING"
                    or run.checkpoint_ns is not None
                    or run.checkpoint_id is not None
                    or approval_id is not None
                    or bound_run_id is not None
                ):
                    raise CheckpointInUseError(
                        "failed-start checkpoint is already owned by durable facts"
                    )
                await session.execute(
                    delete(AgentCheckpointWrite).where(
                        AgentCheckpointWrite.workspace_id == self.workspace_id,
                        AgentCheckpointWrite.thread_id == thread_id,
                        AgentCheckpointWrite.checkpoint_ns == self.checkpoint_ns,
                    )
                )
                await session.execute(
                    delete(AgentCheckpoint).where(
                        AgentCheckpoint.workspace_id == self.workspace_id,
                        AgentCheckpoint.thread_id == thread_id,
                        AgentCheckpoint.checkpoint_ns == self.checkpoint_ns,
                    )
                )
        except IntegrityError as exc:
            raise CheckpointInUseError(
                "failed-start checkpoint became owned during cleanup"
            ) from exc

    async def adelete_orphan_writes(self, *, older_than: datetime) -> int:
        """Delete TTL-expired writes whose checkpoint never became durable."""

        return await WorkspaceCheckpointMaintenanceService(
            self.session_factory, workspace_id=self.workspace_id
        ).delete_orphan_writes(older_than=older_than, checkpoint_ns=self.checkpoint_ns)


class WorkspaceCheckpointMaintenanceService:
    """Workspace-scoped orphan cleanup reusable by workers and future schedulers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        workspace_id: UUID,
    ) -> None:
        self.session_factory = session_factory
        self.workspace_id = workspace_id

    async def _lock_conversation(self, session: AsyncSession, thread_id: UUID) -> bool:
        conversation = await session.scalar(
            select(Conversation.id)
            .where(
                Conversation.workspace_id == self.workspace_id,
                Conversation.id == thread_id,
            )
            .with_for_update()
        )
        return conversation is not None

    async def delete_orphan_writes(
        self,
        *,
        older_than: datetime,
        checkpoint_ns: str | None = None,
    ) -> int:
        if older_than.tzinfo is None:
            raise ValueError("older_than must include a timezone")
        cutoff = older_than.astimezone(UTC)
        checkpoint_exists = (
            select(AgentCheckpoint.checkpoint_id)
            .where(
                AgentCheckpoint.workspace_id == AgentCheckpointWrite.workspace_id,
                AgentCheckpoint.thread_id == AgentCheckpointWrite.thread_id,
                AgentCheckpoint.checkpoint_ns == AgentCheckpointWrite.checkpoint_ns,
                AgentCheckpoint.checkpoint_id == AgentCheckpointWrite.checkpoint_id,
            )
            .exists()
        )
        async with self.session_factory() as session:
            candidate_query = select(
                AgentCheckpointWrite.thread_id,
                AgentCheckpointWrite.checkpoint_ns,
                AgentCheckpointWrite.checkpoint_id,
            ).where(
                AgentCheckpointWrite.workspace_id == self.workspace_id,
                AgentCheckpointWrite.created_at < cutoff,
                AgentCheckpointWrite.updated_at < cutoff,
                ~checkpoint_exists,
            )
            if checkpoint_ns is not None:
                candidate_query = candidate_query.where(
                    AgentCheckpointWrite.checkpoint_ns == checkpoint_ns
                )
            candidates = list(
                await session.execute(
                    candidate_query.distinct().order_by(
                        AgentCheckpointWrite.thread_id,
                        AgentCheckpointWrite.checkpoint_ns,
                        AgentCheckpointWrite.checkpoint_id,
                    )
                )
            )
        deleted = 0
        for thread_id, candidate_ns, checkpoint_id in candidates:
            async with self.session_factory() as session, session.begin():
                if not await self._lock_conversation(session, thread_id):
                    continue
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        delete(AgentCheckpointWrite).where(
                            AgentCheckpointWrite.workspace_id == self.workspace_id,
                            AgentCheckpointWrite.thread_id == thread_id,
                            AgentCheckpointWrite.checkpoint_ns == candidate_ns,
                            AgentCheckpointWrite.checkpoint_id == checkpoint_id,
                            AgentCheckpointWrite.created_at < cutoff,
                            AgentCheckpointWrite.updated_at < cutoff,
                            ~checkpoint_exists,
                        )
                    ),
                )
                deleted += result.rowcount
        return deleted
