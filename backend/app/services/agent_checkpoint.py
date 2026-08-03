from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.agent import AgentCheckpoint
from app.models.entities import MemberRole
from app.repositories.agent import AgentRepository
from app.services.workspaces import WorkspaceAccessService


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
