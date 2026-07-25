from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AiGeneration


class AiGenerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_key(self, user_id: UUID, idempotency_key: str) -> AiGeneration | None:
        return await self.session.scalar(
            select(AiGeneration).where(
                AiGeneration.user_id == user_id,
                AiGeneration.idempotency_key == idempotency_key,
            )
        )

    def add(self, generation: AiGeneration) -> None:
        self.session.add(generation)

