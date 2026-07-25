from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.workspaces import (
    AuditActor,
    AuditLogList,
    AuditLogSummary,
    MemberCreate,
    MemberList,
    MemberPatch,
    MemberSummary,
    WorkspaceCreate,
    WorkspaceList,
    WorkspacePatch,
    WorkspaceSummary,
)
from app.services.workspaces import AuditService, MemberService, WorkspaceService

router = APIRouter()


def workspace_summary(workspace, member) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=workspace.id,
        name=workspace.name,
        kind=workspace.type,
        status=workspace.status,
        version=workspace.version,
        role=member.role,
        member_status=member.status,
        is_default=workspace.personal_owner_user_id == member.user_id,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def member_summary(member, user) -> MemberSummary:
    return MemberSummary(
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        status=member.status,
        version=member.version,
        joined_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.get("", response_model=WorkspaceList)
async def list_workspaces(
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> WorkspaceList:
    rows, total = await WorkspaceService(session, user.id).list(
        page=page, page_size=page_size
    )
    return WorkspaceList(
        items=[workspace_summary(workspace, member) for workspace, member in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=WorkspaceSummary, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> WorkspaceSummary:
    workspace, member, replay = await WorkspaceService(session, user.id).create(
        payload, idempotency_key, request.state.request_id
    )
    if replay is not None:
        return WorkspaceSummary.model_validate(replay)
    return workspace_summary(workspace, member)


@router.get("/{workspace_id}", response_model=WorkspaceSummary)
async def get_workspace(
    workspace_id: UUID, session: SessionDep, user: CurrentUser
) -> WorkspaceSummary:
    workspace, member = await WorkspaceService(session, user.id).get(workspace_id)
    return workspace_summary(workspace, member)


@router.patch("/{workspace_id}", response_model=WorkspaceSummary)
async def update_workspace(
    workspace_id: UUID,
    payload: WorkspacePatch,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceSummary:
    workspace, member = await WorkspaceService(session, user.id).update(
        workspace_id, payload, request.state.request_id
    )
    return workspace_summary(workspace, member)


@router.get("/{workspace_id}/members", response_model=MemberList)
async def list_members(
    workspace_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> MemberList:
    rows, total = await MemberService(session, user.id).list(
        workspace_id, page=page, page_size=page_size
    )
    return MemberList(
        items=[member_summary(member, candidate) for member, candidate in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/{workspace_id}/members",
    response_model=MemberSummary,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    workspace_id: UUID,
    payload: MemberCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> MemberSummary:
    member, candidate, replay = await MemberService(session, user.id).add(
        workspace_id,
        payload,
        idempotency_key,
        request.state.request_id,
    )
    if replay is not None:
        return MemberSummary.model_validate(replay)
    return member_summary(member, candidate)


@router.patch("/{workspace_id}/members/{member_user_id}", response_model=MemberSummary)
async def update_member(
    workspace_id: UUID,
    member_user_id: UUID,
    payload: MemberPatch,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> MemberSummary:
    member, candidate, replay = await MemberService(session, user.id).change(
        workspace_id,
        member_user_id,
        payload,
        idempotency_key,
        request.state.request_id,
    )
    if replay is not None:
        return MemberSummary.model_validate(replay)
    return member_summary(member, candidate)


@router.delete("/{workspace_id}/members/{member_user_id}", response_model=MemberSummary)
async def remove_member(
    workspace_id: UUID,
    member_user_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> MemberSummary:
    member, candidate, replay = await MemberService(session, user.id).change(
        workspace_id,
        member_user_id,
        MemberPatch(version=version, status="REMOVED"),
        idempotency_key,
        request.state.request_id,
        action="membership.removed",
    )
    if replay is not None:
        return MemberSummary.model_validate(replay)
    return member_summary(member, candidate)


@router.get("/{workspace_id}/audit-logs", response_model=AuditLogList)
async def list_audit_logs(
    workspace_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    action: str | None = Query(default=None, min_length=1, max_length=80),
    actor_user_id: UUID | None = None,
    target_type: str | None = Query(default=None, min_length=1, max_length=40),
    target_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    page_size: int = Query(default=50, ge=1, le=100),
) -> AuditLogList:
    rows, next_cursor = await AuditService(session, user.id).list(
        workspace_id,
        action=action,
        actor_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=page_size,
    )
    return AuditLogList(
        items=[
            AuditLogSummary(
                id=log.id,
                workspace_id=log.workspace_id,
                actor=AuditActor(
                    user_id=actor.id,
                    display_name=actor.display_name,
                    role=log.actor_role,
                ),
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                result=log.result,
                request_id=log.request_id,
                metadata=log.metadata_json,
                created_at=log.created_at,
            )
            for log, actor in rows
        ],
        next_cursor=next_cursor,
    )
