from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.checkpoint import (
    TODO_CHECKPOINT_NAMESPACE,
    TODO_HITL_GRAPH_VERSION,
    AsyncSqlAlchemyCheckpointSaver,
    CheckpointCorruptedError,
    CheckpointInUseError,
    WorkspaceCheckpointMaintenanceService,
    checkpoint_config,
)
from app.agent.models import TodoCreateInput, ToolProposal
from app.core.errors import AppError
from app.models.agent import AgentRunStatus, ApprovalStatus
from app.models.base import utc_now
from app.models.entities import MemberRole
from app.repositories.agent import AgentRepository
from app.services.agent import TodoApprovalService
from app.services.agent_checkpoint import AgentCheckpointBindingService
from app.services.workspaces import WorkspaceAccessService, canonical_request_hash


class TodoWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    actor_id: UUID
    conversation_id: UUID
    run_id: UUID
    proposal: ToolProposal
    expires_at: datetime
    short_term_messages: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class ApprovalResumeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    approval_version: int = Field(ge=1)
    approved: bool


class TodoWorkflowState(TypedDict, total=False):
    workspace_id: str
    actor_id: str
    conversation_id: str
    run_id: str
    proposal: dict[str, Any]
    expires_at: str
    short_term_messages: list[dict[str, str]]
    approval_id: str
    approval_version: int
    outcome: Literal["APPROVED", "REJECTED"]
    todo_id: str | None


@dataclass(frozen=True, slots=True)
class AuthorizedResume:
    run_id: UUID
    tool_call_id: UUID
    proposal: ToolProposal
    status: str
    approval_version: int
    todo_id: UUID | None
    checkpoint_ns: str | None
    checkpoint_id: str | None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _contains_approval_id(value: Any, approval_id: UUID) -> bool:
    if isinstance(value, Mapping):
        if str(value.get("approval_id", "")) == str(approval_id):
            return True
        return any(_contains_approval_id(item, approval_id) for item in value.values())
    interrupt_value = getattr(value, "value", None)
    if interrupt_value is not None and interrupt_value is not value:
        return _contains_approval_id(interrupt_value, approval_id)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_approval_id(item, approval_id) for item in value)
    return False


def _interrupt_matches(
    value: Any,
    *,
    approval_id: UUID,
    run_id: UUID,
    tool_call_id: UUID,
    arguments_hash: str,
) -> bool:
    if isinstance(value, Mapping):
        if str(value.get("approval_id", "")) == str(approval_id):
            return (
                str(value.get("run_id", "")) == str(run_id)
                and str(value.get("tool_call_id", "")) == str(tool_call_id)
                and value.get("arguments_hash") == arguments_hash
            )
        return any(
            _interrupt_matches(
                item,
                approval_id=approval_id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                arguments_hash=arguments_hash,
            )
            for item in value.values()
        )
    interrupt_value = getattr(value, "value", None)
    if interrupt_value is not None and interrupt_value is not value:
        return _interrupt_matches(
            interrupt_value,
            approval_id=approval_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            arguments_hash=arguments_hash,
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            _interrupt_matches(
                item,
                approval_id=approval_id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                arguments_hash=arguments_hash,
            )
            for item in value
        )
    return False


