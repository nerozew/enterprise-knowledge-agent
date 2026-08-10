from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import TodoCreateInput
from app.core.errors import AppError
from app.models.agent import AgentCheckpoint, AgentRunStatus
from app.models.base import utc_now
from app.models.entities import MemberRole
from app.repositories.agent import AgentRepository
from app.services.workspaces import WorkspaceAccessService, add_audit


class AgentCheckpointBindingService:
    """Binds one Todo run and approval to its exact durable interrupt checkpoint."""

    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = actor_id
        self.access = WorkspaceAccessService(session, actor_id)
        self.repo = AgentRepository(session)

    async def bind(
        self,
        *,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> None:
        await self.access.require(workspace_id, minimum_role=MemberRole.EDITOR)
        try:
            run = await self.repo.get_run(workspace_id, run_id, for_update=True)
            approval = await self.repo.get_approval(workspace_id, approval_id, for_update=True)
        except OperationalError as exc:
            await self.session.rollback()
            raise AppError("approval_concurrency_conflict", "审批并发冲突，请重试", 409) from exc
        if (
            run is None
            or approval is None
            or run.requested_by_user_id != self.actor_id
            or approval.requested_by_user_id != self.actor_id
            or run.conversation_id != conversation_id
            or approval.run_id != run.id
        ):
            raise AppError("resource_not_found", "资源不存在", 404)
        checkpoint = await self.session.scalar(
            select(AgentCheckpoint).where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.thread_id == conversation_id,
                AgentCheckpoint.checkpoint_ns == checkpoint_ns,
                AgentCheckpoint.checkpoint_id == checkpoint_id,
            )
        )
        if checkpoint is None:
            raise AppError("checkpoint_not_ready", "审批 Checkpoint 尚未就绪", 409)
        run_binding = (run.checkpoint_ns, run.checkpoint_id)
        approval_binding = (approval.checkpoint_ns, approval.checkpoint_id)
        requested_binding = (checkpoint_ns, checkpoint_id)
        if run_binding not in {(None, None), requested_binding}:
            raise AppError("checkpoint_binding_conflict", "运行已绑定其他 Checkpoint", 409)
        if approval_binding not in {(None, None), requested_binding}:
            raise AppError("checkpoint_binding_conflict", "审批已绑定其他 Checkpoint", 409)
        run.checkpoint_ns = checkpoint_ns
        run.checkpoint_id = checkpoint_id
        approval.checkpoint_ns = checkpoint_ns
        approval.checkpoint_id = checkpoint_id
        try:
            await self.session.commit()
        except OperationalError as exc:
            await self.session.rollback()
            raise AppError("approval_concurrency_conflict", "审批并发冲突，请重试", 409) from exc
        except IntegrityError as exc:
            await self.session.rollback()
            raise AppError("checkpoint_binding_conflict", "Checkpoint 绑定冲突", 409) from exc

    async def publish(
        self,
        *,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        checkpoint_ns: str,
        checkpoint_id: str,
        dispatch_id: UUID | None = None,
        claim_token: str | None = None,
    ) -> None:
        """Atomically expose a prepared approval only after its checkpoint exists."""

        _, member = await self.access.require(
            workspace_id, minimum_role=MemberRole.EDITOR, for_update=True
        )
        if (dispatch_id is None) != (claim_token is None):
            raise ValueError("dispatch_id and claim_token must be provided together")
        try:
            if dispatch_id is not None and claim_token is not None:
                if not await self.repo.owns_dispatch_lease(
                    workspace_id,
                    dispatch_id,
                    claim_token=claim_token,
                    for_update=True,
                ):
                    raise AppError("dispatch_lease_lost", "Dispatch 租约已失效", 409)
            run = await self.repo.get_run(workspace_id, run_id, for_update=True)
            approval = await self.repo.get_approval(workspace_id, approval_id, for_update=True)
            checkpoint = await self.session.scalar(
                select(AgentCheckpoint).where(
                    AgentCheckpoint.workspace_id == workspace_id,
                    AgentCheckpoint.thread_id == conversation_id,
                    AgentCheckpoint.checkpoint_ns == checkpoint_ns,
                    AgentCheckpoint.checkpoint_id == checkpoint_id,
                )
            )
            if (
                run is None
                or approval is None
                or checkpoint is None
                or run.requested_by_user_id != self.actor_id
                or approval.requested_by_user_id != self.actor_id
                or run.conversation_id != conversation_id
                or approval.run_id != run.id
            ):
                raise AppError("resource_not_found", "资源不存在", 404)
            arguments = TodoCreateInput.model_validate(approval.arguments_json)
            if arguments.assignee_id not in {None, self.actor_id}:
                raise AppError("unsupported_assignee", "首版待办只能分配给当前操作人", 422)
            requested_binding = (checkpoint_ns, checkpoint_id)
            if (run.checkpoint_ns, run.checkpoint_id) not in {(None, None), requested_binding}:
                raise AppError("checkpoint_binding_conflict", "运行已绑定其他 Checkpoint", 409)
            if (approval.checkpoint_ns, approval.checkpoint_id) not in {
                (None, None),
                requested_binding,
            }:
                raise AppError("checkpoint_binding_conflict", "审批已绑定其他 Checkpoint", 409)
            if run.status == AgentRunStatus.WAITING_APPROVAL.value:
                if (run.checkpoint_ns, run.checkpoint_id) == (None, None) and (
                    approval.checkpoint_ns,
                    approval.checkpoint_id,
                ) == (None, None):
                    run.checkpoint_ns = checkpoint_ns
                    run.checkpoint_id = checkpoint_id
                    approval.checkpoint_ns = checkpoint_ns
                    approval.checkpoint_id = checkpoint_id
                    await self.session.commit()
                    return
                if (run.checkpoint_ns, run.checkpoint_id) != requested_binding:
                    raise AppError("checkpoint_binding_conflict", "审批发布绑定不一致", 409)
                await self.session.rollback()
                return
            if run.status != AgentRunStatus.RUNNING.value:
                raise AppError("invalid_run_state", "当前运行不能发布审批", 409)
            run.checkpoint_ns = checkpoint_ns
            run.checkpoint_id = checkpoint_id
            run.status = AgentRunStatus.WAITING_APPROVAL.value
            run.version += 1
            run.updated_at = utc_now()
            approval.checkpoint_ns = checkpoint_ns
            approval.checkpoint_id = checkpoint_id
            await self.repo.append_event(
                workspace_id,
                run_id,
                "tool.proposed",
                {
                    "tool_call_id": str(approval.tool_call_id),
                    "tool_name": approval.tool_name,
                    "arguments": approval.arguments_json,
                    "tool_args_hash": approval.arguments_hash,
                    "requires_approval": True,
                },
            )
            await self.repo.append_event(
                workspace_id,
                run_id,
                "approval.required",
                {
                    "approval_id": str(approval.id),
                    "tool_call_id": str(approval.tool_call_id),
                    "status": approval.status,
                    "version": approval.version,
                    "expires_at": approval.expires_at.isoformat(),
                },
            )
            add_audit(
                self.session,
                workspace_id=workspace_id,
                actor_id=self.actor_id,
                actor_role=member.role,
                action="approval.requested",
                target_type="approval_request",
                target_id=approval.id,
                request_id=None,
                metadata={"run_id": str(run_id), "version": approval.version},
            )
            await self.session.commit()
        except (OperationalError, IntegrityError) as exc:
            await self.session.rollback()
            raise AppError("checkpoint_binding_conflict", "Checkpoint 发布冲突", 409) from exc
        except Exception:
            await self.session.rollback()
            raise
