from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.tags import TagCreate, TagPatch, TagReference, TagResponse
from app.services.tags import TagService

router = APIRouter()


def _deprecated(response: Response, workspace_id: UUID, suffix: str = "") -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/v1/workspaces/{workspace_id}/tags{suffix}>; rel="successor-version"'
    )


def _tag_response(tag, usage_count: int) -> TagResponse:
    reference = TagReference.model_validate(tag)
    return TagResponse(**reference.model_dump(), usage_count=usage_count)


@router.get("", response_model=list[TagResponse])
async def list_tags(
    session: SessionDep, user: CurrentUser, response: Response
) -> list[TagResponse]:
    service = TagService(session, user.id)
    tags = await service.list()
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id)
    return [_tag_response(tag, usage_count) for tag, usage_count in tags]


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: TagCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> TagResponse:
    service = TagService(session, user.id, request_id=request.state.request_id)
    result = await service.create(payload)
    assert result.tag is not None
    tag = result.tag
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{tag.id}")
    return _tag_response(tag, result.usage_count)


@router.patch("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: UUID,
    payload: TagPatch,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> TagResponse:
    service = TagService(session, user.id, request_id=request.state.request_id)
    result = await service.update(tag_id, payload)
    assert result.tag is not None
    tag = result.tag
    assert service.workspace_id is not None
    _deprecated(response, service.workspace_id, f"/{tag_id}")
    return _tag_response(tag, result.usage_count)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
    force: bool = False,
) -> Response:
    service = TagService(session, user.id, request_id=request.state.request_id)
    await service.delete(tag_id, version, force=force)
    assert service.workspace_id is not None
    response.status_code = status.HTTP_204_NO_CONTENT
    _deprecated(response, service.workspace_id, f"/{tag_id}")
    return response
