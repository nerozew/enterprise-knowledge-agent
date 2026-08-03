"""P8: durable Agent run dispatch queue.

Revision ID: 0011_p8_agent_dispatch
Revises: 0010_p8_checkpoint_binding
Create Date: 2026-08-03
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_p8_agent_dispatch"
down_revision = "0010_p8_checkpoint_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_dispatches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','SUCCEEDED','FAILED')",
            name="agent_dispatch_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="agent_dispatch_attempts"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_dispatch_workspace_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "run_id", name="uq_agent_dispatch_workspace_run"
        ),
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_agent_dispatch_due",
        "agent_run_dispatches",
        ["status", "available_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_dispatch_due", table_name="agent_run_dispatches")
    op.drop_table("agent_run_dispatches")