def build_todo_hitl_graph(
    session_factory: async_sessionmaker[AsyncSession],
    saver: AsyncSqlAlchemyCheckpointSaver,
    *,
    clock: Callable[[], datetime] = utc_now,
    dispatch_id: UUID | None = None,
    claim_token: str | None = None,
) -> CompiledStateGraph:
    if (dispatch_id is None) != (claim_token is None):
        raise ValueError("dispatch_id and claim_token must be provided together")
    graph = StateGraph(TodoWorkflowState)

    async def approval_step(state: TodoWorkflowState) -> dict[str, Any]:
        workflow = TodoWorkflowInput.model_validate(state)
        now = _aware_utc(clock())
        async with session_factory() as session:
            run = await AgentRepository(session).get_run(workflow.workspace_id, workflow.run_id)
            if run is None or run.conversation_id != workflow.conversation_id:
                raise AppError("resource_not_found", "资源不存在", 404)
            approval = await TodoApprovalService(
                session,
                workflow.actor_id,
                workflow.workspace_id,
                clock=lambda: now,
            ).prepare_approval(
                run_id=workflow.run_id,
                proposal=workflow.proposal,
                expires_at=workflow.expires_at,
                dispatch_id=dispatch_id,
                claim_token=claim_token,
            )

        persisted_proposal = ToolProposal(
            tool_call_id=approval.tool_call_id,
            arguments=TodoCreateInput.model_validate(approval.arguments_json),
            arguments_hash=approval.arguments_hash,
        )
        raw_decision = interrupt(
            {
                "approval_id": str(approval.id),
                "approval_version": approval.version,
                "run_id": str(workflow.run_id),
                "tool_call_id": str(approval.tool_call_id),
                "tool_name": approval.tool_name,
                "arguments_hash": approval.arguments_hash,
                "expires_at": approval.expires_at.isoformat(),
            }
        )
        decision = ApprovalResumeDecision.model_validate(raw_decision)
        if decision.approval_id != approval.id:
            raise AppError("approval_parameters_tampered", "审批标识不一致", 409)

        async with session_factory() as session:
            resolved, todo = await TodoApprovalService(
                session,
                workflow.actor_id,
                workflow.workspace_id,
                clock=lambda: _aware_utc(clock()),
            ).resolve(
                approval.id,
                approval_version=decision.approval_version,
                proposal=persisted_proposal,
                approved=decision.approved,
            )
        return {
            "approval_id": str(resolved.id),
            "approval_version": resolved.version,
            "outcome": "APPROVED" if decision.approved else "REJECTED",
            "todo_id": str(todo.id) if todo is not None else None,
        }

    graph.add_node("approval", approval_step)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=saver)


