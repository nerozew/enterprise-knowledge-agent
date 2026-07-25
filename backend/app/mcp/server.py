from __future__ import annotations

import logging
import secrets
import sys
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from app.core.config import get_settings
from app.core.database import SessionFactory
from app.core.errors import AppError
from app.models.entities import User
from app.schemas.ai import AiGenerationRequest
from app.schemas.inspirations import InspirationCreate, InspirationPatch, InspirationResponse
from app.schemas.tags import TagCreate, TagPatch, TagReference, TagResponse
from app.services.ai import AiGenerationService, ai_generation_response
from app.services.inspirations import InspirationService
from app.services.tags import TagService

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
mcp = FastMCP("日常灵感智能助手", instructions="管理当前本机用户的灵感与标签。")


def _configured_user_id() -> UUID:
    if not settings.mcp_user_id:
        raise ValueError("MCP_USER_ID 未配置")
    try:
        return UUID(settings.mcp_user_id)
    except ValueError as exc:
        raise ValueError("MCP_USER_ID 不是有效 UUID") from exc


def _verify_token(local_token: str) -> UUID:
    if not settings.mcp_local_token or not secrets.compare_digest(
        local_token, settings.mcp_local_token
    ):
        raise ValueError("MCP 本地令牌无效")
    return _configured_user_id()


@asynccontextmanager
async def _user_session(user_id: UUID):
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        if user is None or not user.is_active:
            raise ValueError("MCP 用户不存在或已停用")
        yield session


def _as_dict(model) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _mcp_error(exc: AppError) -> ValueError:
    return ValueError(f"{exc.code}: {exc.message}")


def _tag_response(tag, usage_count: int) -> TagResponse:
    reference = TagReference.model_validate(tag)
    return TagResponse(**reference.model_dump(), usage_count=usage_count)


