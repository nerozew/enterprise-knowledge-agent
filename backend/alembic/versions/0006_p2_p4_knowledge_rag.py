"""P2-P4：知识文档、摄取状态、Chunk、Outbox 与补偿。

Revision ID: 0006_p2_p4_knowledge_rag
Revises: 0005_p1_workspace_contract
Create Date: 2026-07-26
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_p2_p4_knowledge_rag"
down_revision = "0005_p1_workspace_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("idempotency_records") as batch:
        batch.add_column(
            sa.Column(
                "request_hash_algorithm",
                sa.String(16),
                nullable=False,
                server_default="SHA-256",
            )
        )
        batch.add_column(
            sa.Column("status", sa.String(16), nullable=False, server_default="SUCCEEDED")
        )
        batch.add_column(sa.Column("operation_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("response_ref", sa.String(512), nullable=True))

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("published_version_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_documents_workspace_id"),
    )
    op.create_index(
        "ix_documents_workspace_status_updated",
        "documents",
        ["workspace_id", "status", "updated_at"],
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(768), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "parser_profile", sa.String(64), nullable=False, server_default="structure-v1"
        ),
        sa.Column(
            "chunk_profile",
            sa.String(64),
            nullable=False,
            server_default="parent-child-v1",
        ),
        sa.Column(
            "index_profile",
            sa.String(128),
            nullable=False,
            server_default="fixture-hash-v1",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_document_versions_workspace_document",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_document_versions_workspace_id"
        ),
    )
    op.create_index(
        "ix_document_versions_workspace_document_created",
        "document_versions",
        ["workspace_id", "document_id", "created_at"],
    )
    op.create_index(
        "ix_document_versions_workspace_hash",
        "document_versions",
        ["workspace_id", "file_sha256", "status"],
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("previous_job_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="QUEUED"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="QUEUED"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_job_id"], ["ingestion_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_ingestion_jobs_workspace_document",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            ondelete="CASCADE",
            name="fk_ingestion_jobs_workspace_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ingestion_jobs_workspace_id"),
    )
    op.create_index(
        "ix_ingestion_jobs_workspace_status_updated",
        "ingestion_jobs",
        ["workspace_id", "status", "updated_at"],
    )

    op.create_table(
        "ingestion_job_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["ingestion_jobs.workspace_id", "ingestion_jobs.id"],
            ondelete="CASCADE",
            name="fk_ingestion_attempts_workspace_job",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_ingestion_attempt_job_no"),
    )
    op.create_index(
        "ix_ingestion_attempts_workspace_job",
        "ingestion_job_attempts",
        ["workspace_id", "job_id"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.JSON(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("source_span_id", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("indexed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_chunk_id"], ["document_chunks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            ondelete="CASCADE",
            name="fk_document_chunks_workspace_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "source_span_id",
            "kind",
            name="uq_chunks_version_span_kind",
        ),
    )
    op.create_index(
        "ix_chunks_workspace_version_kind_order",
        "document_chunks",
        ["workspace_id", "document_version_id", "kind", "ordinal"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_hash", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(768), nullable=True),
        sa.Column("config_version", sa.String(80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "dedupe_hash", name="uq_outbox_workspace_dedupe"
        ),
    )
    op.create_index(
        "ix_outbox_status_available", "outbox_events", ["status", "available_at"]
    )

    op.create_table(
        "compensation_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(48), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(768), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compensation_workspace_status",
        "compensation_tasks",
        ["workspace_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_compensation_workspace_status", table_name="compensation_tasks")
    op.drop_table("compensation_tasks")
    op.drop_index("ix_outbox_status_available", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index(
        "ix_chunks_workspace_version_kind_order", table_name="document_chunks"
    )
    op.drop_table("document_chunks")
    op.drop_index(
        "ix_ingestion_attempts_workspace_job", table_name="ingestion_job_attempts"
    )
    op.drop_table("ingestion_job_attempts")
    op.drop_index(
        "ix_ingestion_jobs_workspace_status_updated", table_name="ingestion_jobs"
    )
    op.drop_table("ingestion_jobs")
    op.drop_index(
        "ix_document_versions_workspace_hash", table_name="document_versions"
    )
    op.drop_index(
        "ix_document_versions_workspace_document_created",
        table_name="document_versions",
    )
    op.drop_table("document_versions")
    op.drop_index("ix_documents_workspace_status_updated", table_name="documents")
    op.drop_table("documents")
    with op.batch_alter_table("idempotency_records") as batch:
        batch.drop_column("response_ref")
        batch.drop_column("operation_id")
        batch.drop_column("status")
        batch.drop_column("request_hash_algorithm")
