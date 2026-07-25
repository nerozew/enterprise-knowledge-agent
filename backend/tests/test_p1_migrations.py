from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.entities import Inspiration, User, Workspace, WorkspaceMember
from app.schemas.inspirations import InspirationPatch
from app.schemas.workspaces import MemberCreate
from app.services.inspirations import InspirationService
from app.services.workspaces import MemberService

BACKEND_ROOT = Path(__file__).parents[1]


def _database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output


def test_p1_sqlite_empty_database_upgrade_and_downgrade(tmp_path):
    database = tmp_path / "p1-empty.db"
    url = _database_url(database)
    _run_alembic(url, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0005_p1_workspace_contract",)
    _run_alembic(url, "downgrade", "base")
    with sqlite3.connect(database) as connection:
        domain_tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version'"
        ).fetchall()
        assert domain_tables == []


def test_p1_sqlite_chinese_mvp_backfill_and_composite_constraints(tmp_path):
    database = tmp_path / "p1-mvp.db"
    url = _database_url(database)
    _run_alembic(url, "upgrade", "0002_tag_usage_index")
    now = datetime.now(UTC).isoformat(" ")
    user_id = uuid4().hex
    inspiration_id = uuid4().hex
    tag_id = uuid4().hex
    generation_id = uuid4().hex
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users"
            "(id,email,password_hash,display_name,is_active,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                user_id,
                "migration@example.com",
                "hash",
                "中文用户",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO inspirations"
            "(id,user_id,title,content,version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                inspiration_id,
                user_id,
                "中文标题",
                "中文正文",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO tags"
            "(id,user_id,name,normalized_name,version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                tag_id,
                user_id,
                "中文标签",
                "中文标签",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO inspiration_tags(inspiration_id,tag_id,created_at) "
            "VALUES(?,?,?)",
            (inspiration_id, tag_id, now),
        )
        connection.execute(
            "INSERT INTO ai_generations"
            "(id,user_id,inspiration_id,operation,idempotency_key,request_hash,"
            "mode,provider,output,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                generation_id,
                user_id,
                inspiration_id,
                "summary",
                "legacy-key",
                "0" * 64,
                "fallback",
                "local",
                "中文输出",
                now,
            ),
        )
        connection.commit()

    _run_alembic(url, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workspaces WHERE type='PERSONAL'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM workspace_members "
            "WHERE role='OWNER' AND status='ACTIVE'"
        ).fetchone() == (1,)
        for table_name in (
            "inspirations",
            "tags",
            "inspiration_tags",
            "ai_generations",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE workspace_id IS NULL"
            ).fetchone() == (0,)
        assert connection.execute(
            "SELECT title, content FROM inspirations"
        ).fetchone() == ("中文标题", "中文正文")
        workspace_id = connection.execute(
            "SELECT workspace_id FROM tags WHERE id=?", (tag_id,)
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tags"
                "(id,workspace_id,user_id,name,normalized_name,version,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    workspace_id,
                    user_id,
                    "重复标签",
                    "中文标签",
                    1,
                    now,
                    now,
                ),
            )
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(inspiration_tags)"
        ).fetchall()
        mapped_columns = {(row[3], row[4]) for row in foreign_keys}
        assert {
            ("workspace_id", "workspace_id"),
            ("inspiration_id", "id"),
            ("tag_id", "id"),
        }.issubset(mapped_columns)


