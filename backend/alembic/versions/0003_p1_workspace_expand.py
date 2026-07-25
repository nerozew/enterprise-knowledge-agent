"""P1 expand：新增 Workspace 基础表并添加可空租户列。

Revision ID: 0003_p1_workspace_expand
Revises: 0002_tag_usage_index
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_p1_workspace_expand"
down_revision = "0002_tag_usage_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("personal_owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["personal_owner_user_id"],
            ["users.id"],
            name="fk_workspaces_personal_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.CheckConstraint(
            "type IN ('PERSONAL','TEAM')", name="ck_workspaces_workspace_type"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE')", name="ck_workspaces_workspace_status"
        ),
        sa.CheckConstraint(
            "(type = 'PERSONAL' AND personal_owner_user_id IS NOT NULL) OR "
            "(type = 'TEAM' AND personal_owner_user_id IS NULL)",
            name="ck_workspaces_workspace_personal_owner",
        ),
        sa.UniqueConstraint(
            "personal_owner_user_id", name="uq_workspaces_personal_owner_user_id"
        ),
    )
    op.create_index(
        "ix_workspaces_status_updated", "workspaces", ["status", "updated_at", "id"]
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_members_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspace_members_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_workspace_members_invited_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "user_id", name="pk_workspace_members"
        ),
        sa.CheckConstraint(
            "role IN ('OWNER','ADMIN','EDITOR','VIEWER')",
            name="ck_workspace_members_workspace_member_role",
        ),
        sa.CheckConstraint(
            "status IN ('INVITED','ACTIVE','SUSPENDED','REMOVED')",
            name="ck_workspace_members_workspace_member_status",
        ),
    )
    op.create_index(
        "ix_workspace_members_user_status",
        "workspace_members",
        ["user_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_workspace_members_workspace_status_role",
        "workspace_members",
        ["workspace_id", "status", "role"],
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.String(16), server_default="SUCCESS", nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_audit_logs_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_audit_logs_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_logs_workspace_created",
        "audit_logs",
        ["workspace_id", "created_at", "id"],
    )
    op.create_index(
        "ix_audit_logs_workspace_action",
        "audit_logs",
        ["workspace_id", "action", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_workspace_actor",
        "audit_logs",
        ["workspace_id", "actor_id", "created_at"],
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(80), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_idempotency_records_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_idempotency_records_actor_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "workspace_id",
            "actor_id",
            "scope",
            "key_hash",
            name="uq_idempotency_workspace_actor_scope_key",
        ),
    )
    op.create_index(
        "ix_idempotency_expires", "idempotency_records", ["expires_at"]
    )
    for table_name in (
        "inspirations",
        "tags",
        "inspiration_tags",
        "ai_generations",
    ):
        op.add_column(table_name, sa.Column("workspace_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    for table_name in (
        "ai_generations",
        "inspiration_tags",
        "tags",
        "inspirations",
    ):
        op.drop_column(table_name, "workspace_id")
    op.drop_table("idempotency_records")
    op.drop_table("audit_logs")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
