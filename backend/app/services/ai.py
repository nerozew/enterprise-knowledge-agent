from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.entities import AiGeneration
from app.repositories.ai_generations import AiGenerationRepository
from app.repositories.inspirations import InspirationRepository
from app.schemas.ai import AiGenerationRequest, AiGenerationResponse


@dataclass(frozen=True)
class ProviderResult:
    output: str
    mode: str
    provider: str


class AIProvider(Protocol):
    async def generate(
        self, operation: str, title: str | None, content: str, instructions: str | None
    ) -> ProviderResult: ...


class DeterministicFallbackProvider:
    async def generate(
        self, operation: str, title: str | None, content: str, instructions: str | None
    ) -> ProviderResult:
        compact = re.sub(r"\s+", " ", content).strip()
        topic = (title or compact[:24] or "这条灵感").strip()
        if operation == "summary":
            excerpt = compact[:160] + ("……" if len(compact) > 160 else "")
            output = f"摘要：{excerpt}"
        else:
            excerpt = compact[:240] + ("……" if len(compact) > 240 else "")
            output = (
                f"围绕“{topic}”继续展开：\n\n"
                f"原始想法：{excerpt}\n\n"
                "可以进一步明确它要解决的问题、适用场景和预期结果；"
                "再拆分为一个今天就能完成的小步骤，并记录验证结果。"
            )
        return ProviderResult(output=output, mode="fallback", provider="deterministic-local")


class HttpAIProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(
        self, operation: str, title: str | None, content: str, instructions: str | None
    ) -> ProviderResult:
        task = "生成简洁准确的中文摘要" if operation == "summary" else "扩展为可执行的中文创意方案"
        prompt = f"任务：{task}\n标题：{title or '无'}\n正文：{content}"
        if instructions:
            prompt += f"\n补充要求：{instructions}"
        headers = {"Authorization": f"Bearer {self.settings.ai_api_key}"}
        payload = {
            "model": self.settings.ai_model,
            "messages": [
                {"role": "system", "content": "你是日常灵感整理助手，只输出结果正文。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            response = await client.post(
                str(self.settings.ai_provider_url), headers=headers, json=payload
            )
            response.raise_for_status()
            data = response.json()
        try:
            output = str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI Provider 返回格式无效") from exc
        if not output:
            raise ValueError("AI Provider 返回空内容")
        return ProviderResult(output=output, mode="provider", provider=str(self.settings.ai_model))


class ResilientAIProvider:
    def __init__(self, primary: AIProvider | None, fallback: AIProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    async def generate(
        self, operation: str, title: str | None, content: str, instructions: str | None
    ) -> ProviderResult:
        if self.primary is not None:
            try:
                return await self.primary.generate(operation, title, content, instructions)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
        return await self.fallback.generate(operation, title, content, instructions)


def build_provider(settings: Settings) -> AIProvider:
    primary = HttpAIProvider(settings) if settings.ai_configured else None
    return ResilientAIProvider(primary, DeterministicFallbackProvider())


class AiGenerationService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: UUID,
        settings: Settings,
        provider: AIProvider | None = None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.generations = AiGenerationRepository(session)
        self.inspirations = InspirationRepository(session)
        self.provider = provider or build_provider(settings)

    @staticmethod
    def request_hash(
        inspiration_id: UUID,
        payload: AiGenerationRequest,
        title: str | None,
        content: str,
    ) -> str:
        canonical = json.dumps(
            {
                "inspiration_id": str(inspiration_id),
                "operation": payload.operation,
                "instruction": payload.instruction,
                "title": title,
                "content": content,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def generate(
        self,
        inspiration_id: UUID,
        payload: AiGenerationRequest,
        idempotency_key: str,
    ) -> AiGeneration:
        inspiration = await self.inspirations.get_owned(inspiration_id, self.user_id)
        if inspiration is None:
            raise AppError("not_found", "灵感不存在", 404)
        digest = self.request_hash(inspiration_id, payload, inspiration.title, inspiration.content)
        existing = await self.generations.get_by_key(self.user_id, idempotency_key)
        if existing:
            if existing.request_hash != digest:
                raise AppError("idempotency_conflict", "该幂等键已用于不同请求", 409)
            return existing

        result = await self.provider.generate(
            payload.operation, inspiration.title, inspiration.content, payload.instruction
        )
        generation = AiGeneration(
            user_id=self.user_id,
            inspiration_id=inspiration.id,
            operation=payload.operation,
            idempotency_key=idempotency_key,
            request_hash=digest,
            mode=result.mode,
            provider=result.provider,
            output=result.output,
        )
        self.generations.add(generation)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = await self.generations.get_by_key(self.user_id, idempotency_key)
            if winner is None or winner.request_hash != digest:
                raise AppError(
                    "idempotency_conflict", "该幂等键已用于不同请求", 409
                ) from exc
            return winner
        await self.session.refresh(generation)
        return generation


def ai_generation_response(generation: AiGeneration) -> AiGenerationResponse:
    warning = None
    if generation.mode == "fallback":
        warning = "AI 服务未配置或暂不可用，当前结果由本地确定性降级逻辑生成。"
    return AiGenerationResponse(
        generation_id=generation.id,
        operation=generation.operation,
        mode=generation.mode,
        result=generation.output,
        warning=warning,
        created_at=generation.created_at,
    )
