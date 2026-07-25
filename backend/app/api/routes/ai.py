from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.errors import AppError
from app.schemas.ai import AiGenerationRequest, AiGenerationResponse
from app.services.ai import AiGenerationService, ai_generation_response

router = APIRouter()


@router.post("/{inspiration_id}/ai-generations", response_model=AiGenerationResponse)
async def generate_ai_content(
    inspiration_id: UUID,
    payload: AiGenerationRequest,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AiGenerationResponse:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise AppError("invalid_idempotency_key", "Idempotency-Key 长度必须为 8 到 128", 422)
    generation = await AiGenerationService(session, user.id, settings).generate(
        inspiration_id, payload, idempotency_key
    )
    return ai_generation_response(generation)
