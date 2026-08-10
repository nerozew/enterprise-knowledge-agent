"""P8: Workspace-scoped LangGraph checkpoints and pending writes.

Revision ID: 0009_p8_agent_checkpoints
Revises: 0008_p8_agent_domain
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0009_p8_agent_checkpoints"
down_revision = "0008_p8_agent_domain"
branch_labels = None
depends_on = None


CHECKPOINT_BLOB = sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")


def upgrade() -> None:
    op.create_table(
        "agent_checkpoints",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_ns", sa.String(160), nullable=False),
        sa.Column("checkpoint_id", sa.String(128), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(128), nullable=True),
        sa.Column("checkpoint_type", sa.String(64), nullable=False),
        sa.Column("checkpoint_blob", CHECKPOINT_BLOB, nullable=False),
        sa.Column("metadata_type", sa.String(64), nullable=False),
        sa.Column("metadata_blob", CHECKPOINT_BLOB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_checkpoints_workspace_conversation",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "thread_id", "checkpoint_ns", "checkpoint_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_agent_checkpoints_scope_created",
        "agent_checkpoints",
        ["workspace_id", "thread_id", "checkpoint_ns", "created_at"],
    )

    op.create_table(
        "agent_checkpoint_writes",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_ns", sa.String(160), nullable=False),
        sa.Column("checkpoint_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("write_idx", sa.Integer(), nullable=False),
        sa.Column("task_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(64), nullable=False),
        sa.Column("value_blob", CHECKPOINT_BLOB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_checkpoint_writes_workspace_conversation",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "write_idx",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_agent_checkpoint_writes_scope",
        "agent_checkpoint_writes",
        ["workspace_id", "thread_id", "checkpoint_ns", "checkpoint_id"],
    )
    op.create_index(
        "ix_agent_checkpoint_writes_orphan_cleanup",
        "agent_checkpoint_writes",
        ["workspace_id", "checkpoint_ns", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_checkpoint_writes_orphan_cleanup",
        table_name="agent_checkpoint_writes",
    )
    op.drop_index("ix_agent_checkpoint_writes_scope", table_name="agent_checkpoint_writes")
    op.drop_table("agent_checkpoint_writes")
    op.drop_index("ix_agent_checkpoints_scope_created", table_name="agent_checkpoints")
    op.drop_table("agent_checkpoints")
