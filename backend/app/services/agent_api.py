from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.hitl import ApprovalResumeDecision, TodoApprovalResumeWorker
from app.agent.models import AGENT_GRAPH_VERSION, TodoCreateInput, ToolProposal
from app.core.errors import AppError
from app.models.agent import (
    AgentMessageRecord,
    AgentRun,
    AgentRunDispatch,
    AgentRunEvent,
    AgentRunStatus,
    ApprovalRequest,
    Conversation,
    ConversationStatus,
    Todo,
)
from app.models.base import utc_now
from app.models.entities import IdempotencyRecord, MemberRole
from app.repositories.agent import AgentRepository
from app.repositories.workspaces import IdempotencyRepository
from app.schemas.agent import (
    AgentCitationSummary,
    AgentMessageSummary,
    AgentRefusal,
    AgentRunCancel,
    AgentRunCreate,
    AgentRunSnapshot,
    AgentRunSummary,
    ApprovalDecision,
    ApprovalDecisionResult,
    ApprovalSummary,
    ConversationCreate,
    ConversationPatch,
    ConversationSummary,
    OperationAccepted,
    TodoPatch,
    TodoSummary,
    ToolProposalSummary,
)
from app.services.workspaces import (
    WorkspaceAccessService,
    add_audit,
    canonical_request_hash,
    hash_idempotency_key,
    validate_idempotency_key,
)

TERMINAL_RUN_STATUSES = {
    AgentRunStatus.COMPLETED.value,
    AgentRunStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value,
}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def conversation_summary(value: Conversation) -> ConversationSummary:
    return ConversationSummary.model_validate(value)


def run_summary(value: AgentRun) -> AgentRunSummary:
    return AgentRunSummary.model_validate(value)


