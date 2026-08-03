from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import AgentMessage
from app.repositories.agent import AgentRepository


class ShortTermConversationMemory:
    """Loads bounded messages from exactly one Workspace conversation."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        max_messages: int = 12,
        max_characters: int = 12_000,
    ) -> None:
        if not 1 <= max_messages <= 100:
            raise ValueError("max_messages must be between 1 and 100")
        if not 256 <= max_characters <= 100_000:
            raise ValueError("max_characters must be between 256 and 100000")
        self.repo = AgentRepository(session)
        self.max_messages = max_messages
        self.max_characters = max_characters

    async def load(self, workspace_id: UUID, conversation_id: UUID) -> list[AgentMessage]:
        records = await self.repo.list_recent_messages(
            workspace_id,
            conversation_id,
            limit=self.max_messages,
        )
        remaining = self.max_characters
        bounded: list[AgentMessage] = []
        for record in reversed(records):
            if remaining <= 0:
                break
            content = record.content[:remaining]
            if not content:
                continue
            bounded.append(
                AgentMessage.model_validate({"role": record.role.lower(), "content": content})
            )
            remaining -= len(content)
        bounded.reverse()
        return bounded
