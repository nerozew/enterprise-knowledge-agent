"""P8: Bind Todo runs and approvals to an exact interrupt checkpoint.

Revision ID: 0010_p8_checkpoint_binding
Revises: 0009_p8_agent_checkpoints
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0010_p8_checkpoint_binding"
down_revision = "0009_p8_agent_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("checkpoint_ns", sa.String(160), nullable=True))
        batch.add_column(sa.Column("checkpoint_id", sa.String(128), nullable=True))
        batch.create_unique_constraint(
            "uq_agent_runs_workspace_checkpoint",
            ["workspace_id", "id", "checkpoint_ns", "checkpoint_id"],
        )
        batch.create_check_constraint(
            "agent_run_checkpoint_complete",
            "(checkpoint_ns IS NULL AND checkpoint_id IS NULL) OR "
            "(checkpoint_ns IS NOT NULL AND checkpoint_id IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_agent_runs_workspace_checkpoint",
            "agent_checkpoints",
            ["workspace_id", "conversation_id", "checkpoint_ns", "checkpoint_id"],
            ["workspace_id", "thread_id", "checkpoint_ns", "checkpoint_id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("checkpoint_ns", sa.String(160), nullable=True))
        batch.add_column(sa.Column("checkpoint_id", sa.String(128), nullable=True))
        batch.create_check_constraint(
            "approval_checkpoint_complete",
            "(checkpoint_ns IS NULL AND checkpoint_id IS NULL) OR "
            "(checkpoint_ns IS NOT NULL AND checkpoint_id IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_approval_requests_run_checkpoint",
            "agent_runs",
            ["workspace_id", "run_id", "checkpoint_ns", "checkpoint_id"],
            ["workspace_id", "id", "checkpoint_ns", "checkpoint_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("fk_approval_requests_run_checkpoint", type_="foreignkey")
        batch.drop_constraint("approval_checkpoint_complete", type_="check")
        batch.drop_column("checkpoint_id")
        batch.drop_column("checkpoint_ns")

    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("fk_agent_runs_workspace_checkpoint", type_="foreignkey")
        batch.drop_constraint("agent_run_checkpoint_complete", type_="check")
        batch.drop_constraint("uq_agent_runs_workspace_checkpoint", type_="unique")
        batch.drop_column("checkpoint_id")
        batch.drop_column("checkpoint_ns")
