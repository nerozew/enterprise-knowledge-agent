from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.ai import AiGenerationRequest, AiGenerationResponse
from app.schemas.common import Page
from app.schemas.inspirations import (
    InspirationCreate,
    InspirationPatch,
    InspirationResponse,
    VersionRequest,
)
from app.schemas.tags import TagCreate, TagPatch, TagReference, TagResponse
from app.services.ai import AiGenerationService, ai_generation_response
from app.services.inspirations import InspirationService
from app.services.tags import TagService
from app.services.workspaces import validate_idempotency_key

router = APIRouter()


def _tag_response(tag, usage_count: int) -> TagResponse:
    reference = TagReference.model_validate(tag)
    return TagResponse(**reference.model_dump(), usage_count=usage_count)


@router.get("/{workspace_id}/inspirations", response_model=Page[InspirationResponse])
async def list_inspirations(
    workspace_id: UUID,
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
    items, total = await InspirationService(
        session, user.id, workspace_id
    ).list(
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


@router.post(
    "/{workspace_id}/inspirations",
    response_model=InspirationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inspiration(
    workspace_id: UUID,
    payload: InspirationCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> InspirationResponse:
    key = validate_idempotency_key(idempotency_key)
    result = await InspirationService(
        session, user.id, workspace_id, request.state.request_id
    ).create(payload, key)
    if result.response_json is not None:
        return InspirationResponse.model_validate(result.response_json)
    return InspirationResponse.model_validate(result.resource)


@router.get(
    "/{workspace_id}/inspirations/{inspiration_id}",
    response_model=InspirationResponse,
)
async def get_inspiration(
    workspace_id: UUID,
    inspiration_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    include_deleted: bool = False,
) -> InspirationResponse:
    item = await InspirationService(session, user.id, workspace_id).get(
        inspiration_id, include_deleted=include_deleted
    )
    return InspirationResponse.model_validate(item)


@router.patch(
    "/{workspace_id}/inspirations/{inspiration_id}",
    response_model=InspirationResponse,
)
async def update_inspiration(
    workspace_id: UUID,
    inspiration_id: UUID,
    payload: InspirationPatch,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> InspirationResponse:
    key = validate_idempotency_key(idempotency_key)
    result = await InspirationService(
        session, user.id, workspace_id, request.state.request_id
    ).update(inspiration_id, payload, key)
    if result.response_json is not None:
        return InspirationResponse.model_validate(result.response_json)
    return InspirationResponse.model_validate(result.resource)


@router.delete(
    "/{workspace_id}/inspirations/{inspiration_id}",
    response_model=InspirationResponse,
)
async def delete_inspiration(
    workspace_id: UUID,
    inspiration_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> InspirationResponse:
    key = validate_idempotency_key(idempotency_key)
    result = await InspirationService(
        session, user.id, workspace_id, request.state.request_id
    ).delete(inspiration_id, version, key)
    if result.response_json is not None:
        return InspirationResponse.model_validate(result.response_json)
    return InspirationResponse.model_validate(result.resource)


@router.post(
    "/{workspace_id}/inspirations/{inspiration_id}/restore",
    response_model=InspirationResponse,
)
async def restore_inspiration(
    workspace_id: UUID,
    inspiration_id: UUID,
    payload: VersionRequest,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> InspirationResponse:
    key = validate_idempotency_key(idempotency_key)
    result = await InspirationService(
        session, user.id, workspace_id, request.state.request_id
    ).restore(inspiration_id, payload.version, key)
    if result.response_json is not None:
        return InspirationResponse.model_validate(result.response_json)
    return InspirationResponse.model_validate(result.resource)


@router.post(
    "/{workspace_id}/inspirations/{inspiration_id}/ai-generations",
    response_model=AiGenerationResponse,
)
async def generate_ai_content(
    workspace_id: UUID,
    inspiration_id: UUID,
    payload: AiGenerationRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> AiGenerationResponse:
    key = validate_idempotency_key(idempotency_key)
    generation = await AiGenerationService(
        session,
        user.id,
        settings,
        workspace_id=workspace_id,
        request_id=request.state.request_id,
    ).generate(inspiration_id, payload, key)
    return ai_generation_response(generation)


@router.get("/{workspace_id}/tags", response_model=list[TagResponse])
async def list_tags(
    workspace_id: UUID, session: SessionDep, user: CurrentUser
) -> list[TagResponse]:
    tags = await TagService(session, user.id, workspace_id).list()
    return [_tag_response(tag, usage_count) for tag, usage_count in tags]


@router.post(
    "/{workspace_id}/tags",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tag(
    workspace_id: UUID,
    payload: TagCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> TagResponse:
    key = validate_idempotency_key(idempotency_key)
    result = await TagService(
        session, user.id, workspace_id, request.state.request_id
    ).create(payload, key)
    if result.response_json is not None:
        return TagResponse.model_validate(result.response_json)
    assert result.tag is not None
    return _tag_response(result.tag, result.usage_count)


@router.patch("/{workspace_id}/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    workspace_id: UUID,
    tag_id: UUID,
    payload: TagPatch,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> TagResponse:
    key = validate_idempotency_key(idempotency_key)
    result = await TagService(
        session, user.id, workspace_id, request.state.request_id
    ).update(tag_id, payload, key)
    if result.response_json is not None:
        return TagResponse.model_validate(result.response_json)
    assert result.tag is not None
    return _tag_response(result.tag, result.usage_count)


@router.delete(
    "/{workspace_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_tag(
    workspace_id: UUID,
    tag_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    version: int = Query(ge=1),
    force: bool = False,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> Response:
    key = validate_idempotency_key(idempotency_key)
    await TagService(
        session, user.id, workspace_id, request.state.request_id
    ).delete(tag_id, version, force=force, idempotency_key=key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