@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL P1 集成迁移",
)
async def test_p1_mysql_upgrade_charset_backfill_and_conflict():
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    assert "charset=utf8mb4" in database_url
    _run_alembic(database_url, "upgrade", "head")
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        charset = (
            await connection.execute(text("SELECT @@character_set_database"))
        ).scalar_one()
        assert charset == "utf8mb4"
        for table_name in (
            "inspirations",
            "tags",
            "inspiration_tags",
            "ai_generations",
        ):
            missing = (
                await connection.execute(
                    text(
                        f"SELECT COUNT(*) FROM {table_name} "
                        "WHERE workspace_id IS NULL"
                    )
                )
            ).scalar_one()
            assert missing == 0
        composite_unique = (
            await connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.statistics "
                    "WHERE table_schema=DATABASE() AND table_name='tags' "
                    "AND index_name='uq_tags_workspace_normalized_name'"
                )
            )
        ).scalar_one()
        assert composite_unique == 2

        await connection.rollback()
        transaction = await connection.begin()
        try:
            user_id = uuid4().hex
            workspace_id = uuid4().hex
            now = datetime.now(UTC).replace(tzinfo=None)
            await connection.execute(
                text(
                    "INSERT INTO users"
                    "(id,email,password_hash,display_name,is_active,created_at,updated_at) "
                    "VALUES(:id,:email,'hash','迁移测试',1,:now,:now)"
                ),
                {
                    "id": user_id,
                    "email": f"{user_id}@migration.invalid",
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO workspaces"
                    "(id,name,type,status,version,created_at,updated_at) "
                    "VALUES(:id,'迁移约束测试','TEAM','ACTIVE',1,:now,:now)"
                ),
                {"id": workspace_id, "now": now},
            )
            await connection.execute(
                text(
                    "INSERT INTO tags"
                    "(id,workspace_id,user_id,name,normalized_name,version,"
                    "created_at,updated_at) "
                    "VALUES(:id,:wid,:uid,'标签一','same',1,:now,:now)"
                ),
                {
                    "id": uuid4().hex,
                    "wid": workspace_id,
                    "uid": user_id,
                    "now": now,
                },
            )
            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text(
                            "INSERT INTO tags"
                            "(id,workspace_id,user_id,name,normalized_name,version,"
                            "created_at,updated_at) "
                            "VALUES(:id,:wid,:uid,'标签二','same',1,:now,:now)"
                        ),
                        {
                            "id": uuid4().hex,
                            "wid": workspace_id,
                            "uid": user_id,
                            "now": now,
                        },
                    )
            finally:
                await savepoint.rollback()
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL 成员并发幂等测试",
)
async def test_p1_mysql_concurrent_member_same_key_replays_winner():
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_id = uuid4()
    candidate_id = uuid4()
    workspace_id = uuid4()
    owner_email = f"owner-{owner_id}@example.com"
    candidate_email = f"candidate-{candidate_id}@example.com"
    now = datetime.now(UTC)
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=owner_id,
                    email=owner_email,
                    password_hash="unused",
                    display_name="并发 Owner",
                ),
                User(
                    id=candidate_id,
                    email=candidate_email,
                    password_hash="unused",
                    display_name="并发候选人",
                ),
                Workspace(
                    id=workspace_id,
                    name="成员并发测试",
                    type="TEAM",
                    status="ACTIVE",
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=owner_id,
                    role="OWNER",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=owner_id,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()

    async def add_member():
        async with factory() as session:
            return await MemberService(session, owner_id).add(
                workspace_id,
                MemberCreate(email=candidate_email, role="VIEWER"),
                "mysql-member-race-key",
                str(uuid4()),
            )

    try:
        first, second = await asyncio.gather(add_member(), add_member())
        assert first[0].user_id == second[0].user_id == candidate_id
        assert first[2] == second[2] or first[2] is None or second[2] is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM audit_logs WHERE workspace_id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM idempotency_records WHERE workspace_id=:wid"
                ),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM workspace_members WHERE workspace_id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM workspaces WHERE id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM users WHERE id IN (:owner,:candidate)"),
                {"owner": owner_id, "candidate": candidate_id},
            )
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("P1_TEST_MYSQL_URL"),
    reason="设置 P1_TEST_MYSQL_URL 后执行真实 MySQL 灵感并发幂等测试",
)
async def test_p1_mysql_concurrent_inspiration_patch_replays_winner():
    database_url = os.environ["P1_TEST_MYSQL_URL"]
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_id = uuid4()
    workspace_id = uuid4()
    inspiration_id = uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=owner_id,
                    email=f"owner-{owner_id}@example.com",
                    password_hash="unused",
                    display_name="灵感并发 Owner",
                ),
                Workspace(
                    id=workspace_id,
                    name="灵感并发测试",
                    type="TEAM",
                    status="ACTIVE",
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=owner_id,
                    role="OWNER",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=owner_id,
                    created_at=now,
                    updated_at=now,
                ),
                Inspiration(
                    id=inspiration_id,
                    workspace_id=workspace_id,
                    user_id=owner_id,
                    title="并发前",
                    content="并发正文",
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()

    async def update_inspiration():
        async with factory() as session:
            return await InspirationService(
                session, owner_id, workspace_id
            ).update(
                inspiration_id,
                InspirationPatch(version=1, title="并发后"),
                "mysql-inspiration-patch-race-key",
            )

    try:
        first, second = await asyncio.gather(
            update_inspiration(), update_inspiration()
        )
        assert first.response_json == second.response_json
        assert first.response_json is not None
        assert first.response_json["id"] == str(inspiration_id)
        assert first.response_json["title"] == "并发后"
        assert first.response_json["version"] == 2
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM audit_logs WHERE workspace_id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM idempotency_records WHERE workspace_id=:wid"
                ),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM inspirations WHERE workspace_id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM workspace_members WHERE workspace_id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM workspaces WHERE id=:wid"),
                {"wid": workspace_id},
            )
            await connection.execute(
                text("DELETE FROM users WHERE id=:owner"),
                {"owner": owner_id},
            )
        await engine.dispose()
