from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.tags import TagCreate, TagPatch, TagReference, TagResponse
from app.services.tags import TagService

router = APIRouter()


def _tag_response(tag, usage_count: int) -> TagResponse:
    reference = TagReference.model_validate(tag)
    return TagResponse(**reference.model_dump(), usage_count=usage_count)


@router.get("", response_model=list[TagResponse])
async def list_tags(session: SessionDep, user: CurrentUser) -> list[TagResponse]:
    tags = await TagService(session, user.id).list()
    return [_tag_response(tag, usage_count) for tag, usage_count in tags]


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(payload: TagCreate, session: SessionDep, user: CurrentUser) -> TagResponse:
    tag, usage_count = await TagService(session, user.id).create(payload)
    return _tag_response(tag, usage_count)


@router.patch("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: UUID, payload: TagPatch, session: SessionDep, user: CurrentUser
) -> TagResponse:
    tag, usage_count = await TagService(session, user.id).update(tag_id, payload)
    return _tag_response(tag, usage_count)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
    force: bool = False,
) -> Response:
    await TagService(session, user.id).delete(tag_id, version, force=force)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
