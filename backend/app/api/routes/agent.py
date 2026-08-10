from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.errors import AppError
from app.core.security import access_token_expires_at
from app.schemas.agent import (
    AgentRunCancel,
    AgentRunCreate,
    AgentRunSnapshot,
    ApprovalDecision,
    ApprovalDecisionResult,
    ApprovalList,
    ApprovalSummary,
    ConversationCreate,
    ConversationList,
    ConversationPatch,
    ConversationSummary,
    OperationAccepted,
    TodoList,
    TodoPatch,
    TodoSummary,
)
from app.services.agent_api import (
    AgentApiService,
    AgentEventStreamService,
    ApprovalDecisionService,
)

router = APIRouter()

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _session_factory(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    bind = session.bind
    if not isinstance(bind, AsyncEngine):
        raise AppError("internal_error", "数据库会话配置无效", 500)
    return async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=False)


def _validate_if_match(if_match: str | None, version: int) -> None:
    if if_match is None:
        return
    normalized = if_match.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    try:
        supplied = int(normalized)
    except ValueError as exc:
        raise AppError("invalid_if_match", "If-Match 必须包含资源版本号", 422) from exc
    if supplied != version:
        raise AppError("version_conflict", "If-Match 与请求版本不一致", 409)


@router.get("/{workspace_id}/conversations", response_model=ConversationList)
async def list_conversations(
    workspace_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_filter: Annotated[
        Literal["ACTIVE", "ARCHIVED"] | None,
        Query(alias="status"),
    ] = None,
) -> ConversationList:
    items, total = await AgentApiService(session, user.id).list_conversations(
        workspace_id,
        page=page,
        page_size=page_size,
        status=status_filter,
    )
    return ConversationList(items=items, page=page, page_size=page_size, total=total)


@router.post(
    "/{workspace_id}/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    workspace_id: UUID,
    payload: ConversationCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
) -> ConversationSummary:
    return await AgentApiService(
        session, user.id, request_id=_request_id(request)
    ).create_conversation(
        workspace_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/{workspace_id}/conversations/{conversation_id}",
    response_model=ConversationSummary,
)
async def get_conversation(
    workspace_id: UUID,
    conversation_id: UUID,
    session: SessionDep,
    user: CurrentUser,
) -> ConversationSummary:
    return await AgentApiService(session, user.id).get_conversation(workspace_id, conversation_id)


@router.patch(
    "/{workspace_id}/conversations/{conversation_id}",
    response_model=ConversationSummary,
)
async def update_conversation(
    workspace_id: UUID,
    conversation_id: UUID,
    payload: ConversationPatch,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
    if_match: IfMatch = None,
) -> ConversationSummary:
    _validate_if_match(if_match, payload.version)
    return await AgentApiService(
        session, user.id, request_id=_request_id(request)
    ).update_conversation(
        workspace_id,
        conversation_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{workspace_id}/conversations/{conversation_id}/runs",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    workspace_id: UUID,
    conversation_id: UUID,
    payload: AgentRunCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
) -> OperationAccepted:
    return await AgentApiService(session, user.id, request_id=_request_id(request)).create_run(
        workspace_id,
        conversation_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{workspace_id}/agent-runs/{run_id}", response_model=AgentRunSnapshot)
async def get_run(
    workspace_id: UUID,
    run_id: UUID,
    session: SessionDep,
    user: CurrentUser,
) -> AgentRunSnapshot:
    return await AgentApiService(session, user.id).get_run(workspace_id, run_id)


@router.post(
    "/{workspace_id}/agent-runs/{run_id}/cancel",
    response_model=AgentRunSnapshot,
)
async def cancel_run(
    workspace_id: UUID,
    run_id: UUID,
    payload: AgentRunCancel,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
    if_match: IfMatch = None,
) -> AgentRunSnapshot:
    _validate_if_match(if_match, payload.version)
    return await AgentApiService(session, user.id, request_id=_request_id(request)).cancel_run(
        workspace_id,
        run_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{workspace_id}/approvals", response_model=ApprovalList)
async def list_approvals(
    workspace_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_filter: Annotated[
        Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"] | None,
        Query(alias="status"),
    ] = None,
) -> ApprovalList:
    items, total = await AgentApiService(session, user.id).list_approvals(
        workspace_id,
        page=page,
        page_size=page_size,
        status=status_filter,
    )
    return ApprovalList(items=items, page=page, page_size=page_size, total=total)


@router.get("/{workspace_id}/approvals/{approval_id}", response_model=ApprovalSummary)
async def get_approval(
    workspace_id: UUID,
    approval_id: UUID,
    session: SessionDep,
    user: CurrentUser,
) -> ApprovalSummary:
    return await AgentApiService(session, user.id).get_approval(workspace_id, approval_id)


@router.post(
    "/{workspace_id}/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResult,
)
async def decide_approval(
    workspace_id: UUID,
    approval_id: UUID,
    payload: ApprovalDecision,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
    if_match: IfMatch = None,
) -> ApprovalDecisionResult:
    _validate_if_match(if_match, payload.version)
    return await ApprovalDecisionService(
        session,
        _session_factory(session),
        user.id,
        request_id=_request_id(request),
    ).decide(
        workspace_id,
        approval_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{workspace_id}/todos", response_model=TodoList)
async def list_todos(
    workspace_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_filter: Annotated[
        Literal["OPEN", "COMPLETED", "CANCELLED"] | None,
        Query(alias="status"),
    ] = None,
    assignee_id: UUID | None = None,
) -> TodoList:
    items, total = await AgentApiService(session, user.id).list_todos(
        workspace_id,
        page=page,
        page_size=page_size,
        status=status_filter,
        assignee_id=assignee_id,
    )
    return TodoList(items=items, page=page, page_size=page_size, total=total)


@router.get("/{workspace_id}/todos/{todo_id}", response_model=TodoSummary)
async def get_todo(
    workspace_id: UUID,
    todo_id: UUID,
    session: SessionDep,
    user: CurrentUser,
) -> TodoSummary:
    return await AgentApiService(session, user.id).get_todo(workspace_id, todo_id)


@router.patch("/{workspace_id}/todos/{todo_id}", response_model=TodoSummary)
async def update_todo(
    workspace_id: UUID,
    todo_id: UUID,
    payload: TodoPatch,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
    if_match: IfMatch = None,
) -> TodoSummary:
    _validate_if_match(if_match, payload.version)
    return await AgentApiService(session, user.id, request_id=_request_id(request)).update_todo(
        workspace_id,
        todo_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{workspace_id}/agent-runs/{run_id}/events")
async def stream_run_events(
    workspace_id: UUID,
    run_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    service = AgentEventStreamService(
        _session_factory(session),
        user.id,
        workspace_id,
        run_id,
        token_expires_at=access_token_expires_at(token, settings),
    )
    cursor, _terminal = await service.prepare(last_event_id)
    return StreamingResponse(
        service.stream(cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