@mcp.tool()
async def inspiration_search(
    local_token: str,
    q: str | None = None,
    tag_ids: list[str] | None = None,
    tag_match: str = "any",
    include_deleted: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """搜索当前用户灵感。local_token 为本机令牌；limit 取值 1 到 100。"""
    user_id = _verify_token(local_token)
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    if tag_match not in {"any", "all"}:
        raise ValueError("tag_match 只能为 any 或 all")
    try:
        async with _user_session(user_id) as session:
            items, total = await InspirationService(session, user_id).list(
                q=q,
                tag_ids=[UUID(value) for value in (tag_ids or [])],
                tag_match=tag_match,
                include_deleted=include_deleted,
                page=1,
                page_size=limit,
            )
            return {
                "items": [_as_dict(InspirationResponse.model_validate(item)) for item in items],
                "total": total,
            }
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def inspiration_get(local_token: str, inspiration_id: str) -> dict[str, Any]:
    """按 UUID 获取当前用户的一条未删除灵感。"""
    user_id = _verify_token(local_token)
    try:
        async with _user_session(user_id) as session:
            item = await InspirationService(session, user_id).get(UUID(inspiration_id))
            return _as_dict(InspirationResponse.model_validate(item))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def inspiration_create(
    local_token: str,
    content: str,
    title: str | None = None,
    source_url: str | None = None,
    tag_ids: list[str] | None = None,
) -> dict[str, Any]:
    """为当前用户创建灵感；content 必填，tag_ids 只能引用当前用户标签。"""
    user_id = _verify_token(local_token)
    try:
        payload = InspirationCreate(
            title=title,
            content=content,
            source_url=source_url,
            tag_ids=[UUID(value) for value in (tag_ids or [])],
        )
        async with _user_session(user_id) as session:
            result = await InspirationService(session, user_id).create(payload)
            item = result.resource
            return _as_dict(InspirationResponse.model_validate(item))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def inspiration_update(
    local_token: str,
    inspiration_id: str,
    version: int,
    title: str | None = None,
    content: str | None = None,
    source_url: str | None = None,
    tag_ids: list[str] | None = None,
) -> dict[str, Any]:
    """乐观锁更新灵感；version 必须等于服务端当前版本。"""
    user_id = _verify_token(local_token)
    values: dict[str, Any] = {"version": version}
    if title is not None:
        values["title"] = title
    if content is not None:
        values["content"] = content
    if source_url is not None:
        values["source_url"] = source_url
    if tag_ids is not None:
        values["tag_ids"] = [UUID(value) for value in tag_ids]
    try:
        async with _user_session(user_id) as session:
            result = await InspirationService(session, user_id).update(
                UUID(inspiration_id), InspirationPatch(**values)
            )
            item = result.resource
            return _as_dict(InspirationResponse.model_validate(item))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def inspiration_delete(
    local_token: str, inspiration_id: str, version: int, confirm: bool = False
) -> dict[str, Any]:
    """软删除灵感。高风险操作，只有 confirm=true 时才会执行。"""
    user_id = _verify_token(local_token)
    if not confirm:
        raise ValueError("删除前必须明确设置 confirm=true")
    try:
        async with _user_session(user_id) as session:
            result = await InspirationService(session, user_id).delete(
                UUID(inspiration_id), version
            )
            item = result.resource
            return _as_dict(InspirationResponse.model_validate(item))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def inspiration_restore(
    local_token: str, inspiration_id: str, version: int
) -> dict[str, Any]:
    """恢复当前用户已软删除的灵感。"""
    user_id = _verify_token(local_token)
    try:
        async with _user_session(user_id) as session:
            result = await InspirationService(session, user_id).restore(
                UUID(inspiration_id), version
            )
            item = result.resource
            return _as_dict(InspirationResponse.model_validate(item))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def tag_list(local_token: str) -> list[dict[str, Any]]:
    """列出当前用户的全部标签。"""
    user_id = _verify_token(local_token)
    async with _user_session(user_id) as session:
        tags = await TagService(session, user_id).list()
        return [_as_dict(_tag_response(tag, usage_count)) for tag, usage_count in tags]


@mcp.tool()
async def tag_create(local_token: str, name: str, color: str | None = None) -> dict[str, Any]:
    """创建当前用户标签；color 使用 #RRGGBB 或 #RRGGBBAA。"""
    user_id = _verify_token(local_token)
    try:
        async with _user_session(user_id) as session:
            result = await TagService(session, user_id).create(
                TagCreate(name=name, color=color)
            )
            assert result.tag is not None
            return _as_dict(_tag_response(result.tag, result.usage_count))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def tag_update(
    local_token: str,
    tag_id: str,
    version: int,
    name: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """乐观锁更新标签；version 必须等于服务端当前版本。"""
    user_id = _verify_token(local_token)
    values: dict[str, Any] = {"version": version}
    if name is not None:
        values["name"] = name
    if color is not None:
        values["color"] = color
    try:
        async with _user_session(user_id) as session:
            result = await TagService(session, user_id).update(
                UUID(tag_id), TagPatch(**values)
            )
            assert result.tag is not None
            return _as_dict(_tag_response(result.tag, result.usage_count))
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def tag_delete(
    local_token: str,
    tag_id: str,
    version: int,
    force: bool = False,
    confirm: bool = False,
) -> dict[str, bool]:
    """永久删除标签并解除关联。高风险操作，只有 confirm=true 时执行。"""
    user_id = _verify_token(local_token)
    if not confirm:
        raise ValueError("删除前必须明确设置 confirm=true")
    try:
        async with _user_session(user_id) as session:
            await TagService(session, user_id).delete(UUID(tag_id), version, force=force)
            return {"deleted": True}
    except AppError as exc:
        raise _mcp_error(exc) from exc


@mcp.tool()
async def ai_generate(
    local_token: str,
    inspiration_id: str,
    operation: str,
    idempotency_key: str,
    instruction: str | None = None,
) -> dict[str, Any]:
    """为灵感生成 summary 或 expand；idempotency_key 长度为 8 到 128。"""
    user_id = _verify_token(local_token)
    if not 8 <= len(idempotency_key) <= 128:
        raise ValueError("idempotency_key 长度必须为 8 到 128")
    try:
        payload = AiGenerationRequest(operation=operation, instruction=instruction)
        async with _user_session(user_id) as session:
            generation = await AiGenerationService(session, user_id, settings).generate(
                UUID(inspiration_id), payload, idempotency_key
            )
            return _as_dict(ai_generation_response(generation))
    except AppError as exc:
        raise _mcp_error(exc) from exc


if __name__ == "__main__":
    logger.info("启动 MCP stdio 服务")
    mcp.run(transport="stdio")
