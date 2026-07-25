from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import APIModel


class AiGenerationRequest(APIModel):
    operation: Literal["summary", "expand"]
    instruction: str | None = Field(default=None, max_length=500)


class AiGenerationResponse(APIModel):
    generation_id: UUID
    operation: Literal["summary", "expand"]
    mode: Literal["provider", "fallback"]
    result: str
    warning: str | None = None
    created_at: datetime

