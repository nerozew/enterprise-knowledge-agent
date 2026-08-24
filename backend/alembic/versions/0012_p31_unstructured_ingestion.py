"""P3.1: layout-aware unstructured ingestion and resolvable source spans.

Revision ID: 0012_p31_unstructured_ingestion
Revises: 0011_p8_agent_dispatch
Create Date: 2026-08-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_p31_unstructured_ingestion"
down_revision = "0011_p8_agent_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document_versions") as batch:
        batch.add_column(
            sa.Column("structure_schema_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("layout_profile", sa.String(128), nullable=True))
        batch.add_column(sa.Column("ocr_profile", sa.String(128), nullable=True))
        batch.add_column(sa.Column("derived_manifest_key", sa.String(768), nullable=True))
        batch.add_column(sa.Column("derived_manifest_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("page_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("slide_count", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("ocr_page_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "parse_warnings_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("(JSON_ARRAY())"),
            )
        )

    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.add_column(sa.Column("stage_detail", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column(
                "pages_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("(JSON_OBJECT())"),
            )
        )
        batch.add_column(
            sa.Column(
                "warnings_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("(JSON_ARRAY())"),
            )
        )

    op.create_table(
        "document_version_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("locator_key", sa.String(80), nullable=False, server_default="document"),
        sa.Column("object_key", sa.String(768), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rebuildable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="READY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            ondelete="CASCADE",
            name="fk_version_artifacts_workspace_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "document_version_id",
            "artifact_type",
            "locator_key",
            name="uq_version_artifacts_locator",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_version_artifacts_workspace_version",
        "document_version_artifacts",
        ["workspace_id", "document_version_id"],
    )

    op.create_table(
        "chunk_source_spans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("primary_source_span_id", sa.String(64), nullable=False),
        sa.Column("source_span_id", sa.String(64), nullable=False),
        sa.Column("span_ordinal", sa.Integer(), nullable=False),
        sa.Column("block_ref", sa.String(64), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("slide_no", sa.Integer(), nullable=True),
        sa.Column("bbox_json", sa.JSON(), nullable=True),
        sa.Column("block_char_start", sa.Integer(), nullable=False),
        sa.Column("block_char_end", sa.Integer(), nullable=False),
        sa.Column("excerpt_text", sa.Text(), nullable=False),
        sa.Column("excerpt_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id"],
            [
                "document_versions.workspace_id",
                "document_versions.document_id",
                "document_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_chunk_spans_workspace_document_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id", "chunk_id", "primary_source_span_id"],
            [
                "document_chunks.workspace_id",
                "document_chunks.document_version_id",
                "document_chunks.id",
                "document_chunks.source_span_id",
            ],
            ondelete="CASCADE",
            name="fk_chunk_spans_workspace_chunk",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "document_version_id",
            "chunk_id",
            "source_span_id",
            name="uq_chunk_spans_workspace_version_chunk_span",
        ),
        sa.UniqueConstraint(
            "workspace_id", "chunk_id", "span_ordinal", name="uq_chunk_spans_chunk_order"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_chunk_spans_workspace_version_chunk",
        "chunk_source_spans",
        ["workspace_id", "document_version_id", "chunk_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_spans_workspace_version_chunk", table_name="chunk_source_spans")
    op.drop_table("chunk_source_spans")
    op.drop_index(
        "ix_version_artifacts_workspace_version", table_name="document_version_artifacts"
    )
    op.drop_table("document_version_artifacts")
    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.drop_column("warnings_json")
        batch.drop_column("pages_json")
        batch.drop_column("stage_detail")
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_column("parse_warnings_json")
        batch.drop_column("ocr_page_count")
        batch.drop_column("slide_count")
        batch.drop_column("page_count")
        batch.drop_column("derived_manifest_sha256")
        batch.drop_column("derived_manifest_key")
        batch.drop_column("ocr_profile")
        batch.drop_column("layout_profile")
        batch.drop_column("structure_schema_version")
