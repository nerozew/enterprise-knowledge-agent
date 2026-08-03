from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import (
    AgentCitation,
    AgentMessageRecord,
    AgentRun,
    AgentRunDispatch,
    AgentRunEvent,
    ApprovalRequest,
    Conversation,
    Todo,
)
from app.models.base import utc_now
from app.models.knowledge import Document, DocumentChunk


class AgentRepository:
    """Workspace-scoped persistence and CAS transitions for P8 domain facts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_conversation(
        self, workspace_id: UUID, conversation_id: UUID, *, for_update: bool = False
    ) -> Conversation | None:
        query = select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.id == conversation_id,
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def list_conversations(
        self,
        workspace_id: UUID,
        *,
        actor_id: UUID,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[Conversation], int]:
        filters = [
            Conversation.workspace_id == workspace_id,
            Conversation.created_by_user_id == actor_id,
        ]
        if status is not None:
            filters.append(Conversation.status == status)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(Conversation).where(*filters)
            )
            or 0
        )
        rows = list(
            await self.session.scalars(
                select(Conversation)
                .where(*filters)
                .order_by(Conversation.updated_at.desc(), Conversation.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def add_conversation(self, conversation: Conversation) -> None:
        self.session.add(conversation)

    def add_run(self, run: AgentRun) -> None:
        self.session.add(run)

    def add_dispatch(self, dispatch: AgentRunDispatch) -> None:
        self.session.add(dispatch)

    def add_message(self, message: AgentMessageRecord) -> None:
        self.session.add(message)

    async def get_active_run_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> AgentRun | None:
        return await self.session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.conversation_id == conversation_id,
                AgentRun.status.in_(["CREATED", "RUNNING", "WAITING_APPROVAL"]),
            )
        )

    async def list_run_messages(
        self, workspace_id: UUID, run_id: UUID
    ) -> list[AgentMessageRecord]:
        return list(
            await self.session.scalars(
                select(AgentMessageRecord)
                .where(
                    AgentMessageRecord.workspace_id == workspace_id,
                    AgentMessageRecord.run_id == run_id,
                    AgentMessageRecord.role.in_(["SYSTEM", "USER", "ASSISTANT"]),
                )
                .order_by(AgentMessageRecord.sequence_no)
            )
        )

    async def next_message_sequence(self, workspace_id: UUID, conversation_id: UUID) -> int:
        current = await self.session.scalar(
            select(func.max(AgentMessageRecord.sequence_no)).where(
                AgentMessageRecord.workspace_id == workspace_id,
                AgentMessageRecord.conversation_id == conversation_id,
            )
        )
        return int(current or 0) + 1

    async def get_run(
        self, workspace_id: UUID, run_id: UUID, *, for_update: bool = False
    ) -> AgentRun | None:
        query = select(AgentRun).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.id == run_id,
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def get_dispatch_by_run(
        self, workspace_id: UUID, run_id: UUID
    ) -> AgentRunDispatch | None:
        return await self.session.scalar(
            select(AgentRunDispatch).where(
                AgentRunDispatch.workspace_id == workspace_id,
                AgentRunDispatch.run_id == run_id,
            )
        )

    async def claim_dispatches(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[AgentRunDispatch]:
        rows = list(
            await self.session.scalars(
                select(AgentRunDispatch)
                .where(
                    AgentRunDispatch.available_at <= now,
                    or_(
                        AgentRunDispatch.status == "PENDING",
                        (
                            (AgentRunDispatch.status == "PROCESSING")
                            & (AgentRunDispatch.lease_expires_at.is_not(None))
                            & (AgentRunDispatch.lease_expires_at <= now)
                        ),
                    ),
                )
                .order_by(AgentRunDispatch.available_at, AgentRunDispatch.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = "PROCESSING"
            row.attempts += 1
            row.lease_owner = worker_id
            row.lease_expires_at = lease_expires_at
            row.updated_at = now
        await self.session.flush()
        return rows

    async def finish_dispatch(
        self,
        dispatch_id: UUID,
        *,
        worker_id: str,
        status: str,
        now: datetime,
        error_code: str | None = None,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AgentRunDispatch)
                .where(
                    AgentRunDispatch.id == dispatch_id,
                    AgentRunDispatch.status == "PROCESSING",
                    AgentRunDispatch.lease_owner == worker_id,
                )
                .values(
                    status=status,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=error_code,
                    completed_at=now,
                    updated_at=now,
                )
            ),
        )
        return result.rowcount == 1

    async def get_approval(
        self, workspace_id: UUID, approval_id: UUID, *, for_update: bool = False
    ) -> ApprovalRequest | None:
        query = select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.id == approval_id,
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def get_approval_by_call(
        self, workspace_id: UUID, run_id: UUID, tool_call_id: UUID
    ) -> ApprovalRequest | None:
        return await self.session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.workspace_id == workspace_id,
                ApprovalRequest.run_id == run_id,
                ApprovalRequest.tool_call_id == tool_call_id,
            )
        )

    async def get_todo_by_approval(self, workspace_id: UUID, approval_id: UUID) -> Todo | None:
        return await self.session.scalar(
            select(Todo).where(
                Todo.workspace_id == workspace_id,
                Todo.approval_id == approval_id,
            )
        )

    async def get_todo_by_source(
        self, workspace_id: UUID, run_id: UUID, tool_call_id: UUID
    ) -> Todo | None:
        return await self.session.scalar(
            select(Todo).where(
                Todo.workspace_id == workspace_id,
                Todo.source_run_id == run_id,
                Todo.source_tool_call_id == tool_call_id,
            )
        )

    async def get_todo(self, workspace_id: UUID, todo_id: UUID) -> Todo | None:
        return await self.session.scalar(
            select(Todo).where(Todo.workspace_id == workspace_id, Todo.id == todo_id)
        )

    async def list_approvals(
        self,
        workspace_id: UUID,
        *,
        requested_by_user_id: UUID,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[ApprovalRequest], int]:
        filters = [
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.requested_by_user_id == requested_by_user_id,
        ]
        if status is not None:
            filters.append(ApprovalRequest.status == status)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(ApprovalRequest).where(*filters)
            )
            or 0
        )
        rows = list(
            await self.session.scalars(
                select(ApprovalRequest)
                .where(*filters)
                .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    async def list_todos(
        self,
        workspace_id: UUID,
        *,
        actor_id: UUID,
        page: int,
        page_size: int,
        status: str | None,
        assignee_id: UUID | None,
    ) -> tuple[list[Todo], int]:
        filters = [
            Todo.workspace_id == workspace_id,
            or_(
                Todo.assignee_user_id == actor_id,
                Todo.created_by_user_id == actor_id,
            ),
        ]
        if status is not None:
            filters.append(Todo.status == status)
        if assignee_id is not None:
            filters.append(Todo.assignee_user_id == assignee_id)
        total = int(
            await self.session.scalar(select(func.count()).select_from(Todo).where(*filters))
            or 0
        )
        rows = list(
            await self.session.scalars(
                select(Todo)
                .where(*filters)
                .order_by(Todo.created_at.desc(), Todo.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    async def list_events_after(
        self,
        workspace_id: UUID,
        run_id: UUID,
        *,
        sequence_no: int,
        limit: int = 500,
    ) -> list[AgentRunEvent]:
        return list(
            await self.session.scalars(
                select(AgentRunEvent)
                .where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.sequence_no > sequence_no,
                )
                .order_by(AgentRunEvent.sequence_no)
                .limit(limit)
            )
        )

    async def latest_event_of_type(
        self, workspace_id: UUID, run_id: UUID, event_type: str
    ) -> AgentRunEvent | None:
        return await self.session.scalar(
            select(AgentRunEvent)
            .where(
                AgentRunEvent.workspace_id == workspace_id,
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.event_type == event_type,
            )
            .order_by(AgentRunEvent.sequence_no.desc())
            .limit(1)
        )

    async def event_bounds(self, workspace_id: UUID, run_id: UUID) -> tuple[int | None, int | None]:
        row = (
            await self.session.execute(
                select(
                    func.min(AgentRunEvent.sequence_no),
                    func.max(AgentRunEvent.sequence_no),
                ).where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.run_id == run_id,
                )
            )
        ).one()
        return (
            int(row[0]) if row[0] is not None else None,
            int(row[1]) if row[1] is not None else None,
        )

    async def event_replay_facts(
        self, workspace_id: UUID, run_id: UUID, *, after_sequence: int
    ) -> tuple[int, bool]:
        filters = (
            AgentRunEvent.workspace_id == workspace_id,
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.sequence_no > after_sequence,
        )
        count = int(
            await self.session.scalar(
                select(func.count(AgentRunEvent.id)).where(*filters)
            )
            or 0
        )
        expired_count = int(
            await self.session.scalar(
                select(func.count(AgentRunEvent.id)).where(
                    *filters,
                    AgentRunEvent.payload_json["payload_expired"].as_boolean().is_(True),
                )
            )
            or 0
        )
        return count, expired_count > 0

    async def get_approval_for_run(
        self, workspace_id: UUID, run_id: UUID
    ) -> ApprovalRequest | None:
        return await self.session.scalar(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.workspace_id == workspace_id,
                ApprovalRequest.run_id == run_id,
            )
            .order_by(ApprovalRequest.created_at.desc())
            .limit(1)
        )

    async def list_citation_details(
        self, workspace_id: UUID, run_id: UUID
    ) -> list[tuple[AgentCitation, DocumentChunk, Document]]:
        rows = await self.session.execute(
            select(AgentCitation, DocumentChunk, Document)
            .join(
                DocumentChunk,
                (
                    (DocumentChunk.workspace_id == AgentCitation.workspace_id)
                    & (DocumentChunk.document_version_id == AgentCitation.document_version_id)
                    & (DocumentChunk.id == AgentCitation.chunk_id)
                    & (DocumentChunk.source_span_id == AgentCitation.source_span_id)
                ),
            )
            .join(
                Document,
                (Document.workspace_id == DocumentChunk.workspace_id)
                & (Document.id == DocumentChunk.document_id),
            )
            .where(
                AgentCitation.workspace_id == workspace_id,
                AgentCitation.run_id == run_id,
            )
            .order_by(AgentCitation.ordinal)
        )
        return list(rows.tuples())

    async def cas_todo_status(
        self,
        workspace_id: UUID,
        todo_id: UUID,
        *,
        expected_version: int,
        new_status: str,
        completed_at: Any,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Todo)
                .where(
                    Todo.workspace_id == workspace_id,
                    Todo.id == todo_id,
                    Todo.version == expected_version,
                    Todo.status == "OPEN",
                )
                .values(
                    status=new_status,
                    version=Todo.version + 1,
                    completed_at=completed_at,
                    updated_at=utc_now(),
                )
            ),
        )
        return result.rowcount == 1

    async def list_recent_messages(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> list[AgentMessageRecord]:
        rows = list(
            await self.session.scalars(
                select(AgentMessageRecord)
                .where(
                    AgentMessageRecord.workspace_id == workspace_id,
                    AgentMessageRecord.conversation_id == conversation_id,
                    AgentMessageRecord.role.in_(["SYSTEM", "USER", "ASSISTANT"]),
                )
                .order_by(AgentMessageRecord.sequence_no.desc())
                .limit(limit)
            )
        )
        rows.reverse()
        return rows

    def add_approval(self, approval: ApprovalRequest) -> None:
        self.session.add(approval)

    def add_todo(self, todo: Todo) -> None:
        self.session.add(todo)

    def add_citation(self, citation: AgentCitation) -> None:
        self.session.add(citation)

    async def cas_run_status(
        self,
        workspace_id: UUID,
        run_id: UUID,
        *,
        expected_status: str,
        expected_version: int,
        new_status: str,
        finished_at: Any = None,
    ) -> bool:
        values: dict[str, Any] = {
            "status": new_status,
            "version": AgentRun.version + 1,
            "updated_at": utc_now(),
        }
        if finished_at is not None:
            values["finished_at"] = finished_at
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.id == run_id,
                    AgentRun.status == expected_status,
                    AgentRun.version == expected_version,
                )
                .values(**values)
            ),
        )
        return result.rowcount == 1

    async def cas_approval_status(
        self,
        workspace_id: UUID,
        approval_id: UUID,
        *,
        expected_status: str,
        expected_version: int,
        new_status: str,
        resolved_by_user_id: UUID,
        resolved_at: Any,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(ApprovalRequest)
                .where(
                    ApprovalRequest.workspace_id == workspace_id,
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.status == expected_status,
                    ApprovalRequest.version == expected_version,
                )
                .values(
                    status=new_status,
                    version=ApprovalRequest.version + 1,
                    resolved_by_user_id=resolved_by_user_id,
                    resolved_at=resolved_at,
                    updated_at=utc_now(),
                )
            ),
        )
        return result.rowcount == 1

    async def append_event(
        self,
        workspace_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentRunEvent:
        locked_run_id = await self.session.scalar(
            select(AgentRun.id)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
            .with_for_update()
        )
        if locked_run_id is None:
            raise ValueError("event run does not exist in workspace")
        last_sequence = int(
            await self.session.scalar(
                select(func.max(AgentRunEvent.sequence_no)).where(
                    AgentRunEvent.workspace_id == workspace_id,
                    AgentRunEvent.run_id == run_id,
                )
            )
            or 0
        )
        event = AgentRunEvent(
            workspace_id=workspace_id,
            run_id=run_id,
            sequence_no=last_sequence + 1,
            event_type=event_type,
            payload_json=payload,
        )
        self.session.add(event)
        return event