def approval_summary(value: ApprovalRequest) -> ApprovalSummary:
    return ApprovalSummary(
        id=value.id,
        workspace_id=value.workspace_id,
        run_id=value.run_id,
        tool_call_id=value.tool_call_id,
        requested_by_user_id=value.requested_by_user_id,
        tool_name=value.tool_name,
        arguments=value.arguments_json,
        tool_args_hash=value.arguments_hash,
        status=value.status,
        version=value.version,
        expires_at=value.expires_at,
        resolved_at=value.resolved_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def todo_summary(value: Todo) -> TodoSummary:
    return TodoSummary(
        id=value.id,
        workspace_id=value.workspace_id,
        title=value.title,
        description=value.description,
        status=value.status,
        due_at=value.due_at,
        assignee_id=value.assignee_user_id,
        source_run_id=value.source_run_id,
        approval_id=value.approval_id,
        version=value.version,
        completed_at=value.completed_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


class AgentApiService:
    """Workspace-scoped REST application service for P8 B8."""

    def __init__(
        self,
        session: AsyncSession,
        actor_id: UUID,
        *,
        request_id: str | None = None,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.request_id = request_id
        self.access = WorkspaceAccessService(session, actor_id)
        self.repo = AgentRepository(session)
        self.idempotency = IdempotencyRepository(session)

    async def _replay(
        self,
        workspace_id: UUID,
        *,
        scope: str,
        idempotency_key: str | None,
        request_hash: str,
    ) -> tuple[str, IdempotencyRecord | None]:
        key = validate_idempotency_key(idempotency_key)
        record = await self.idempotency.get(
            workspace_id,
            self.actor_id,
            scope,
            hash_idempotency_key(key),
        )
        if record is not None and record.request_hash != request_hash:
            raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
        return key, record

    def _record(
        self,
        workspace_id: UUID,
        *,
        scope: str,
        key: str,
        request_hash: str,
        response_status: int,
        response: dict[str, Any],
        resource_type: str,
        resource_id: UUID,
    ) -> None:
        self.idempotency.add(
            IdempotencyRecord(
                workspace_id=workspace_id,
                actor_id=self.actor_id,
                scope=scope,
                key_hash=hash_idempotency_key(key),
                request_hash=request_hash,
                response_status=response_status,
                response_json=response,
                resource_type=resource_type,
                resource_id=resource_id,
                expires_at=utc_now() + timedelta(days=7),
            )
        )

    async def create_conversation(
        self,
        workspace_id: UUID,
        payload: ConversationCreate,
        *,
        idempotency_key: str | None,
    ) -> ConversationSummary:
        _, member = await self.access.require(
            workspace_id
        )
        scope = "agent.conversation.create"
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        key, replay = await self._replay(
            workspace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None and replay.response_json is not None:
            return ConversationSummary.model_validate(replay.response_json)
        conversation = Conversation(
            workspace_id=workspace_id,
            created_by_user_id=self.actor_id,
            title=payload.title,
            status=ConversationStatus.ACTIVE.value,
            version=1,
        )
        self.repo.add_conversation(conversation)
        await self.session.flush()
        response = conversation_summary(conversation)
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="agent.conversation.created",
            target_type="conversation",
            target_id=conversation.id,
            request_id=self.request_id,
            metadata={"version": 1},
        )
        self._record(
            workspace_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=201,
            response=response.model_dump(mode="json"),
            resource_type="conversation",
            resource_id=conversation.id,
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.idempotency.get(
                workspace_id, self.actor_id, scope, hash_idempotency_key(key)
            )
            if winner is not None and winner.request_hash == request_hash and winner.response_json:
                return ConversationSummary.model_validate(winner.response_json)
            raise AppError("idempotency_conflict", "会话创建并发冲突", 409) from exc
        return response

    async def list_conversations(
        self,
        workspace_id: UUID,
        *,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[ConversationSummary], int]:
        await self.access.require(workspace_id)
        rows, total = await self.repo.list_conversations(
            workspace_id,
            actor_id=self.actor_id,
            page=page,
            page_size=page_size,
            status=status,
        )
        return [conversation_summary(row) for row in rows], total

    async def get_conversation(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> ConversationSummary:
        await self.access.require(workspace_id)
        conversation = await self.repo.get_conversation(workspace_id, conversation_id)
        if conversation is None or conversation.created_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        return conversation_summary(conversation)

    async def update_conversation(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        payload: ConversationPatch,
        *,
        idempotency_key: str | None,
    ) -> ConversationSummary:
        _, member = await self.access.require(workspace_id)
        scope = f"agent.conversation.update:{conversation_id}"
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        key, replay = await self._replay(
            workspace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None and replay.response_json is not None:
            return ConversationSummary.model_validate(replay.response_json)
        conversation = await self.repo.get_conversation(workspace_id, conversation_id)
        if conversation is None or conversation.created_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        values: dict[str, Any] = {
            "version": Conversation.version + 1,
            "updated_at": utc_now(),
        }
        if payload.title is not None:
            values["title"] = payload.title
        if payload.status is not None:
            values["status"] = payload.status
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.id == conversation_id,
                    Conversation.version == payload.version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise AppError("version_conflict", "会话已被其他操作更新", 409)
        await self.session.flush()
        refreshed = await self.repo.get_conversation(workspace_id, conversation_id)
        assert refreshed is not None
        response = conversation_summary(refreshed)
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="agent.conversation.updated",
            target_type="conversation",
            target_id=conversation_id,
            request_id=self.request_id,
            metadata={"from_version": payload.version, "status": response.status},
        )
        self._record(
            workspace_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=200,
            response=response.model_dump(mode="json"),
            resource_type="conversation",
            resource_id=conversation_id,
        )
        await self.session.commit()
        return response

    async def create_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        payload: AgentRunCreate,
        *,
        idempotency_key: str | None,
    ) -> OperationAccepted:
        _, member = await self.access.require(workspace_id)
        if payload.retrieval_strategy in {"LOCAL", "GLOBAL"}:
            raise AppError(
                "retrieval_strategy_not_available",
                "当前 P8 单 Agent MVP 尚未启用 GraphRAG Local/Global",
                422,
            )
        scope = f"agent.run.create:{conversation_id}"
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        key, replay = await self._replay(
            workspace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None and replay.response_json is not None:
            return OperationAccepted.model_validate(replay.response_json)
        conversation = await self.repo.get_conversation(
            workspace_id, conversation_id, for_update=True
        )
        if conversation is None or conversation.created_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        if conversation.status != ConversationStatus.ACTIVE.value:
            raise AppError("conversation_archived", "归档会话不能创建 Run", 409)
        if conversation.version != payload.conversation_version:
            raise AppError("version_conflict", "会话已被其他操作更新", 409)
        active = await self.repo.get_active_run_for_conversation(workspace_id, conversation_id)
        if active is not None:
            raise AppError("active_run_exists", "该会话已有未结束的 Run", 409)

        now = utc_now()
        run = AgentRun(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            requested_by_user_id=self.actor_id,
            status=AgentRunStatus.RUNNING.value,
            graph_version=AGENT_GRAPH_VERSION,
            input_text=payload.message,
            version=1,
            started_at=now,
        )
        self.repo.add_run(run)
        await self.session.flush()
        self.repo.add_dispatch(
            AgentRunDispatch(
                workspace_id=workspace_id,
                run_id=run.id,
                status="PENDING",
                attempts=0,
                available_at=now,
            )
        )
        message = AgentMessageRecord(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run.id,
            author_user_id=self.actor_id,
            role="USER",
            content=payload.message,
            sequence_no=await self.repo.next_message_sequence(workspace_id, conversation_id),
            version=1,
        )
        self.repo.add_message(message)
        conversation_update = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.id == conversation_id,
                    Conversation.version == payload.conversation_version,
                )
                .values(
                    version=Conversation.version + 1,
                    last_message_at=now,
                    updated_at=now,
                )
            ),
        )
        if conversation_update.rowcount != 1:
            await self.session.rollback()
            raise AppError("version_conflict", "会话已被其他操作更新", 409)
        await self.repo.append_event(
            workspace_id,
            run.id,
            "run.created",
            {
                "conversation_id": str(conversation_id),
                "status": run.status,
                "retrieval_strategy": payload.retrieval_strategy,
                "knowledge_base_ids": [str(value) for value in payload.knowledge_base_ids],
                "budget": payload.budget.model_dump(mode="json"),
            },
        )
        response = OperationAccepted(
            operation_id=run.id,
            resource_type="agent_run",
            resource_id=run.id,
            status="RUNNING",
            status_url=f"/api/v1/workspaces/{workspace_id}/agent-runs/{run.id}",
            events_url=f"/api/v1/workspaces/{workspace_id}/agent-runs/{run.id}/events",
            created_at=run.created_at,
        )
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="agent.run.created",
            target_type="agent_run",
            target_id=run.id,
            request_id=self.request_id,
            metadata={"conversation_id": str(conversation_id), "version": 1},
        )
        self._record(
            workspace_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=202,
            response=response.model_dump(mode="json"),
            resource_type="agent_run",
            resource_id=run.id,
        )
        try:
            await self.session.commit()
        except (IntegrityError, OperationalError) as exc:
            await self.session.rollback()
            winner = await self.idempotency.get(
                workspace_id, self.actor_id, scope, hash_idempotency_key(key)
            )
            if winner is not None and winner.request_hash == request_hash and winner.response_json:
                return OperationAccepted.model_validate(winner.response_json)
            raise AppError("agent_run_concurrency_conflict", "Run 创建并发冲突", 409) from exc
        return response

    async def get_run(self, workspace_id: UUID, run_id: UUID) -> AgentRunSnapshot:
        await self.access.require(workspace_id)
        run = await self.repo.get_run(workspace_id, run_id)
        if run is None or run.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        return await self._snapshot(run)

    async def _snapshot(self, run: AgentRun) -> AgentRunSnapshot:
        messages = await self.repo.list_run_messages(run.workspace_id, run.id)
        approval = await self.repo.get_approval_for_run(run.workspace_id, run.id)
        todo = (
            await self.repo.get_todo_by_approval(run.workspace_id, approval.id)
            if approval is not None
            else None
        )
        citations = await self.repo.list_citation_details(run.workspace_id, run.id)
        _first, last_sequence = await self.repo.event_bounds(run.workspace_id, run.id)
        assistant_messages = [message for message in messages if message.role == "ASSISTANT"]
        proposal = None
        if approval is not None:
            proposal = ToolProposalSummary(
                tool_call_id=approval.tool_call_id,
                tool_name="todo_create",
                arguments=approval.arguments_json,
                tool_args_hash=approval.arguments_hash,
                impact_scope="在当前 Workspace 创建一个待办",
            )
        return AgentRunSnapshot(
            run=run_summary(run),
            messages=[AgentMessageSummary.model_validate(message) for message in messages],
            final_answer=assistant_messages[-1].content if assistant_messages else None,
            refusal=(
                AgentRefusal(code=run.error_code, message="Agent 未能完成本次请求")
                if run.error_code is not None
                else None
            ),
            citations=[
                AgentCitationSummary(
                    id=citation.id,
                    document_id=chunk.document_id,
                    document_version_id=citation.document_version_id,
                    document_title=document.title,
                    page=chunk.page,
                    heading_path=chunk.heading_path,
                    quote=citation.quote,
                    source_span_id=citation.source_span_id,
                    ordinal=citation.ordinal,
                )
                for citation, chunk, document in citations
            ],
            inspiration_hits=[],
            tool_proposal=proposal,
            approval=approval_summary(approval) if approval is not None else None,
            todo=todo_summary(todo) if todo is not None else None,
            last_event_id=str(last_sequence) if last_sequence is not None else None,
            last_sequence_no=last_sequence or 0,
        )

    async def cancel_run(
        self,
        workspace_id: UUID,
        run_id: UUID,
        payload: AgentRunCancel,
        *,
        idempotency_key: str | None,
    ) -> AgentRunSnapshot:
        _, member = await self.access.require(workspace_id)
        scope = f"agent.run.cancel:{run_id}"
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        key, replay = await self._replay(
            workspace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None and replay.response_json is not None:
            return AgentRunSnapshot.model_validate(replay.response_json)
        run = await self.repo.get_run(workspace_id, run_id, for_update=True)
        if run is None or run.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        if run.status in TERMINAL_RUN_STATUSES:
            raise AppError("invalid_run_state", "终态 Run 不能取消", 409)
        if run.version != payload.version:
            raise AppError("version_conflict", "Run 已被其他操作更新", 409)
        now = utc_now()
        if not await self.repo.cas_run_status(
            workspace_id,
            run_id,
            expected_status=run.status,
            expected_version=run.version,
            new_status=AgentRunStatus.CANCELLED.value,
            finished_at=now,
        ):
            await self.session.rollback()
            raise AppError("version_conflict", "Run 已被其他操作更新", 409)
        await self.repo.append_event(
            workspace_id,
            run_id,
            "run.cancelled",
            {"reason": payload.reason, "from_version": payload.version},
        )
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action="agent.run.cancelled",
            target_type="agent_run",
            target_id=run_id,
            request_id=self.request_id,
            metadata={"from_version": payload.version},
        )
        await self.session.flush()
        refreshed = await self.repo.get_run(workspace_id, run_id)
        assert refreshed is not None
        response = await self._snapshot(refreshed)
        self._record(
            workspace_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=200,
            response=response.model_dump(mode="json"),
            resource_type="agent_run",
            resource_id=run_id,
        )
        await self.session.commit()
        return response

    async def list_approvals(
        self,
        workspace_id: UUID,
        *,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[ApprovalSummary], int]:
        await self.access.require(workspace_id)
        rows, total = await self.repo.list_approvals(
            workspace_id,
            requested_by_user_id=self.actor_id,
            page=page,
            page_size=page_size,
            status=status,
        )
        return [approval_summary(row) for row in rows], total

    async def get_approval(self, workspace_id: UUID, approval_id: UUID) -> ApprovalSummary:
        await self.access.require(workspace_id)
        approval = await self.repo.get_approval(workspace_id, approval_id)
        if approval is None or approval.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        return approval_summary(approval)

    async def list_todos(
        self,
        workspace_id: UUID,
        *,
        page: int,
        page_size: int,
        status: str | None,
        assignee_id: UUID | None,
    ) -> tuple[list[TodoSummary], int]:
        await self.access.require(workspace_id)
        rows, total = await self.repo.list_todos(
            workspace_id,
            actor_id=self.actor_id,
            page=page,
            page_size=page_size,
            status=status,
            assignee_id=assignee_id,
        )
        return [todo_summary(row) for row in rows], total

    async def get_todo(self, workspace_id: UUID, todo_id: UUID) -> TodoSummary:
        await self.access.require(workspace_id)
        todo = await self.repo.get_todo(workspace_id, todo_id)
        if todo is None or self.actor_id not in {
            todo.assignee_user_id,
            todo.created_by_user_id,
        }:
            raise AppError("resource_not_found", "资源不存在", 404)
        return todo_summary(todo)

    async def update_todo(
        self,
        workspace_id: UUID,
        todo_id: UUID,
        payload: TodoPatch,
        *,
        idempotency_key: str | None,
    ) -> TodoSummary:
        _, member = await self.access.require(
            workspace_id, minimum_role=MemberRole.EDITOR
        )
        scope = f"agent.todo.update:{todo_id}"
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        key, replay = await self._replay(
            workspace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None and replay.response_json is not None:
            return TodoSummary.model_validate(replay.response_json)
        todo = await self.repo.get_todo(workspace_id, todo_id)
        if todo is None or self.actor_id not in {
            todo.assignee_user_id,
            todo.created_by_user_id,
        }:
            raise AppError("resource_not_found", "资源不存在", 404)
        if todo.version != payload.version:
            raise AppError("version_conflict", "待办已被其他操作更新", 409)
        completed_at = utc_now() if payload.status == "COMPLETED" else None
        if not await self.repo.cas_todo_status(
            workspace_id,
            todo_id,
            expected_version=payload.version,
            new_status=payload.status,
            completed_at=completed_at,
        ):
            await self.session.rollback()
            raise AppError("invalid_todo_state", "只有 OPEN 待办可以更新", 409)
        add_audit(
            self.session,
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            actor_role=member.role,
            action=f"todo.{payload.status.lower()}",
            target_type="todo",
            target_id=todo_id,
            request_id=self.request_id,
            metadata={"from_version": payload.version},
        )
        await self.session.flush()
        refreshed = await self.repo.get_todo(workspace_id, todo_id)
        assert refreshed is not None
        response = todo_summary(refreshed)
        self._record(
            workspace_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=200,
            response=response.model_dump(mode="json"),
            resource_type="todo",
            resource_id=todo_id,
        )
        await self.session.commit()
        return response


class ApprovalDecisionService:
    """REST adapter that reuses the hardened durable HITL resume worker."""

    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        actor_id: UUID,
        *,
        request_id: str | None = None,
    ) -> None:
        self.session = session
        self.session_factory = session_factory
        self.actor_id = actor_id
        self.request_id = request_id

    async def decide(
        self,
        workspace_id: UUID,
        approval_id: UUID,
        payload: ApprovalDecision,
        *,
        idempotency_key: str | None,
    ) -> ApprovalDecisionResult:
        api = AgentApiService(
            self.session, self.actor_id, request_id=self.request_id
        )
        await api.access.require(workspace_id, minimum_role=MemberRole.EDITOR)
        scope = f"agent.approval.decide:{approval_id}"
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        key, replay = await api._replay(
            workspace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None and replay.response_json is not None:
            return ApprovalDecisionResult.model_validate(replay.response_json)
        approval = await api.repo.get_approval(workspace_id, approval_id)
        if approval is None or approval.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        if approval.arguments_hash != payload.tool_args_hash:
            raise AppError("approval_parameters_tampered", "审批参数哈希不一致", 409)
        ToolProposal(
            tool_call_id=approval.tool_call_id,
            arguments=TodoCreateInput.model_validate(approval.arguments_json),
            arguments_hash=approval.arguments_hash,
        )
        run = await api.repo.get_run(workspace_id, approval.run_id)
        if run is None:
            raise AppError("resource_not_found", "资源不存在", 404)
        approval_run_id = approval.run_id
        conversation_id = run.conversation_id
        await self.session.rollback()
        await TodoApprovalResumeWorker(self.session_factory).resume(
            workspace_id=workspace_id,
            actor_id=self.actor_id,
            conversation_id=conversation_id,
            decision=ApprovalResumeDecision(
                approval_id=approval_id,
                approval_version=payload.version,
                approved=payload.decision == "APPROVED",
            ),
        )
        self.session.expire_all()
        refreshed_approval = await api.repo.get_approval(workspace_id, approval_id)
        refreshed_run = await api.repo.get_run(workspace_id, approval_run_id)
        if (
            refreshed_approval is None
            or refreshed_run is None
            or refreshed_approval.resolved_at is None
        ):
            raise AppError("approval_inconsistent", "审批结果事实不完整", 409)
        todo = await api.repo.get_todo_by_approval(workspace_id, approval_id)
        response = ApprovalDecisionResult(
            approval_id=approval_id,
            status=refreshed_approval.status,
            decided_at=refreshed_approval.resolved_at,
            run=run_summary(refreshed_run),
            todo_id=todo.id if todo is not None else None,
        )
        api._record(
            workspace_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=200,
            response=response.model_dump(mode="json"),
            resource_type="approval_request",
            resource_id=approval_id,
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await api.idempotency.get(
                workspace_id, self.actor_id, scope, hash_idempotency_key(key)
            )
            if winner is not None and winner.request_hash == request_hash and winner.response_json:
                return ApprovalDecisionResult.model_validate(winner.response_json)
            raise AppError("idempotency_conflict", "审批幂等记录冲突", 409) from exc
        return response


class AgentEventStreamService:
    """MySQL-first SSE replay and polling service for P8 B9."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        actor_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
    ) -> None:
        self.session_factory = session_factory
        self.actor_id = actor_id
        self.workspace_id = workspace_id
        self.run_id = run_id

    async def prepare(self, last_event_id: str | None) -> tuple[int, bool]:
        if last_event_id is None or last_event_id == "":
            cursor = 0
        else:
            try:
                cursor = int(last_event_id)
            except ValueError as exc:
                raise AppError(
                    "invalid_last_event_id", "Last-Event-ID 必须为非负整数", 422
                ) from exc
            if cursor < 0:
                raise AppError("invalid_last_event_id", "Last-Event-ID 必须为非负整数", 422)
        async with self.session_factory() as session:
            await WorkspaceAccessService(session, self.actor_id).require(self.workspace_id)
            repo = AgentRepository(session)
            run = await repo.get_run(self.workspace_id, self.run_id)
            if run is None or run.requested_by_user_id != self.actor_id:
                raise AppError("resource_not_found", "资源不存在", 404)
            first, last = await repo.event_bounds(self.workspace_id, self.run_id)
            if first is not None and cursor < first - 1:
                raise self._history_expired(first, last)
            if last is not None and cursor > last:
                raise AppError(
                    "invalid_last_event_id",
                    "Last-Event-ID 超出当前 Run 的事件范围",
                    422,
                )
            replay_count, has_expired_payload = await repo.event_replay_facts(
                self.workspace_id,
                self.run_id,
                after_sequence=cursor,
            )
            expected_count = max((last or cursor) - cursor, 0)
            if has_expired_payload or replay_count != expected_count:
                raise self._history_expired(first, last)
            return cursor, run.status in TERMINAL_RUN_STATUSES

    def _history_expired(self, first: int | None, last: int | None) -> AppError:
        return AppError(
            "event_history_expired",
            "事件历史已过期，请读取 Run 快照恢复",
            410,
            {
                "run_id": str(self.run_id),
                "snapshot_url": (
                    f"/api/v1/workspaces/{self.workspace_id}/agent-runs/{self.run_id}"
                ),
                "earliest_available_event_id": str(first) if first is not None else None,
                "latest_event_id": str(last) if last is not None else None,
            },
        )

    @staticmethod
    def encode_event(event: AgentRunEvent) -> str:
        timestamp = _aware_utc(event.created_at).isoformat()
        data = json.dumps(
            {
                "run_id": str(event.run_id),
                "seq": event.sequence_no,
                "timestamp": timestamp,
                "payload": event.payload_json,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"id: {event.sequence_no}\nevent: {event.event_type}\ndata: {data}\n\n"

    @staticmethod
    def encode_heartbeat(run_id: UUID) -> str:
        heartbeat = json.dumps(
            {"run_id": str(run_id), "timestamp": utc_now().isoformat()},
            separators=(",", ":"),
        )
        return f"event: heartbeat\ndata: {heartbeat}\n\n"

    async def stream(self, cursor: int) -> AsyncIterator[str]:
        heartbeat_elapsed = 0
        while True:
            async with self.session_factory() as session:
                repo = AgentRepository(session)
                run = await repo.get_run(self.workspace_id, self.run_id)
                if run is None or run.requested_by_user_id != self.actor_id:
                    return
                events = await repo.list_events_after(
                    self.workspace_id, self.run_id, sequence_no=cursor
                )
                for event in events:
                    cursor = event.sequence_no
                    heartbeat_elapsed = 0
                    yield self.encode_event(event)
                if not events and run.status in TERMINAL_RUN_STATUSES:
                    return
                if events:
                    continue
            await asyncio.sleep(1)
            heartbeat_elapsed += 1
            if heartbeat_elapsed >= 15:
                heartbeat_elapsed = 0
                yield self.encode_heartbeat(self.run_id)
