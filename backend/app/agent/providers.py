from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from app.agent.models import AgentMessage, RouteDecision, RuntimeContext, TodoCreateInput
from app.core.config import Settings


class AgentProviderUnavailableError(RuntimeError):
    """Sanitized failure raised when the configured Agent model is unavailable."""


class OpenAICompatibleAgentModel:
    """Strict Agent model adapter; it never falls back to fabricated local answers."""

    def __init__(self, settings: Settings) -> None:
        self.url = str(settings.ai_provider_url or "").strip()
        self.api_key = str(settings.ai_api_key or "").strip()
        self.model = str(settings.ai_model or "").strip()
        self.timeout = settings.ai_timeout_seconds

    def _require_configured(self) -> None:
        if not self.url or not self.model:
            raise AgentProviderUnavailableError("Agent model provider is not configured")

    async def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        structured: bool,
    ) -> str:
        self._require_configured()
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if structured:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentProviderUnavailableError("Agent model provider request failed") from exc
        if not isinstance(content, str) or not content.strip():
            raise AgentProviderUnavailableError("Agent model provider returned no content")
        return content.strip()

    @staticmethod
    def _history(messages: Sequence[AgentMessage]) -> list[dict[str, str]]:
        return [message.model_dump() for message in messages[-12:]]

    async def decide(
        self, *, messages: Sequence[AgentMessage], query: str
    ) -> RouteDecision:
        content = await self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Classify the latest request into exactly one route. Return only JSON "
                        "with keys route, confidence, reason_code, normalized_query, "
                        "needs_clarification. Routes: DIRECT_ANSWER, KNOWLEDGE_QA, "
                        "INSPIRATION_SEARCH, TODO_CREATE. Reason codes: CASUAL_OR_GENERAL, "
                        "WORKSPACE_KNOWLEDGE, INSPIRATION_LOOKUP, ACTION_REQUEST, AMBIGUOUS. "
                        "Use TODO_CREATE only for an explicit request to create a todo."
                    ),
                },
                *self._history(messages),
                {"role": "user", "content": query},
            ],
            structured=True,
        )
        try:
            return RouteDecision.model_validate_json(content)
        except ValueError as exc:
            raise AgentProviderUnavailableError(
                "Agent route response failed schema validation"
            ) from exc

    async def answer(
        self,
        *,
        messages: Sequence[AgentMessage],
        query: str,
        context: RuntimeContext,
    ) -> str:
        del context
        return await self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Answer the general conversational request directly and concisely. "
                        "Do not claim access to enterprise facts and do not perform actions."
                    ),
                },
                *self._history(messages),
                {"role": "user", "content": query},
            ],
            structured=False,
        )

    async def extract(
        self, *, messages: Sequence[AgentMessage], query: str
    ) -> TodoCreateInput:
        content = await self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract todo arguments and return only JSON. Allowed keys are title, "
                        "description, due_at, assignee_id. title is required. Omit unknown "
                        "optional values. Never invent workspace or user identifiers."
                    ),
                },
                *self._history(messages),
                {"role": "user", "content": query},
            ],
            structured=True,
        )
        try:
            decoded = json.loads(content)
            return TodoCreateInput.model_validate(decoded)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise AgentProviderUnavailableError(
                "Agent todo response failed schema validation"
            ) from exc
