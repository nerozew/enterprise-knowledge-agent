"""为灵感标签关联的标签列添加索引。

Revision ID: 0002_tag_usage_index
Revises: 0001_initial
Create Date: 2026-07-17
"""

from alembic import op

revision = "0002_tag_usage_index"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.execute(
            "ALTER TABLE inspiration_tags "
            "RENAME INDEX fk_inspiration_tags_tag_id_tags "
            "TO ix_inspiration_tags_tag_id"
        )
    else:
        op.create_index(
            "ix_inspiration_tags_tag_id",
            "inspiration_tags",
            ["tag_id"],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.execute(
            "ALTER TABLE inspiration_tags "
            "RENAME INDEX ix_inspiration_tags_tag_id "
            "TO fk_inspiration_tags_tag_id_tags"
        )
    else:
        op.drop_index("ix_inspiration_tags_tag_id", table_name="inspiration_tags")
