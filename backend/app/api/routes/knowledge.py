from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    status,
)

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.knowledge import (
    DocumentFormat,
    DocumentList,
    DocumentPatch,
    DocumentSummary,
    DocumentVersionSummary,
    IngestionJobSummary,
    JobVersionRequest,
    SearchRequest,
    SearchResponse,
    UploadAccepted,
)
from app.services.knowledge import (
    KnowledgeService,
    document_snapshot,
    job_snapshot,
    version_snapshot,
)

router = APIRouter()


@router.post(
    "/{workspace_id}/documents",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    workspace_id: UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(min_length=1, max_length=240)] = None,
    duplicate_policy: Annotated[
        Literal["REUSE_EXISTING", "CREATE_NEW"], Form()
    ] = "REUSE_EXISTING",
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> UploadAccepted:
    maximum = settings.rag_max_upload_mib * 1024 * 1024
    content = await file.read(maximum + 1)
    document, version, job, reused = await KnowledgeService(
        session, user.id, workspace_id, settings, request.state.request_id
    ).upload(
        filename=file.filename or "",
        mime_type=file.content_type,
        content=content,
        title=title,
        duplicate_policy=duplicate_policy,
        idempotency_key=idempotency_key,
    )
    return UploadAccepted(
        document=DocumentSummary.model_validate(document_snapshot(document)),
        version=DocumentVersionSummary.model_validate(version_snapshot(version)),
        job=IngestionJobSummary.model_validate(job_snapshot(job)),
        reused=reused,
    )


@router.post(
    "/{workspace_id}/documents/{document_id}/versions",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document_version(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    duplicate_policy: Annotated[
        Literal["REUSE_EXISTING", "CREATE_NEW"], Form()
    ] = "CREATE_NEW",
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> UploadAccepted:
    maximum = settings.rag_max_upload_mib * 1024 * 1024
    content = await file.read(maximum + 1)
    document, version, job, reused = await KnowledgeService(
        session, user.id, workspace_id, settings, request.state.request_id
    ).upload(
        filename=file.filename or "",
        mime_type=file.content_type,
        content=content,
        title=None,
        duplicate_policy=duplicate_policy,
        idempotency_key=idempotency_key,
        document_id=document_id,
    )
    return UploadAccepted(
        document=DocumentSummary.model_validate(document_snapshot(document)),
        version=DocumentVersionSummary.model_validate(version_snapshot(version)),
        job=IngestionJobSummary.model_validate(job_snapshot(job)),
        reused=reused,
    )


@router.get("/{workspace_id}/documents", response_model=DocumentList)
async def list_documents(
    workspace_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    include_deleted: bool = False,
    q: Annotated[str | None, Query(min_length=1, max_length=240)] = None,
    source_format: Annotated[DocumentFormat | None, Query()] = None,
    status_filter: Annotated[
        Literal["ACTIVE", "DELETED"] | None,
        Query(alias="status"),
    ] = None,
    version_status: Annotated[
        Literal["PUBLISHING", "PUBLISHED", "SUPERSEDED", "FAILED"] | None,
        Query(),
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> DocumentList:
    items, total = await KnowledgeService(session, user.id, workspace_id, settings).list(
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
        q=q,
        source_format=source_format,
        status=status_filter,
        version_status=version_status,
    )
    return DocumentList(
        items=[
            DocumentSummary.model_validate(document_snapshot(document))
            for document in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{workspace_id}/documents/{document_id}", response_model=DocumentSummary
)
async def get_document(
    workspace_id: UUID,
    document_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> DocumentSummary:
    document = await KnowledgeService(
        session, user.id, workspace_id, settings
    ).get(document_id)
    return DocumentSummary.model_validate(document_snapshot(document))


@router.patch(
    "/{workspace_id}/documents/{document_id}", response_model=DocumentSummary
)
async def update_document(
    workspace_id: UUID,
    document_id: UUID,
    payload: DocumentPatch,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> DocumentSummary:
    document = await KnowledgeService(
        session, user.id, workspace_id, settings, request.state.request_id
    ).update_document(
        document_id,
        title=payload.title,
        expected_version=payload.version,
        idempotency_key=idempotency_key,
    )
    return DocumentSummary.model_validate(document_snapshot(document))


@router.delete(
    "/{workspace_id}/documents/{document_id}", response_model=DocumentSummary
)
async def delete_document(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    version: int = Query(ge=1),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> DocumentSummary:
    document = await KnowledgeService(
        session, user.id, workspace_id, settings, request.state.request_id
    ).delete_document(
        document_id,
        expected_version=version,
        idempotency_key=idempotency_key,
    )
    return DocumentSummary.model_validate(document_snapshot(document))


@router.get(
    "/{workspace_id}/documents/{document_id}/versions",
    response_model=list[DocumentVersionSummary],
)
async def list_document_versions(
    workspace_id: UUID,
    document_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> list[DocumentVersionSummary]:
    versions = await KnowledgeService(
        session, user.id, workspace_id, settings
    ).versions(document_id)
    return [
        DocumentVersionSummary.model_validate(version_snapshot(version))
        for version in versions
    ]


@router.get(
    "/{workspace_id}/ingestion-jobs/{job_id}",
    response_model=IngestionJobSummary,
)
async def get_ingestion_job(
    workspace_id: UUID,
    job_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> IngestionJobSummary:
    job = await KnowledgeService(
        session, user.id, workspace_id, settings
    ).get_job(job_id)
    return IngestionJobSummary.model_validate(job_snapshot(job))


@router.post(
    "/{workspace_id}/ingestion-jobs/{job_id}/retry",
    response_model=IngestionJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_ingestion_job(
    workspace_id: UUID,
    job_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> IngestionJobSummary:
    job = await KnowledgeService(
        session, user.id, workspace_id, settings
    ).retry(job_id, idempotency_key=idempotency_key)
    return IngestionJobSummary.model_validate(job_snapshot(job))


@router.post(
    "/{workspace_id}/ingestion-jobs/{job_id}/cancel",
    response_model=IngestionJobSummary,
)
async def cancel_ingestion_job(
    workspace_id: UUID,
    job_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    payload: JobVersionRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> IngestionJobSummary:
    job = await KnowledgeService(
        session, user.id, workspace_id, settings
    ).cancel(
        job_id,
        expected_version=payload.version,
        idempotency_key=idempotency_key,
    )
    return IngestionJobSummary.model_validate(job_snapshot(job))


@router.post("/{workspace_id}/search", response_model=SearchResponse)
async def search_knowledge(
    workspace_id: UUID,
    payload: SearchRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> SearchResponse:
    return await KnowledgeService(
        session, user.id, workspace_id, settings, request.state.request_id
    ).search(payload)
