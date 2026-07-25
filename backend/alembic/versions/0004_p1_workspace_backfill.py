"""P1 backfill：为已有用户和业务数据回填个人 Workspace。

Revision ID: 0004_p1_workspace_backfill
Revises: 0003_p1_workspace_expand
Create Date: 2026-07-25
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0004_p1_workspace_backfill"
down_revision = "0003_p1_workspace_expand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(
        bind=bind,
        only=[
            "users",
            "workspaces",
            "workspace_members",
            "inspirations",
            "tags",
            "inspiration_tags",
            "ai_generations",
        ],
    )
    users = metadata.tables["users"]
    workspaces = metadata.tables["workspaces"]
    members = metadata.tables["workspace_members"]
    inspirations = metadata.tables["inspirations"]
    tags = metadata.tables["tags"]
    inspiration_tags = metadata.tables["inspiration_tags"]
    generations = metadata.tables["ai_generations"]
    now = datetime.now(UTC)
    workspace_by_user: dict[object, object] = {}
    for user_id, display_name in bind.execute(
        sa.select(users.c.id, users.c.display_name)
    ):
        existing = bind.execute(
            sa.select(workspaces.c.id).where(
                workspaces.c.personal_owner_user_id == user_id
            )
        ).scalar_one_or_none()
        workspace_id = existing or uuid4().hex
        if existing is None:
            bind.execute(
                workspaces.insert().values(
                    id=workspace_id,
                    name=f"{display_name}的个人空间",
                    type="PERSONAL",
                    status="ACTIVE",
                    personal_owner_user_id=user_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            bind.execute(
                members.insert().values(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role="OWNER",
                    status="ACTIVE",
                    version=1,
                    invited_by_user_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        workspace_by_user[user_id] = workspace_id

    for table in (inspirations, tags):
        for row_id, user_id in bind.execute(sa.select(table.c.id, table.c.user_id)):
            workspace_id = workspace_by_user.get(user_id)
            if workspace_id is None:
                raise RuntimeError(
                    f"{table.name} {row_id} 引用了不存在的用户，拒绝猜测归属"
                )
            bind.execute(
                table.update()
                .where(table.c.id == row_id)
                .values(workspace_id=workspace_id)
            )

    inspiration_owner = dict(
        bind.execute(
            sa.select(inspirations.c.id, inspirations.c.workspace_id)
        ).all()
    )
    tag_owner = dict(
        bind.execute(sa.select(tags.c.id, tags.c.workspace_id)).all()
    )
    for inspiration_id, tag_id in bind.execute(
        sa.select(inspiration_tags.c.inspiration_id, inspiration_tags.c.tag_id)
    ):
        inspiration_workspace = inspiration_owner.get(inspiration_id)
        tag_workspace = tag_owner.get(tag_id)
        if inspiration_workspace is None or inspiration_workspace != tag_workspace:
            raise RuntimeError(
                "inspiration_tags 存在跨 Workspace 或悬空关联，拒绝猜测修复"
            )
        bind.execute(
            inspiration_tags.update()
            .where(
                inspiration_tags.c.inspiration_id == inspiration_id,
                inspiration_tags.c.tag_id == tag_id,
            )
            .values(workspace_id=inspiration_workspace)
        )

    for generation_id, user_id, inspiration_id in bind.execute(
        sa.select(
            generations.c.id,
            generations.c.user_id,
            generations.c.inspiration_id,
        )
    ):
        user_workspace = workspace_by_user.get(user_id)
        inspiration_workspace = inspiration_owner.get(inspiration_id)
        if user_workspace is None or user_workspace != inspiration_workspace:
            raise RuntimeError(
                f"ai_generations {generation_id} 归属矛盾，拒绝猜测修复"
            )
        bind.execute(
            generations.update()
            .where(generations.c.id == generation_id)
            .values(workspace_id=user_workspace)
        )

    for table in (inspirations, tags, inspiration_tags, generations):
        missing = bind.execute(
            sa.select(sa.func.count())
            .select_from(table)
            .where(table.c.workspace_id.is_(None))
        ).scalar_one()
        if missing:
            raise RuntimeError(f"{table.name} 仍有 {missing} 条记录未回填")


def downgrade() -> None:
    # 数据回填不做逆向删除；expand downgrade 会移除新增列与表。
    pass