class TodoApprovalResumeWorker:
    """Minimal in-process resume worker; MySQL remains the only durable fact source."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock

    @staticmethod
    def _checkpoint_namespace(run_id: UUID) -> str:
        return f"{TODO_CHECKPOINT_NAMESPACE}:todo:{run_id}"

    @staticmethod
    def _legacy_run_namespace(run_id: UUID) -> str:
        return f"{TODO_CHECKPOINT_NAMESPACE}:{run_id}"

    def _saver(
        self,
        workspace_id: UUID,
        run_id: UUID,
        *,
        checkpoint_ns: str | None = None,
    ) -> AsyncSqlAlchemyCheckpointSaver:
        return AsyncSqlAlchemyCheckpointSaver(
            self.session_factory,
            workspace_id=workspace_id,
            checkpoint_ns=checkpoint_ns or self._checkpoint_namespace(run_id),
        )

    async def _authorize_start(self, workflow: TodoWorkflowInput) -> None:
        async with self.session_factory() as session:
            await WorkspaceAccessService(session, workflow.actor_id).require(
                workflow.workspace_id,
                minimum_role=MemberRole.EDITOR,
            )
            run = await AgentRepository(session).get_run(workflow.workspace_id, workflow.run_id)
            if (
                run is None
                or run.requested_by_user_id != workflow.actor_id
                or run.conversation_id != workflow.conversation_id
            ):
                raise AppError("resource_not_found", "资源不存在", 404)
            if run.graph_version != TODO_HITL_GRAPH_VERSION:
                raise AppError("checkpoint_binding_conflict", "Checkpoint 图版本不一致", 409)
            if run.route != "TODO_CREATE":
                raise AppError("invalid_run_state", "当前运行不是待办创建路由", 409)
            if run.status not in {
                AgentRunStatus.RUNNING.value,
                AgentRunStatus.WAITING_APPROVAL.value,
            }:
                raise AppError("invalid_run_state", "当前运行不能启动审批", 409)

    async def _authorize_resume(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        decision: ApprovalResumeDecision,
    ) -> AuthorizedResume:
        async with self.session_factory() as session:
            await WorkspaceAccessService(session, actor_id).require(
                workspace_id,
                minimum_role=MemberRole.EDITOR,
            )
            repo = AgentRepository(session)
            approval = await repo.get_approval(workspace_id, decision.approval_id)
            if approval is None or approval.requested_by_user_id != actor_id:
                raise AppError("resource_not_found", "资源不存在", 404)
            run = await repo.get_run(workspace_id, approval.run_id)
            if (
                run is None
                or run.requested_by_user_id != actor_id
                or run.conversation_id != conversation_id
                or run.route != "TODO_CREATE"
            ):
                raise AppError("resource_not_found", "资源不存在", 404)
            if run.graph_version != TODO_HITL_GRAPH_VERSION:
                raise AppError("checkpoint_binding_conflict", "Checkpoint 图版本不一致", 409)
            calculated_hash = canonical_request_hash(approval.arguments_json)
            if approval.tool_name != "todo_create" or approval.arguments_hash != calculated_hash:
                raise AppError("approval_parameters_tampered", "审批参数不一致", 409)
            expected_version = (
                approval.version
                if approval.status == ApprovalStatus.PENDING.value
                else approval.version - 1
            )
            if decision.approval_version != expected_version:
                raise AppError("version_conflict", "审批已被其他操作更新", 409)
            todo_id: UUID | None = None
            if approval.status == ApprovalStatus.APPROVED.value:
                if not decision.approved:
                    raise AppError("approval_already_approved", "审批已批准", 409)
                todo = await repo.get_todo_by_approval(workspace_id, approval.id)
                if (
                    todo is None
                    or todo.source_run_id != run.id
                    or todo.source_tool_call_id != approval.tool_call_id
                ):
                    raise AppError("approval_inconsistent", "已批准审批缺少对应待办", 409)
                todo_id = todo.id
            elif approval.status == ApprovalStatus.REJECTED.value:
                if decision.approved:
                    raise AppError("approval_already_rejected", "审批已被拒绝", 409)
            elif approval.status == ApprovalStatus.EXPIRED.value:
                raise AppError("approval_expired", "审批已过期", 409)
            return AuthorizedResume(
                run_id=run.id,
                tool_call_id=approval.tool_call_id,
                proposal=ToolProposal(
                    tool_call_id=approval.tool_call_id,
                    arguments=TodoCreateInput.model_validate(approval.arguments_json),
                    arguments_hash=approval.arguments_hash,
                ),
                status=approval.status,
                approval_version=approval.version,
                todo_id=todo_id,
                checkpoint_ns=approval.checkpoint_ns,
                checkpoint_id=approval.checkpoint_id,
            )

    async def _find_interrupt_checkpoint_id(
        self,
        saver: AsyncSqlAlchemyCheckpointSaver,
        *,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        actor_id: UUID,
        proposal: ToolProposal,
    ) -> str | None:
        config = checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            checkpoint_ns=saver.checkpoint_ns,
        )
        matches: list[str] = []
        async for checkpoint_tuple in saver.alist(config):
            values = checkpoint_tuple.checkpoint.get("channel_values", {})
            expected_scope = {
                "workspace_id": str(workspace_id),
                "actor_id": str(actor_id),
                "conversation_id": str(conversation_id),
                "run_id": str(run_id),
            }
            if not isinstance(values, Mapping) or any(
                str(values.get(key, "")) != expected for key, expected in expected_scope.items()
            ):
                continue
            try:
                stored_proposal = ToolProposal.model_validate(values.get("proposal"))
            except ValueError:
                continue
            calculated_hash = canonical_request_hash(
                stored_proposal.arguments.model_dump(mode="json")
            )
            if (
                stored_proposal.tool_call_id != proposal.tool_call_id
                or stored_proposal.arguments_hash != proposal.arguments_hash
                or calculated_hash != proposal.arguments_hash
            ):
                continue
            if not checkpoint_tuple.pending_writes or not any(
                channel == "__interrupt__"
                and _interrupt_matches(
                    value,
                    approval_id=approval_id,
                    run_id=run_id,
                    tool_call_id=proposal.tool_call_id,
                    arguments_hash=proposal.arguments_hash,
                )
                for _task_id, channel, value in checkpoint_tuple.pending_writes
            ):
                continue
            configurable = checkpoint_tuple.config.get("configurable", {})
            checkpoint_id = configurable.get("checkpoint_id")
            if checkpoint_id:
                matches.append(str(checkpoint_id))
        if len(matches) > 1:
            raise AppError("checkpoint_ambiguous", "存在多个匹配的待审批 Checkpoint", 409)
        return matches[0] if matches else None

    async def _locate_unbound_interrupt(
        self,
        saver: AsyncSqlAlchemyCheckpointSaver,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        proposal: ToolProposal,
    ) -> tuple[AsyncSqlAlchemyCheckpointSaver, str] | None:
        checkpoint_id = await self._find_interrupt_checkpoint_id(
            saver,
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            run_id=run_id,
            approval_id=approval_id,
            proposal=proposal,
        )
        if checkpoint_id is not None:
            return saver, checkpoint_id
        matches: list[tuple[AsyncSqlAlchemyCheckpointSaver, str]] = []
        for legacy_namespace in (
            self._legacy_run_namespace(run_id),
            TODO_CHECKPOINT_NAMESPACE,
        ):
            if legacy_namespace == saver.checkpoint_ns:
                continue
            legacy_saver = self._saver(
                workspace_id,
                run_id,
                checkpoint_ns=legacy_namespace,
            )
            checkpoint_id = await self._find_interrupt_checkpoint_id(
                legacy_saver,
                workspace_id=workspace_id,
                actor_id=actor_id,
                conversation_id=conversation_id,
                run_id=run_id,
                approval_id=approval_id,
                proposal=proposal,
            )
            if checkpoint_id is not None:
                matches.append((legacy_saver, checkpoint_id))
        if len(matches) > 1:
            raise AppError("checkpoint_ambiguous", "兼容窗口存在多个匹配 Checkpoint", 409)
        return matches[0] if matches else None

    async def _bind_checkpoint(
        self,
        *,
        actor_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        checkpoint_ns: str,
        checkpoint_id: str,
        dispatch_id: UUID | None = None,
        claim_token: str | None = None,
    ) -> None:
        async with self.session_factory() as session:
            await AgentCheckpointBindingService(session, actor_id).publish(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=run_id,
                approval_id=approval_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
                dispatch_id=dispatch_id,
                claim_token=claim_token,
            )

    async def _replay_existing_interrupt(
        self,
        saver: AsyncSqlAlchemyCheckpointSaver,
        *,
        workflow: TodoWorkflowInput,
        dispatch_id: UUID | None = None,
        claim_token: str | None = None,
    ) -> TodoWorkflowState | None:
        async with self.session_factory() as session:
            approval = await AgentRepository(session).get_approval_by_call(
                workflow.workspace_id,
                workflow.run_id,
                workflow.proposal.tool_call_id,
            )
        if approval is None:
            return None
        calculated_hash = canonical_request_hash(
            workflow.proposal.arguments.model_dump(mode="json")
        )
        if (
            approval.tool_name != workflow.proposal.tool_name
            or approval.tool_call_id != workflow.proposal.tool_call_id
            or approval.arguments_hash != calculated_hash
            or workflow.proposal.arguments_hash != calculated_hash
            or canonical_request_hash(approval.arguments_json) != calculated_hash
        ):
            raise AppError("approval_parameters_tampered", "审批参数不一致", 409)
        checkpoint_id = approval.checkpoint_id
        selected_saver = saver
        if approval.checkpoint_ns not in {
            None,
            saver.checkpoint_ns,
            TODO_CHECKPOINT_NAMESPACE,
            self._legacy_run_namespace(workflow.run_id),
        }:
            raise AppError("checkpoint_binding_conflict", "Checkpoint 版本不一致", 409)
        if approval.checkpoint_ns is not None:
            selected_saver = self._saver(
                workflow.workspace_id,
                workflow.run_id,
                checkpoint_ns=approval.checkpoint_ns,
            )
        if checkpoint_id is None:
            located = await self._locate_unbound_interrupt(
                saver,
                workspace_id=workflow.workspace_id,
                actor_id=workflow.actor_id,
                conversation_id=workflow.conversation_id,
                run_id=workflow.run_id,
                approval_id=approval.id,
                proposal=workflow.proposal,
            )
            if located is None:
                return None
            selected_saver, checkpoint_id = located
            await self._bind_checkpoint(
                actor_id=workflow.actor_id,
                workspace_id=workflow.workspace_id,
                conversation_id=workflow.conversation_id,
                run_id=workflow.run_id,
                approval_id=approval.id,
                checkpoint_ns=selected_saver.checkpoint_ns,
                checkpoint_id=checkpoint_id,
                dispatch_id=dispatch_id,
                claim_token=claim_token,
            )
        checkpoint_tuple = await selected_saver.aget_tuple(
            checkpoint_config(
                workspace_id=workflow.workspace_id,
                conversation_id=workflow.conversation_id,
                checkpoint_ns=selected_saver.checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )
        )
        if checkpoint_tuple is None:
            raise AppError("checkpoint_missing", "Checkpoint 不存在，拒绝恢复", 409)
        interrupt_values = [
            value
            for _task_id, channel, value in checkpoint_tuple.pending_writes or []
            if channel == "__interrupt__"
            and _interrupt_matches(
                value,
                approval_id=approval.id,
                run_id=workflow.run_id,
                tool_call_id=workflow.proposal.tool_call_id,
                arguments_hash=workflow.proposal.arguments_hash,
            )
        ]
        if not interrupt_values:
            raise AppError("checkpoint_not_resumable", "Checkpoint 不在待审批状态", 409)
        values = checkpoint_tuple.checkpoint.get("channel_values", {})
        if not isinstance(values, Mapping):
            raise AppError("checkpoint_corrupted", "Checkpoint 状态无效", 409)
        return cast(
            TodoWorkflowState,
            dict(values) | {"__interrupt__": interrupt_values[0]},
        )

    async def start(
        self,
        workflow: TodoWorkflowInput,
        *,
        dispatch_id: UUID | None = None,
        claim_token: str | None = None,
    ) -> TodoWorkflowState:
        if (dispatch_id is None) != (claim_token is None):
            raise ValueError("dispatch_id and claim_token must be provided together")
        await self._authorize_start(workflow)
        # Close the preflight-to-execution revocation window before constructing a
        # saver. The graph node performs the same authorization again before facts.
        await self._authorize_start(workflow)
        saver = self._saver(workflow.workspace_id, workflow.run_id)
        await WorkspaceCheckpointMaintenanceService(
            self.session_factory, workspace_id=workflow.workspace_id
        ).delete_orphan_writes(older_than=_aware_utc(self.clock()) - timedelta(hours=24))
        replayed = await self._replay_existing_interrupt(
            saver,
            workflow=workflow,
            dispatch_id=dispatch_id,
            claim_token=claim_token,
        )
        if replayed is not None:
            return replayed
        graph = build_todo_hitl_graph(
            self.session_factory,
            saver,
            clock=self.clock,
            dispatch_id=dispatch_id,
            claim_token=claim_token,
        )
        config = checkpoint_config(
            workspace_id=workflow.workspace_id,
            conversation_id=workflow.conversation_id,
            checkpoint_ns=saver.checkpoint_ns,
        )
        safe_workflow = workflow.model_copy(update={"short_term_messages": []})
        try:
            result = await graph.ainvoke(
                cast(TodoWorkflowState, safe_workflow.model_dump(mode="json")),
                config,
                durability="sync",
            )
        except AppError as exc:
            if exc.code in {"permission_denied", "resource_not_found"}:
                try:
                    await saver.adelete_unbound_run(
                        thread_id=workflow.conversation_id,
                        run_id=workflow.run_id,
                    )
                except CheckpointInUseError:
                    # A concurrently committed approval/run binding owns the
                    # checkpoint now; never remove its resumable state.
                    pass
            raise
        async with self.session_factory() as session:
            approval = await AgentRepository(session).get_approval_by_call(
                workflow.workspace_id,
                workflow.run_id,
                workflow.proposal.tool_call_id,
            )
        if approval is None:
            raise AppError("approval_inconsistent", "审批事实缺失", 409)
        checkpoint_id = await self._find_interrupt_checkpoint_id(
            saver,
            workspace_id=workflow.workspace_id,
            actor_id=workflow.actor_id,
            conversation_id=workflow.conversation_id,
            run_id=workflow.run_id,
            approval_id=approval.id,
            proposal=workflow.proposal,
        )
        if checkpoint_id is None:
            raise AppError("checkpoint_not_ready", "审批 Checkpoint 尚未就绪", 409)
        await self._bind_checkpoint(
            actor_id=workflow.actor_id,
            workspace_id=workflow.workspace_id,
            conversation_id=workflow.conversation_id,
            run_id=workflow.run_id,
            approval_id=approval.id,
            checkpoint_ns=saver.checkpoint_ns,
            checkpoint_id=checkpoint_id,
            dispatch_id=dispatch_id,
            claim_token=claim_token,
        )
        return cast(TodoWorkflowState, result)

    async def resume(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        decision: ApprovalResumeDecision,
    ) -> TodoWorkflowState:
        authorized = await self._authorize_resume(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            decision=decision,
        )
        if authorized.status == ApprovalStatus.APPROVED.value:
            return TodoWorkflowState(
                workspace_id=str(workspace_id),
                actor_id=str(actor_id),
                conversation_id=str(conversation_id),
                run_id=str(authorized.run_id),
                approval_id=str(decision.approval_id),
                approval_version=authorized.approval_version,
                outcome="APPROVED",
                todo_id=str(authorized.todo_id),
            )
        if authorized.status == ApprovalStatus.REJECTED.value:
            return TodoWorkflowState(
                workspace_id=str(workspace_id),
                actor_id=str(actor_id),
                conversation_id=str(conversation_id),
                run_id=str(authorized.run_id),
                approval_id=str(decision.approval_id),
                approval_version=authorized.approval_version,
                outcome="REJECTED",
                todo_id=None,
            )
        expected_checkpoint_ns = self._checkpoint_namespace(authorized.run_id)
        if authorized.checkpoint_ns not in {
            None,
            TODO_CHECKPOINT_NAMESPACE,
            expected_checkpoint_ns,
            self._legacy_run_namespace(authorized.run_id),
        }:
            raise AppError("checkpoint_binding_conflict", "Checkpoint 版本不一致", 409)
        saver = self._saver(
            workspace_id,
            authorized.run_id,
            checkpoint_ns=authorized.checkpoint_ns or expected_checkpoint_ns,
        )
        await WorkspaceCheckpointMaintenanceService(
            self.session_factory, workspace_id=workspace_id
        ).delete_orphan_writes(older_than=_aware_utc(self.clock()) - timedelta(hours=24))
        checkpoint_id = authorized.checkpoint_id
        if authorized.checkpoint_ns not in {
            None,
            TODO_CHECKPOINT_NAMESPACE,
            expected_checkpoint_ns,
            self._legacy_run_namespace(authorized.run_id),
        }:
            raise AppError("checkpoint_binding_conflict", "Checkpoint 版本不一致", 409)
        if checkpoint_id is None:
            located = await self._locate_unbound_interrupt(
                saver,
                workspace_id=workspace_id,
                actor_id=actor_id,
                conversation_id=conversation_id,
                run_id=authorized.run_id,
                approval_id=decision.approval_id,
                proposal=authorized.proposal,
            )
            if located is None:
                raise AppError("checkpoint_not_ready", "审批 Checkpoint 尚未就绪", 409)
            saver, checkpoint_id = located
            await self._bind_checkpoint(
                actor_id=actor_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=authorized.run_id,
                approval_id=decision.approval_id,
                checkpoint_ns=saver.checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )
        config = checkpoint_config(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            checkpoint_ns=saver.checkpoint_ns,
            checkpoint_id=checkpoint_id,
        )
        try:
            checkpoint_tuple = await saver.aget_tuple(config)
        except CheckpointCorruptedError as exc:
            raise AppError("checkpoint_corrupted", "Checkpoint 已损坏，拒绝恢复", 409) from exc
        if checkpoint_tuple is None:
            raise AppError("checkpoint_missing", "Checkpoint 不存在，拒绝恢复", 409)
        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        if not isinstance(channel_values, Mapping):
            raise AppError("checkpoint_corrupted", "Checkpoint 状态无效", 409)
        expected_scope = {
            "workspace_id": str(workspace_id),
            "actor_id": str(actor_id),
            "conversation_id": str(conversation_id),
        }
        if any(channel_values.get(key) != value for key, value in expected_scope.items()):
            raise AppError("resource_not_found", "资源不存在", 404)
        stored_approval_id = channel_values.get("approval_id")
        if stored_approval_id is not None and str(stored_approval_id) != str(decision.approval_id):
            raise AppError("approval_parameters_tampered", "审批标识不一致", 409)
        if channel_values.get("outcome") in {"APPROVED", "REJECTED"}:
            return cast(TodoWorkflowState, dict(channel_values))
        if not checkpoint_tuple.pending_writes or not any(
            channel == "__interrupt__"
            and _interrupt_matches(
                value,
                approval_id=decision.approval_id,
                run_id=authorized.run_id,
                tool_call_id=authorized.tool_call_id,
                arguments_hash=authorized.proposal.arguments_hash,
            )
            for _task_id, channel, value in checkpoint_tuple.pending_writes
        ):
            raise AppError("checkpoint_not_resumable", "Checkpoint 不在待审批状态", 409)

        if saver.checkpoint_ns == TODO_CHECKPOINT_NAMESPACE:
            # A legacy namespace can contain tasks from several runs. Resolve only
            # from the authenticated facts after the exact checkpoint match above.
            async with self.session_factory() as session:
                resolved, todo = await TodoApprovalService(
                    session,
                    actor_id,
                    workspace_id,
                    clock=lambda: _aware_utc(self.clock()),
                ).resolve(
                    decision.approval_id,
                    approval_version=decision.approval_version,
                    proposal=authorized.proposal,
                    approved=decision.approved,
                )
            return TodoWorkflowState(
                workspace_id=str(workspace_id),
                actor_id=str(actor_id),
                conversation_id=str(conversation_id),
                run_id=str(authorized.run_id),
                approval_id=str(resolved.id),
                approval_version=resolved.version,
                outcome="APPROVED" if decision.approved else "REJECTED",
                todo_id=str(todo.id) if todo is not None else None,
            )

        graph = build_todo_hitl_graph(
            self.session_factory,
            saver,
            clock=self.clock,
        )
        result = await graph.ainvoke(
            Command(resume=decision.model_dump(mode="json")),
            config,
            durability="sync",
        )
        return cast(TodoWorkflowState, result)
