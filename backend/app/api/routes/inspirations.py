from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

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


@router.get("", response_model=Page[InspirationResponse])
async def list_inspirations(
    session: SessionDep,
    user: CurrentUser,
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
    items, total = await InspirationService(session, user.id).list(
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
    return Page[InspirationResponse](
        items=[InspirationResponse.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=InspirationResponse, status_code=status.HTTP_201_CREATED)
async def create_inspiration(
    payload: InspirationCreate, session: SessionDep, user: CurrentUser
) -> InspirationResponse:
    item = await InspirationService(session, user.id).create(payload)
    return InspirationResponse.model_validate(item)


@router.get("/{inspiration_id}", response_model=InspirationResponse)
async def get_inspiration(
    inspiration_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    include_deleted: bool = False,
) -> InspirationResponse:
    item = await InspirationService(session, user.id).get(
        inspiration_id, include_deleted=include_deleted
    )
    return InspirationResponse.model_validate(item)


@router.patch("/{inspiration_id}", response_model=InspirationResponse)
async def update_inspiration(
    inspiration_id: UUID,
    payload: InspirationPatch,
    session: SessionDep,
    user: CurrentUser,
) -> InspirationResponse:
    item = await InspirationService(session, user.id).update(inspiration_id, payload)
    return InspirationResponse.model_validate(item)


@router.delete("/{inspiration_id}", response_model=InspirationResponse)
async def delete_inspiration(
    inspiration_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
) -> InspirationResponse:
    item = await InspirationService(session, user.id).delete(inspiration_id, version)
    return InspirationResponse.model_validate(item)


@router.post("/{inspiration_id}/restore", response_model=InspirationResponse)
async def restore_inspiration(
    inspiration_id: UUID,
    payload: VersionRequest,
    session: SessionDep,
    user: CurrentUser,
) -> InspirationResponse:
    item = await InspirationService(session, user.id).restore(inspiration_id, payload.version)
    return InspirationResponse.model_validate(item)
