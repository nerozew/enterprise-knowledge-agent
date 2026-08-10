from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import AgentKernel
from app.agent.hitl import TodoApprovalResumeWorker, TodoWorkflowInput
from app.agent.models import AgentMessage, AgentState, RouteDecision
from app.agent.providers import AgentProviderUnavailableError, OpenAICompatibleAgentModel
from app.agent.tools import InspirationServiceToolAdapter, KnowledgeServiceToolAdapter
from app.core.config import Settings
from app.core.errors import AppError
from app.models.agent import (
    AgentCitation,
    AgentMessageRecord,
    AgentRun,
    AgentRunDispatch,
    AgentRunStatus,
)
from app.models.base import utc_now
from app.repositories.agent import AgentRepository
from app.services.inspirations import InspirationService
from app.services.knowledge import KnowledgeService

TERMINAL_RUN_STATUSES = {
    AgentRunStatus.COMPLETED.value,
    AgentRunStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value,
}


class DispatchLeaseLost(RuntimeError):
    """The computation must be discarded because this claim was fenced out."""


KernelFactory = Callable[[AsyncSession, AgentRun], AgentKernel]


class AgentDispatchWorker:
    """MySQL-first lease worker for initial P8 LangGraph execution."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        worker_id: str,
        kernel_factory: KernelFactory | None = None,
        clock: Callable[[], datetime] = utc_now,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.worker_id = worker_id
        self.kernel_factory = kernel_factory
        self.clock = clock
        self.random_source = random_source

    def _now(self) -> datetime:
        value = self.clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _default_kernel(self, session: AsyncSession, run: AgentRun) -> AgentKernel:
        model = OpenAICompatibleAgentModel(self.settings)
        return AgentKernel(
            router=model,
            direct_answer=model,
            todo_arguments=model,
            knowledge_tool=KnowledgeServiceToolAdapter(
                lambda context: KnowledgeService(
                    session,
                    context.actor_id,
                    context.workspace_id,
                    self.settings,
                )
            ),
            inspiration_tool=InspirationServiceToolAdapter(
                lambda context: InspirationService(
                    session,
                    context.actor_id,
                    workspace_id=context.workspace_id,
                )
            ),
        )

    async def claim(self, *, limit: int | None = None) -> list[AgentRunDispatch]:
        async with self.session_factory() as session:
            rows = await AgentRepository(session).claim_dispatches(
                worker_id=self.worker_id,
                lease_seconds=self.settings.agent_dispatch_lease_seconds,
                limit=limit or self.settings.agent_dispatch_claim_limit,
            )
            await session.commit()
            return rows

    async def renew(self, dispatch: AgentRunDispatch) -> bool:
        if dispatch.lease_owner is None:
            return False
        async with self.session_factory() as session:
            renewed = await AgentRepository(session).renew_dispatch_lease(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                lease_seconds=self.settings.agent_dispatch_lease_seconds,
            )
            await session.commit()
            return renewed

    async def run_once(self) -> int:
        claimed = await self.claim()
        await asyncio.gather(*(self._run_claim_with_renewal(row) for row in claimed))
        return len(claimed)

    async def _run_claim_with_renewal(self, dispatch: AgentRunDispatch) -> None:
        task = asyncio.create_task(self._process_claim(dispatch))
        try:
            while not task.done():
                done, _ = await asyncio.wait(
                    {task}, timeout=self.settings.agent_dispatch_renew_seconds
                )
                if done:
                    break
                if not await self.renew(dispatch):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise DispatchLeaseLost("dispatch lease renewal was fenced out")
            await task
        except DispatchLeaseLost:
            return

    async def _load_state(self, dispatch: AgentRunDispatch) -> tuple[AgentRun, AgentState]:
        async with self.session_factory() as session:
            repo = AgentRepository(session)
            run = await repo.get_run(dispatch.workspace_id, dispatch.run_id)
            if run is None:
                raise AppError("resource_not_found", "Agent Run 不存在", 404)
            records = await repo.list_recent_messages(
                dispatch.workspace_id, run.conversation_id, limit=12
            )
            messages = [
                AgentMessage(role=record.role.lower(), content=record.content)
                for record in records
                if record.role.lower() in {"system", "user", "assistant"}
            ]
            if not messages:
                messages = [AgentMessage(role="user", content=run.input_text)]
            persisted_route = None
            if run.route is not None and run.reason_code is not None:
                persisted_route = RouteDecision.model_validate(
                    {
                        "route": run.route,
                        "confidence": 1,
                        "reason_code": run.reason_code,
                        "normalized_query": run.input_text,
                    }
                )
            state = AgentState(
                messages=messages,
                workspace_id=run.workspace_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                actor_id=run.requested_by_user_id,
                graph_version=run.graph_version,
                route=persisted_route,
            )
            return run, state

    async def _invoke(self, run: AgentRun, state: AgentState) -> AgentState:
        async with self.session_factory() as service_session:
            factory = self.kernel_factory or self._default_kernel
            raw = await factory(service_session, run).compile().ainvoke(state)
            return AgentState.model_validate(raw)

    async def _process_claim(self, dispatch: AgentRunDispatch) -> None:
        try:
            run, state = await self._load_state(dispatch)
            if run.status in TERMINAL_RUN_STATUSES | {AgentRunStatus.WAITING_APPROVAL.value}:
                await self._finish(dispatch, "SUCCEEDED")
                return
            result = await self._invoke(run, state)
            await self._persist_result(dispatch, result)
        except DispatchLeaseLost:
            return
        except Exception as exc:
            await self._handle_failure(dispatch, exc)

    async def _persist_result(self, dispatch: AgentRunDispatch, result: AgentState) -> None:
        if dispatch.lease_owner is None:
            raise DispatchLeaseLost
        async with self.session_factory() as session:
            repo = AgentRepository(session)
            if not await repo.owns_dispatch_lease(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                for_update=True,
            ):
                raise DispatchLeaseLost
            run = await repo.get_run(dispatch.workspace_id, dispatch.run_id, for_update=True)
            if run is None:
                raise AppError("resource_not_found", "Agent Run 不存在", 404)
            if run.status in TERMINAL_RUN_STATUSES | {AgentRunStatus.WAITING_APPROVAL.value}:
                await session.rollback()
                await self._finish(dispatch, "SUCCEEDED")
                return
            if result.route is not None:
                if run.route is not None and run.route != result.route.route:
                    raise AppError("route_binding_conflict", "Run 路由事实不一致", 409)
                if run.route is None:
                    run.route = result.route.route
                    run.reason_code = result.route.reason_code
                    run.version += 1
                    await repo.append_event(
                        run.workspace_id,
                        run.id,
                        "route.selected",
                        {
                            "route": result.route.route,
                            "reason_code": result.route.reason_code,
                        },
                    )
            await session.commit()

        if (
            result.route is not None
            and result.route.route == "TODO_CREATE"
            and result.pending_tool_call
        ):
            await TodoApprovalResumeWorker(self.session_factory, clock=self.clock).start(
                TodoWorkflowInput(
                    workspace_id=result.workspace_id,
                    actor_id=result.actor_id,
                    conversation_id=result.conversation_id,
                    run_id=result.run_id,
                    proposal=result.pending_tool_call,
                    expires_at=self._now()
                    + timedelta(seconds=self.settings.agent_approval_ttl_seconds),
                    short_term_messages=[message.model_dump() for message in result.messages[-12:]],
                ),
                dispatch_id=dispatch.id,
                claim_token=dispatch.lease_owner,
            )
            await self._finish(dispatch, "SUCCEEDED")
            return
        await self._persist_terminal(dispatch, result)

    async def _persist_terminal(self, dispatch: AgentRunDispatch, result: AgentState) -> None:
        if dispatch.lease_owner is None:
            raise DispatchLeaseLost
        async with self.session_factory() as session:
            repo = AgentRepository(session)
            if not await repo.owns_dispatch_lease(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                for_update=True,
            ):
                raise DispatchLeaseLost
            run = await repo.get_run(dispatch.workspace_id, dispatch.run_id, for_update=True)
            if run is None:
                raise AppError("resource_not_found", "Agent Run 不存在", 404)
            if run.status in TERMINAL_RUN_STATUSES:
                await session.rollback()
                await self._finish(dispatch, "SUCCEEDED")
                return
            failed = result.error_code == "AGENT_OUTPUT_INVALID"
            now = self._now()
            if result.route and result.route.route in {"KNOWLEDGE_QA", "INSPIRATION_SEARCH"}:
                await repo.append_event(
                    run.workspace_id,
                    run.id,
                    "retrieval.started",
                    {"route": result.route.route},
                )
                await repo.append_event(
                    run.workspace_id,
                    run.id,
                    "retrieval.completed",
                    {"route": result.route.route, "citation_count": len(result.citations)},
                )
            message: AgentMessageRecord | None = None
            if result.final_answer:
                message = AgentMessageRecord(
                    workspace_id=run.workspace_id,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    role="ASSISTANT",
                    content=result.final_answer,
                    sequence_no=await repo.next_message_sequence(
                        run.workspace_id, run.conversation_id
                    ),
                    version=1,
                )
                repo.add_message(message)
                await session.flush()
            for ordinal, citation in enumerate(result.citations, start=1):
                repo.add_citation(
                    AgentCitation(
                        workspace_id=run.workspace_id,
                        run_id=run.id,
                        message_id=message.id if message else None,
                        document_version_id=citation.document_version_id,
                        chunk_id=citation.chunk_id,
                        source_span_id=citation.source_span_id,
                        quote=citation.quote,
                        ordinal=ordinal,
                    )
                )
            run.status = AgentRunStatus.FAILED.value if failed else AgentRunStatus.COMPLETED.value
            run.error_code = result.error_code
            run.finished_at = now
            run.version += 1
            await repo.append_event(
                run.workspace_id,
                run.id,
                "run.failed" if failed else "run.completed",
                {"outcome": "failed" if failed else "answered"},
            )
            finished = await repo.finish_dispatch(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                status="SUCCEEDED",
            )
            if not finished:
                raise DispatchLeaseLost
            await session.commit()

    async def _finish(self, dispatch: AgentRunDispatch, status: str) -> None:
        if dispatch.lease_owner is None:
            raise DispatchLeaseLost
        async with self.session_factory() as session:
            finished = await AgentRepository(session).finish_dispatch(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                status=status,
            )
            if not finished:
                raise DispatchLeaseLost
            await session.commit()

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (AgentProviderUnavailableError, OperationalError, TimeoutError)):
            return True
        return isinstance(exc, AppError) and exc.code in {
            "approval_concurrency_conflict",
            "checkpoint_not_ready",
        }

    async def _handle_failure(self, dispatch: AgentRunDispatch, exc: Exception) -> None:
        if dispatch.lease_owner is None:
            return
        error_code = getattr(exc, "code", exc.__class__.__name__).lower()[:80]
        retryable = (
            self._retryable(exc) and dispatch.attempts < self.settings.agent_dispatch_max_attempts
        )
        async with self.session_factory() as session:
            repo = AgentRepository(session)
            if retryable:
                base = min(2 ** (dispatch.attempts - 1) * 2, 60)
                delay = base * (1 + 0.25 * self.random_source())
                changed = await repo.reschedule_dispatch(
                    dispatch.workspace_id,
                    dispatch.id,
                    claim_token=dispatch.lease_owner,
                    delay_seconds=delay,
                    error_code=error_code,
                )
                if changed:
                    await session.commit()
                else:
                    await session.rollback()
                return
            if not await repo.owns_dispatch_lease(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                for_update=True,
            ):
                await session.rollback()
                return
            run = await repo.get_run(dispatch.workspace_id, dispatch.run_id, for_update=True)
            now = await repo.database_utc_now()
            if run is not None and run.status not in TERMINAL_RUN_STATUSES:
                run.status = AgentRunStatus.FAILED.value
                run.error_code = error_code
                run.finished_at = now
                run.version += 1
                await repo.append_event(
                    run.workspace_id,
                    run.id,
                    "run.failed",
                    {
                        "error_code": error_code,
                        "retry_exhausted": (
                            dispatch.attempts >= self.settings.agent_dispatch_max_attempts
                        ),
                    },
                )
            changed = await repo.finish_dispatch(
                dispatch.workspace_id,
                dispatch.id,
                claim_token=dispatch.lease_owner,
                status="FAILED",
                error_code=error_code,
            )
            if changed:
                await session.commit()
            else:
                await session.rollback()

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            claimed = await self.run_once()
            if claimed == 0:
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self.settings.agent_dispatch_poll_seconds
                    )
                except TimeoutError:
                    pass
