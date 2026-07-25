"""P1 contract：收紧 Workspace 列并建立租户复合约束。

Revision ID: 0005_p1_workspace_contract
Revises: 0004_p1_workspace_backfill
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_p1_workspace_contract"
down_revision = "0004_p1_workspace_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inspirations") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_inspirations_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_inspirations_workspace_id", ["workspace_id", "id"]
        )
        batch.drop_index("ix_inspirations_user_updated")
        batch.drop_index("ix_inspirations_user_deleted")
        batch.create_index(
            "ix_inspirations_workspace_updated",
            ["workspace_id", "updated_at", "id"],
        )
        batch.create_index(
            "ix_inspirations_workspace_deleted", ["workspace_id", "deleted_at"]
        )

    with op.batch_alter_table("tags") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_tags_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint("uq_tags_workspace_id", ["workspace_id", "id"])
        batch.drop_constraint("uq_tags_user_normalized_name", type_="unique")
        batch.create_unique_constraint(
            "uq_tags_workspace_normalized_name",
            ["workspace_id", "normalized_name"],
        )
        batch.drop_index("ix_tags_user_updated")
        batch.create_index(
            "ix_tags_workspace_updated", ["workspace_id", "updated_at", "id"]
        )

    with op.batch_alter_table("inspiration_tags") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_constraint(
            "fk_inspiration_tags_inspiration_id_inspirations", type_="foreignkey"
        )
        batch.drop_constraint("fk_inspiration_tags_tag_id_tags", type_="foreignkey")
        batch.drop_constraint("pk_inspiration_tags", type_="primary")
        batch.create_primary_key(
            "pk_inspiration_tags", ["inspiration_id", "tag_id", "workspace_id"]
        )
        batch.create_foreign_key(
            "fk_inspiration_tags_workspace_inspiration",
            "inspirations",
            ["workspace_id", "inspiration_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_inspiration_tags_workspace_tag",
            "tags",
            ["workspace_id", "tag_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_index(
            "ix_inspiration_tags_workspace_tag", ["workspace_id", "tag_id"]
        )

    with op.batch_alter_table("ai_generations") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_ai_generations_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint(
            "fk_ai_generations_inspiration_id_inspirations", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_ai_generations_workspace_inspiration",
            "inspirations",
            ["workspace_id", "inspiration_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint(
            "uq_ai_generations_user_idempotency_key", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_ai_generations_workspace_user_idempotency",
            ["workspace_id", "user_id", "idempotency_key"],
        )
        batch.drop_index("ix_ai_generations_inspiration_created")
        batch.create_index(
            "ix_ai_generations_workspace_inspiration_created",
            ["workspace_id", "inspiration_id", "created_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_generations") as batch:
        batch.drop_index("ix_ai_generations_workspace_inspiration_created")
        batch.create_index(
            "ix_ai_generations_inspiration_created",
            ["inspiration_id", "created_at"],
        )
        batch.drop_constraint(
            "uq_ai_generations_workspace_user_idempotency", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_ai_generations_user_idempotency_key",
            ["user_id", "idempotency_key"],
        )
        batch.drop_constraint(
            "fk_ai_generations_workspace_inspiration", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_ai_generations_inspiration_id_inspirations",
            "inspirations",
            ["inspiration_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint(
            "fk_ai_generations_workspace_id_workspaces", type_="foreignkey"
        )
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=True)

    with op.batch_alter_table("inspiration_tags") as batch:
        batch.drop_index("ix_inspiration_tags_workspace_tag")
        batch.drop_constraint(
            "fk_inspiration_tags_workspace_tag", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_inspiration_tags_workspace_inspiration", type_="foreignkey"
        )
        batch.drop_constraint("pk_inspiration_tags", type_="primary")
        batch.create_primary_key(
            "pk_inspiration_tags", ["inspiration_id", "tag_id"]
        )
        batch.create_foreign_key(
            "fk_inspiration_tags_inspiration_id_inspirations",
            "inspirations",
            ["inspiration_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_inspiration_tags_tag_id_tags",
            "tags",
            ["tag_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=True)

    with op.batch_alter_table("tags") as batch:
        batch.drop_index("ix_tags_workspace_updated")
        batch.create_index("ix_tags_user_updated", ["user_id", "updated_at"])
        batch.drop_constraint("uq_tags_workspace_normalized_name", type_="unique")
        batch.create_unique_constraint(
            "uq_tags_user_normalized_name", ["user_id", "normalized_name"]
        )
        batch.drop_constraint("uq_tags_workspace_id", type_="unique")
        batch.drop_constraint("fk_tags_workspace_id_workspaces", type_="foreignkey")
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=True)

    with op.batch_alter_table("inspirations") as batch:
        batch.drop_index("ix_inspirations_workspace_deleted")
        batch.drop_index("ix_inspirations_workspace_updated")
        batch.create_index(
            "ix_inspirations_user_updated", ["user_id", "updated_at"]
        )
        batch.create_index(
            "ix_inspirations_user_deleted", ["user_id", "deleted_at"]
        )
        batch.drop_constraint("uq_inspirations_workspace_id", type_="unique")
        batch.drop_constraint(
            "fk_inspirations_workspace_id_workspaces", type_="foreignkey"
        )
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=True)
