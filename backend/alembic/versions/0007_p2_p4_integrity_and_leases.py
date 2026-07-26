"""P2-P4：发布指针、Chunk 归属完整性与 Worker 租约。

Revision ID: 0007_p2_p4_integrity_and_leases
Revises: 0006_p2_p4_knowledge_rag
Create Date: 2026-07-26
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_p2_p4_integrity_and_leases"
down_revision = "0006_p2_p4_knowledge_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document_versions") as batch:
        batch.create_unique_constraint(
            "uq_document_versions_workspace_document_id",
            ["workspace_id", "document_id", "id"],
        )
    with op.batch_alter_table("documents") as batch:
        batch.create_foreign_key(
            "fk_documents_published_version",
            "document_versions",
            ["workspace_id", "id", "published_version_id"],
            ["workspace_id", "document_id", "id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_constraint(
            "fk_document_chunks_workspace_version", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_document_chunks_workspace_document_version",
            "document_versions",
            ["workspace_id", "document_id", "document_version_id"],
            ["workspace_id", "document_id", "id"],
            ondelete="CASCADE",
        )
    with op.batch_alter_table("outbox_events") as batch:
        batch.add_column(sa.Column("lease_owner", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("last_error_code", sa.String(80), nullable=True))
    with op.batch_alter_table("compensation_tasks") as batch:
        batch.add_column(sa.Column("lease_owner", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("compensation_tasks") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_column("last_error_code")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_constraint(
            "fk_document_chunks_workspace_document_version", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_document_chunks_workspace_version",
            "document_versions",
            ["workspace_id", "document_version_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("fk_documents_published_version", type_="foreignkey")
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_constraint(
            "uq_document_versions_workspace_document_id", type_="unique"
        )
