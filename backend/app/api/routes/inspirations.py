from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.common import Page
from app.schemas.inspirations import (
    InspirationCreate,
    InspirationPatch,
    InspirationResponse,
    VersionRequest,
)
from app.services.inspirations import InspirationService

router = APIRouter()


def _deprecated(response: Response, workspace_id: UUID, suffix: str = "") -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/v1/workspaces/{workspace_id}/inspirations{suffix}>; '
        'rel="successor-version"'
    )


@router.get("", response_model=Page[InspirationResponse])
async def list_inspirations(
    session: SessionDep,
    user: CurrentUser,
    response: Response,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    tag_ids: Annotated[list[UUID] | None, Query()] = None,
    tag_match: Literal["any", "all"] = "any",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: Literal[
        "created_asc", "created_desc", "updated_asc", "updated_desc"
    ] = "updated_desc",
    include_deleted: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[InspirationResponse]:
    service = InspirationService(session, user.id)
    items, total = await service.list(
        q=q,
        tag_ids=tag_ids,
        tag_match=tag_match,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
    )
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id)
    return Page[InspirationResponse](
        items=[InspirationResponse.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=InspirationResponse, status_code=status.HTTP_201_CREATED)
async def create_inspiration(
    payload: InspirationCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> InspirationResponse:
    service = InspirationService(session, user.id, request_id=request.state.request_id)
    result = await service.create(payload)
    item = result.resource
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{item.id}")
    return InspirationResponse.model_validate(item)


@router.get("/{inspiration_id}", response_model=InspirationResponse)
async def get_inspiration(
    inspiration_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    response: Response,
    include_deleted: bool = False,
) -> InspirationResponse:
    service = InspirationService(session, user.id)
    item = await service.get(
        inspiration_id, include_deleted=include_deleted
    )
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{inspiration_id}")
    return InspirationResponse.model_validate(item)


@router.patch("/{inspiration_id}", response_model=InspirationResponse)
async def update_inspiration(
    inspiration_id: UUID,
    payload: InspirationPatch,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> InspirationResponse:
    service = InspirationService(session, user.id, request_id=request.state.request_id)
    result = await service.update(inspiration_id, payload)
    item = result.resource
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{inspiration_id}")
    return InspirationResponse.model_validate(item)


@router.delete("/{inspiration_id}", response_model=InspirationResponse)
async def delete_inspiration(
    inspiration_id: UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
) -> InspirationResponse:
    service = InspirationService(session, user.id, request_id=request.state.request_id)
    result = await service.delete(inspiration_id, version)
    item = result.resource
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{inspiration_id}")
    return InspirationResponse.model_validate(item)


@router.post("/{inspiration_id}/restore", response_model=InspirationResponse)
async def restore_inspiration(
    inspiration_id: UUID,
    payload: VersionRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> InspirationResponse:
    service = InspirationService(session, user.id, request_id=request.state.request_id)
    result = await service.restore(inspiration_id, payload.version)
    item = result.resource
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{inspiration_id}")
    return InspirationResponse.model_validate(item)
