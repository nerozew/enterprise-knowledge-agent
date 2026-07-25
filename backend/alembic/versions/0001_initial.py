"""创建 MVP 核心表。

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-17
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "inspirations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("expanded_content", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_inspirations_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inspirations"),
    )
    op.create_index("ix_inspirations_user_id", "inspirations", ["user_id"])
    op.create_index("ix_inspirations_user_updated", "inspirations", ["user_id", "updated_at"])
    op.create_index("ix_inspirations_user_deleted", "inspirations", ["user_id", "deleted_at"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("normalized_name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(9), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_tags_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("user_id", "normalized_name", name="uq_tags_user_normalized_name"),
    )
    op.create_index("ix_tags_user_id", "tags", ["user_id"])
    op.create_index("ix_tags_user_updated", "tags", ["user_id", "updated_at"])

    op.create_table(
        "inspiration_tags",
        sa.Column("inspiration_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["inspiration_id"],
            ["inspirations.id"],
            name="fk_inspiration_tags_inspiration_id_inspirations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name="fk_inspiration_tags_tag_id_tags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("inspiration_id", "tag_id", name="pk_inspiration_tags"),
    )

    op.create_table(
        "ai_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("inspiration_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ai_generations_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inspiration_id"],
            ["inspirations.id"],
            name="fk_ai_generations_inspiration_id_inspirations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_generations"),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_ai_generations_user_idempotency_key"
        ),
    )
    op.create_index("ix_ai_generations_user_id", "ai_generations", ["user_id"])
    op.create_index("ix_ai_generations_inspiration_id", "ai_generations", ["inspiration_id"])
    op.create_index(
        "ix_ai_generations_inspiration_created",
        "ai_generations",
        ["inspiration_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("ai_generations")
    op.drop_table("inspiration_tags")
    op.drop_table("tags")
    op.drop_table("inspirations")
    op.drop_table("users")
