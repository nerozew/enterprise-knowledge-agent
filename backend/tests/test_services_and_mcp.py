from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.entities import InspirationTag, User
from app.services.ai import DeterministicFallbackProvider, ResilientAIProvider


class FailingProvider:
    async def generate(self, operation, title, content, instructions):
        request = httpx.Request("POST", "https://provider.invalid")
        raise httpx.ConnectError("provider unavailable", request=request)


def test_inspiration_tag_has_tag_id_leading_index():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in InspirationTag.__table__.indexes
    }
    assert indexes["ix_inspiration_tags_tag_id"] == ("tag_id",)


def _load_tag_index_migration():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0002_index_inspiration_tags_tag_id.py"
    )
    spec = importlib.util.spec_from_file_location("tag_index_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationOpRecorder:
    def __init__(self, dialect_name: str) -> None:
        self.dialect_name = dialect_name
        self.calls: list[tuple[str, tuple, dict]] = []

    def get_bind(self):
        dialect = type("Dialect", (), {"name": self.dialect_name})()
        return type("Bind", (), {"dialect": dialect})()

    def execute(self, statement: str) -> None:
        self.calls.append(("execute", (statement,), {}))

    def create_index(self, *args, **kwargs) -> None:
        self.calls.append(("create_index", args, kwargs))

    def drop_index(self, *args, **kwargs) -> None:
        self.calls.append(("drop_index", args, kwargs))


def test_tag_index_migration_mysql_renames_index_in_both_directions(monkeypatch):
    migration = _load_tag_index_migration()
    recorder = MigrationOpRecorder("mysql")
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()
    migration.downgrade()

    assert recorder.calls == [
        (
            "execute",
            (
                "ALTER TABLE inspiration_tags "
                "RENAME INDEX fk_inspiration_tags_tag_id_tags "
                "TO ix_inspiration_tags_tag_id",
            ),
            {},
        ),
        (
            "execute",
            (
                "ALTER TABLE inspiration_tags "
                "RENAME INDEX ix_inspiration_tags_tag_id "
                "TO fk_inspiration_tags_tag_id_tags",
            ),
            {},
        ),
    ]


def test_tag_index_migration_other_dialect_creates_and_drops_index(monkeypatch):
    migration = _load_tag_index_migration()
    recorder = MigrationOpRecorder("sqlite")
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()
    migration.downgrade()

    assert recorder.calls == [
        (
            "create_index",
            (
                "ix_inspiration_tags_tag_id",
                "inspiration_tags",
                ["tag_id"],
            ),
            {},
        ),
        (
            "drop_index",
            ("ix_inspiration_tags_tag_id",),
            {"table_name": "inspiration_tags"},
        ),
    ]


async def test_provider_failure_uses_deterministic_fallback():
    provider = ResilientAIProvider(FailingProvider(), DeterministicFallbackProvider())
    first = await provider.generate("summary", "标题", "正文内容", None)
    second = await provider.generate("summary", "标题", "正文内容", None)
    assert first.mode == "fallback"
    assert first.output == second.output


def test_non_test_environment_rejects_placeholder_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret="replace-with-a-random-secret-of-at-least-32-bytes",
        )


def test_mcp_rejects_missing_or_wrong_local_token(monkeypatch):
    from app.mcp import server

    monkeypatch.setattr(server.settings, "mcp_user_id", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(server.settings, "mcp_local_token", "expected-token")
    try:
        server._verify_token("wrong-token")
    except ValueError as exc:
        assert "令牌无效" in str(exc)
    else:
        raise AssertionError("错误令牌必须被拒绝")

    assert not hasattr(server, "inspiration_resource")
    assert not hasattr(server, "recent_inspirations_resource")


async def test_mcp_create_tool_reuses_inspiration_service(monkeypatch, session_factory):
    from app.mcp import server

    async with session_factory() as session:
        user = User(
            email="mcp@example.com",
            password_hash="not-used-by-mcp",
            display_name="MCP 用户",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    monkeypatch.setattr(server, "SessionFactory", session_factory)
    monkeypatch.setattr(server.settings, "mcp_user_id", str(user.id))
    monkeypatch.setattr(server.settings, "mcp_local_token", "expected-token")
    result = await server.inspiration_create(
        local_token="expected-token", content="通过 MCP 创建的灵感", title="MCP"
    )
    assert result["content"] == "通过 MCP 创建的灵感"
    assert result["version"] == 1


async def test_mcp_tag_tools_include_usage_count(monkeypatch, session_factory):
    from app.mcp import server

    async with session_factory() as session:
        user = User(
            email="mcp-tags@example.com",
            password_hash="not-used-by-mcp",
            display_name="MCP 标签用户",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    monkeypatch.setattr(server, "SessionFactory", session_factory)
    monkeypatch.setattr(server.settings, "mcp_user_id", str(user.id))
    monkeypatch.setattr(server.settings, "mcp_local_token", "expected-token")

    tag = await server.tag_create("expected-token", "MCP 标签")
    assert tag["usage_count"] == 0
    await server.inspiration_create(
        "expected-token", "带标签的 MCP 灵感", tag_ids=[tag["id"]]
    )

    listed = await server.tag_list("expected-token")
    assert listed[0]["usage_count"] == 1

    updated = await server.tag_update(
        "expected-token", tag["id"], tag["version"], name="已更新标签"
    )
    assert updated["usage_count"] == 1
