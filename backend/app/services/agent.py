from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import TodoCreateInput, ToolProposal
from app.core.errors import AppError
from app.models.agent import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Todo,
    TodoStatus,
)
from app.models.base import utc_now
from app.models.entities import MemberRole
from app.repositories.agent import AgentRepository
from app.services.workspaces import WorkspaceAccessService, add_audit, canonical_request_hash


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TodoApprovalService:
    """Human approval boundary for the first P8 write tool."""

    def __init__(
        self,
        session: AsyncSession,
        actor_id: UUID,
        workspace_id: UUID,
        *,
        request_id: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.workspace_id = workspace_id
        self.request_id = request_id
        self.clock = clock
        self.access = WorkspaceAccessService(session, actor_id)
        self.repo = AgentRepository(session)

    def _validated_proposal(self, proposal: ToolProposal) -> tuple[TodoCreateInput, str]:
        if proposal.tool_name != "todo_create" or not proposal.requires_approval:
            raise AppError("invalid_tool_proposal", "待办工具提议无效", 422)
        arguments = TodoCreateInput.model_validate(proposal.arguments)
        if arguments.assignee_id not in {None, self.actor_id}:
            raise AppError(
                "unsupported_assignee",
                "首版待办只能分配给当前操作者",
                422,
            )
        calculated_hash = canonical_request_hash(arguments.model_dump(mode="json"))
        if calculated_hash != proposal.arguments_hash:
            raise AppError("approval_parameters_tampered", "待审批参数哈希不匹配", 409)
        return arguments, calculated_hash

    async def prepare_approval(
        self,
        *,
        run_id: UUID,
        proposal: ToolProposal,
        expires_at: datetime,
        dispatch_id: UUID | None = None,
        claim_token: str | None = None,
    ) -> ApprovalRequest:
        """Persist an internal draft without publishing it to REST or SSE."""

        if (dispatch_id is None) != (claim_token is None):
            raise ValueError("dispatch_id and claim_token must be provided together")
        now = _aware_utc(self.clock())
        if expires_at.tzinfo is None:
            raise AppError("invalid_approval_expiry", "审批有效期必须是未来的带时区时间", 422)
        try:
            await self.access.require(self.workspace_id, minimum_role=MemberRole.EDITOR)
            if dispatch_id is not None and claim_token is not None:
                if not await self.repo.owns_dispatch_lease(
                    self.workspace_id,
                    dispatch_id,
                    claim_token=claim_token,
                    for_update=True,
                ):
                    raise AppError("dispatch_lease_lost", "Dispatch 租约已失效", 409)
            arguments, calculated_hash = self._validated_proposal(proposal)
            run = await self.repo.get_run(self.workspace_id, run_id, for_update=True)
            if run is None or run.requested_by_user_id != self.actor_id:
                raise AppError("resource_not_found", "资源不存在", 404)
            existing = await self.repo.get_approval_by_call(
                self.workspace_id, run_id, proposal.tool_call_id
            )
            if existing is not None:
                if (
                    existing.requested_by_user_id != self.actor_id
                    or existing.arguments_hash != calculated_hash
                    or canonical_request_hash(existing.arguments_json) != calculated_hash
                ):
                    raise AppError("approval_parameters_tampered", "审批参数不一致", 409)
                await self.session.commit()
                return existing
            if _aware_utc(expires_at) <= now:
                raise AppError("invalid_approval_expiry", "审批有效期必须是未来的带时区时间", 422)
            if run.route != "TODO_CREATE" or run.status != AgentRunStatus.RUNNING.value:
                raise AppError("invalid_run_state", "当前运行不能准备审批", 409)
            approval = ApprovalRequest(
                workspace_id=self.workspace_id,
                run_id=run_id,
                tool_call_id=proposal.tool_call_id,
                requested_by_user_id=self.actor_id,
                tool_name=proposal.tool_name,
                arguments_json=arguments.model_dump(mode="json"),
                arguments_hash=calculated_hash,
                status=ApprovalStatus.PENDING.value,
                version=1,
                expires_at=_aware_utc(expires_at),
            )
            self.repo.add_approval(approval)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.repo.get_approval_by_call(
                self.workspace_id, run_id, proposal.tool_call_id
            )
            if winner is not None and winner.arguments_hash == calculated_hash:
                return winner
            raise AppError("approval_conflict", "审批请求已存在", 409) from exc
        except Exception:
            await self.session.rollback()
            raise
        return approval

    async def create_approval(
        self,
        *,
        run_id: UUID,
        proposal: ToolProposal,
        expires_at: datetime,
    ) -> ApprovalRequest:
        now = _aware_utc(self.clock())
        if expires_at.tzinfo is None:
            raise AppError("invalid_approval_expiry", "审批有效期必须是未来的带时区时间", 422)
        _, member = await self.access.require(self.workspace_id, minimum_role=MemberRole.EDITOR)
        arguments, calculated_hash = self._validated_proposal(proposal)
        run = await self.repo.get_run(self.workspace_id, run_id, for_update=True)
        if run is None or run.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        if run.route != "TODO_CREATE":
            raise AppError("invalid_run_state", "当前运行不是待办创建路由", 409)
        existing = await self.repo.get_approval_by_call(
            self.workspace_id, run_id, proposal.tool_call_id
        )
        if existing is not None:
            if (
                existing.requested_by_user_id != self.actor_id
                or existing.arguments_hash != calculated_hash
                or canonical_request_hash(existing.arguments_json) != calculated_hash
            ):
                raise AppError("approval_parameters_tampered", "审批参数不一致", 409)
            return existing
        if _aware_utc(expires_at) <= now:
            raise AppError(
                "invalid_approval_expiry",
                "审批有效期必须是未来的带时区时间",
                422,
            )
        if run.status != AgentRunStatus.RUNNING.value:
            raise AppError("invalid_run_state", "当前运行不能创建审批", 409)

        approval = ApprovalRequest(
            workspace_id=self.workspace_id,
            run_id=run_id,
            tool_call_id=proposal.tool_call_id,
            requested_by_user_id=self.actor_id,
            tool_name=proposal.tool_name,
            arguments_json=arguments.model_dump(mode="json"),
            arguments_hash=calculated_hash,
            status=ApprovalStatus.PENDING.value,
            version=1,
            expires_at=_aware_utc(expires_at),
        )
        try:
            self.repo.add_approval(approval)
            transitioned = await self.repo.cas_run_status(
                self.workspace_id,
                run.id,
                expected_status=AgentRunStatus.RUNNING.value,
                expected_version=run.version,
                new_status=AgentRunStatus.WAITING_APPROVAL.value,
            )
            if not transitioned:
                raise AppError("version_conflict", "Agent Run 已被其他操作更新", 409)
            await self.repo.append_event(
                self.workspace_id,
                run.id,
                "tool.proposed",
                {
                    "tool_call_id": str(proposal.tool_call_id),
                    "tool_name": proposal.tool_name,
                    "arguments": arguments.model_dump(mode="json"),
                    "tool_args_hash": calculated_hash,
                    "requires_approval": True,
                },
            )
            await self.repo.append_event(
                self.workspace_id,
                run.id,
                "approval.required",
                {
                    "approval_id": str(approval.id),
                    "tool_call_id": str(proposal.tool_call_id),
                },
            )
            add_audit(
                self.session,
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                actor_role=member.role,
                action="approval.requested",
                target_type="approval_request",
                target_id=approval.id,
                request_id=self.request_id,
                metadata={
                    "run_id": str(run.id),
                    "tool_name": proposal.tool_name,
                    "version": 1,
                },
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.repo.get_approval_by_call(
                self.workspace_id, run_id, proposal.tool_call_id
            )
            if winner is not None and winner.arguments_hash == calculated_hash:
                return winner
            raise AppError("approval_conflict", "审批请求已存在", 409) from exc
        except OperationalError as exc:
            await self.session.rollback()
            raise AppError("approval_concurrency_conflict", "审批并发冲突，请重试", 409) from exc
        except Exception:
            await self.session.rollback()
            raise
        refreshed = await self.repo.get_approval(self.workspace_id, approval.id)
        assert refreshed is not None
        return refreshed

    async def resolve(
        self,
        approval_id: UUID,
        *,
        approval_version: int,
        proposal: ToolProposal,
        approved: bool,
    ) -> tuple[ApprovalRequest, Todo | None]:
        now = _aware_utc(self.clock())
        _, member = await self.access.require(self.workspace_id, minimum_role=MemberRole.EDITOR)
        arguments, calculated_hash = self._validated_proposal(proposal)
        approval_snapshot = await self.repo.get_approval(self.workspace_id, approval_id)
        approval = approval_snapshot
        if approval is None or approval.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        try:
            run = await self.repo.get_run(self.workspace_id, approval.run_id, for_update=True)
            approval = await self.repo.get_approval(self.workspace_id, approval_id, for_update=True)
        except OperationalError as exc:
            await self.session.rollback()
            raise AppError("approval_concurrency_conflict", "审批并发冲突，请重试", 409) from exc
        if approval is None or run is None or approval.run_id != run.id:
            raise AppError("resource_not_found", "资源不存在", 404)
        if run is None or run.requested_by_user_id != self.actor_id:
            raise AppError("resource_not_found", "资源不存在", 404)
        if (
            approval.tool_call_id != proposal.tool_call_id
            or approval.tool_name != proposal.tool_name
            or approval.arguments_hash != calculated_hash
            or canonical_request_hash(approval.arguments_json) != calculated_hash
        ):
            raise AppError("approval_parameters_tampered", "审批参数不一致", 409)

        if approval.status == ApprovalStatus.APPROVED.value:
            if not approved:
                raise AppError("approval_already_approved", "审批已批准", 409)
            todo = await self.repo.get_todo_by_approval(self.workspace_id, approval.id)
            if todo is None:
                raise AppError("approval_inconsistent", "已批准审批缺少对应待办", 409)
            return approval, todo
        if approval.status == ApprovalStatus.REJECTED.value:
            if approved:
                raise AppError("approval_already_rejected", "审批已被拒绝", 409)
            return approval, None
        if approval.status == ApprovalStatus.EXPIRED.value:
            raise AppError("approval_expired", "审批已过期", 409)
        if approval.version != approval_version:
            raise AppError("version_conflict", "审批已被其他操作更新", 409)
        if _aware_utc(approval.expires_at) <= now:
            await self._expire(approval, run, member.role, now)
            raise AppError("approval_expired", "审批已过期", 409)
        if run.status != AgentRunStatus.WAITING_APPROVAL.value:
            raise AppError("invalid_run_state", "Agent Run 不在待审批状态", 409)

        if not approved:
            await self._reject(approval, run, member.role, now)
            refreshed = await self.repo.get_approval(self.workspace_id, approval.id)
            assert refreshed is not None
            return refreshed, None
        return await self._approve(approval, run, arguments, member.role, now)

    async def _reject(
        self, approval: ApprovalRequest, run: AgentRun, actor_role: str, now: datetime
    ) -> None:
        try:
            if not await self.repo.cas_approval_status(
                self.workspace_id,
                approval.id,
                expected_status=ApprovalStatus.PENDING.value,
                expected_version=approval.version,
                new_status=ApprovalStatus.REJECTED.value,
                resolved_by_user_id=self.actor_id,
                resolved_at=now,
            ):
                raise AppError("version_conflict", "审批已被其他操作更新", 409)
            if not await self.repo.cas_run_status(
                self.workspace_id,
                run.id,
                expected_status=AgentRunStatus.WAITING_APPROVAL.value,
                expected_version=run.version,
                new_status=AgentRunStatus.CANCELLED.value,
                finished_at=now,
            ):
                raise AppError("version_conflict", "Agent Run 已被其他操作更新", 409)
            await self.repo.append_event(
                self.workspace_id,
                run.id,
                "approval.resolved",
                {
                    "approval_id": str(approval.id),
                    "decision": "REJECTED",
                    "resolved_by_user_id": str(self.actor_id),
                    "resolved_at": now.isoformat(),
                },
            )
            await self.repo.append_event(
                self.workspace_id,
                run.id,
                "run.cancelled",
                {"reason": "approval_rejected", "approval_id": str(approval.id)},
            )
            add_audit(
                self.session,
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                actor_role=actor_role,
                action="approval.rejected",
                target_type="approval_request",
                target_id=approval.id,
                request_id=self.request_id,
                metadata={"run_id": str(run.id), "from_version": approval.version},
            )
            await self.session.commit()
        except (IntegrityError, OperationalError) as exc:
            await self.session.rollback()
            raise AppError("approval_concurrency_conflict", "审批并发冲突，请重试", 409) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def _expire(
        self, approval: ApprovalRequest, run: AgentRun, actor_role: str, now: datetime
    ) -> None:
        try:
            if not await self.repo.cas_approval_status(
                self.workspace_id,
                approval.id,
                expected_status=ApprovalStatus.PENDING.value,
                expected_version=approval.version,
                new_status=ApprovalStatus.EXPIRED.value,
                resolved_by_user_id=self.actor_id,
                resolved_at=now,
            ):
                raise AppError("version_conflict", "审批已被其他操作更新", 409)
            if not await self.repo.cas_run_status(
                self.workspace_id,
                run.id,
                expected_status=AgentRunStatus.WAITING_APPROVAL.value,
                expected_version=run.version,
                new_status=AgentRunStatus.CANCELLED.value,
                finished_at=now,
            ):
                raise AppError("version_conflict", "Agent Run 已被其他操作更新", 409)
            await self.repo.append_event(
                self.workspace_id,
                run.id,
                "approval.resolved",
                {
                    "approval_id": str(approval.id),
                    "decision": "EXPIRED",
                    "resolved_by_user_id": str(self.actor_id),
                    "resolved_at": now.isoformat(),
                },
            )
            await self.repo.append_event(
                self.workspace_id,
                run.id,
                "run.cancelled",
                {"reason": "approval_expired", "approval_id": str(approval.id)},
            )
            add_audit(
                self.session,
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                actor_role=actor_role,
                action="approval.expired",
                target_type="approval_request",
                target_id=approval.id,
                request_id=self.request_id,
                metadata={"run_id": str(run.id), "from_version": approval.version},
            )
            await self.session.commit()
        except (IntegrityError, OperationalError) as exc:
            await self.session.rollback()
            raise AppError("approval_concurrency_conflict", "审批并发冲突，请重试", 409) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def _approve(
        self,
        approval: ApprovalRequest,
        run: AgentRun,
        arguments: TodoCreateInput,
        actor_role: str,
        now: datetime,
    ) -> tuple[ApprovalRequest, Todo]:
        # Rollback expires ORM instances. Keep immutable scalar identifiers so
        # concurrency recovery never performs implicit async IO via attributes.
        approval_id = approval.id
        approval_version = approval.version
        tool_call_id = approval.tool_call_id
        run_id = run.id
        run_version = run.version
        try:
            _, current_member = await self.access.require(
                self.workspace_id,
                minimum_role=MemberRole.EDITOR,
                for_update=True,
            )
            actor_role = current_member.role
            if arguments.assignee_id not in {None, self.actor_id}:
                raise AppError("unsupported_assignee", "首版待办只能分配给当前操作人", 422)
            if not await self.repo.cas_approval_status(
                self.workspace_id,
                approval_id,
                expected_status=ApprovalStatus.PENDING.value,
                expected_version=approval_version,
                new_status=ApprovalStatus.APPROVED.value,
                resolved_by_user_id=self.actor_id,
                resolved_at=now,
            ):
                return await self._recover_approved(approval_id)
            todo = Todo(
                workspace_id=self.workspace_id,
                created_by_user_id=self.actor_id,
                assignee_user_id=arguments.assignee_id or self.actor_id,
                source_run_id=run_id,
                source_tool_call_id=tool_call_id,
                approval_id=approval_id,
                title=arguments.title,
                description=arguments.description,
                due_at=arguments.due_at,
                status=TodoStatus.OPEN.value,
                version=1,
            )
            self.repo.add_todo(todo)
            if not await self.repo.cas_run_status(
                self.workspace_id,
                run_id,
                expected_status=AgentRunStatus.WAITING_APPROVAL.value,
                expected_version=run_version,
                new_status=AgentRunStatus.COMPLETED.value,
                finished_at=now,
            ):
                return await self._recover_approved(approval_id)
            await self.repo.append_event(
                self.workspace_id,
                run_id,
                "approval.resolved",
                {
                    "approval_id": str(approval_id),
                    "decision": "APPROVED",
                    "resolved_by_user_id": str(self.actor_id),
                    "resolved_at": now.isoformat(),
                },
            )
            await self.repo.append_event(
                self.workspace_id,
                run_id,
                "tool.started",
                {"tool_call_id": str(tool_call_id), "tool_name": "todo_create"},
            )
            await self.repo.append_event(
                self.workspace_id,
                run_id,
                "todo.created",
                {
                    "todo_id": str(todo.id),
                    "approval_id": str(approval_id),
                    "tool_call_id": str(tool_call_id),
                },
            )
            await self.repo.append_event(
                self.workspace_id,
                run_id,
                "tool.completed",
                {
                    "tool_call_id": str(tool_call_id),
                    "tool_name": "todo_create",
                    "todo_id": str(todo.id),
                },
            )
            await self.repo.append_event(
                self.workspace_id,
                run_id,
                "run.completed",
                {"outcome": "todo_created", "todo_id": str(todo.id)},
            )
            add_audit(
                self.session,
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                actor_role=actor_role,
                action="approval.approved",
                target_type="approval_request",
                target_id=approval_id,
                request_id=self.request_id,
                metadata={"run_id": str(run_id), "from_version": approval_version},
            )
            add_audit(
                self.session,
                workspace_id=self.workspace_id,
                actor_id=self.actor_id,
                actor_role=actor_role,
                action="todo.created",
                target_type="todo",
                target_id=todo.id,
                request_id=self.request_id,
                metadata={
                    "run_id": str(run_id),
                    "approval_id": str(approval_id),
                    "version": 1,
                },
            )
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return await self._recover_approved(approval_id)
        except OperationalError as exc:
            await self.session.rollback()
            try:
                return await self._recover_approved(approval_id)
            except AppError:
                raise AppError(
                    "approval_concurrency_conflict", "审批并发冲突，请重试", 409
                ) from exc
        except Exception:
            await self.session.rollback()
            raise
        refreshed_approval = await self.repo.get_approval(self.workspace_id, approval_id)
        refreshed_todo = await self.repo.get_todo_by_approval(self.workspace_id, approval_id)
        assert refreshed_approval is not None and refreshed_todo is not None
        return refreshed_approval, refreshed_todo

    async def _recover_approved(self, approval_id: UUID) -> tuple[ApprovalRequest, Todo]:
        await self.session.rollback()
        winner = await self.repo.get_approval(self.workspace_id, approval_id)
        todo = await self.repo.get_todo_by_approval(self.workspace_id, approval_id)
        if winner is not None and winner.status == ApprovalStatus.APPROVED.value and todo:
            return winner, todo
        raise AppError("version_conflict", "审批已被其他操作更新", 409)
