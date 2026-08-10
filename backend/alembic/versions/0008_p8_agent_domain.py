"""P8：Agent 领域事实、审批与待办。

Revision ID: 0008_p8_agent_domain
Revises: 0007_p2_p4_integrity_and_leases
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_p8_agent_domain"
down_revision = "0007_p2_p4_integrity_and_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch:
        batch.create_unique_constraint(
            "uq_chunks_workspace_version_id_span",
            ["workspace_id", "document_version_id", "id", "source_span_id"],
        )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(240), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="conversation_status"),
        sa.CheckConstraint("version >= 1", name="conversation_version"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conversations_workspace_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_conversations_workspace_updated",
        "conversations",
        ["workspace_id", "updated_at", "id"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="CREATED"),
        sa.Column("route", sa.String(32), nullable=True),
        sa.Column("reason_code", sa.String(40), nullable=True),
        sa.Column("graph_version", sa.String(80), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED','RUNNING','WAITING_APPROVAL','COMPLETED','FAILED','CANCELLED')",
            name="agent_run_status",
        ),
        sa.CheckConstraint(
            "route IS NULL OR route IN "
            "('DIRECT_ANSWER','KNOWLEDGE_QA','INSPIRATION_SEARCH','TODO_CREATE')",
            name="agent_run_route",
        ),
        sa.CheckConstraint("version >= 1", name="agent_run_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_runs_workspace_conversation",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_runs_workspace_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_agent_runs_workspace_status_updated",
        "agent_runs",
        ["workspace_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_agent_runs_workspace_conversation",
        "agent_runs",
        ["workspace_id", "conversation_id"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('SYSTEM','USER','ASSISTANT','TOOL')", name="message_role"),
        sa.CheckConstraint("sequence_no >= 1", name="message_sequence"),
        sa.CheckConstraint("version >= 1", name="message_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_messages_workspace_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_messages_workspace_run",
        ),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_messages_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "sequence_no",
            name="uq_messages_workspace_conversation_seq",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_messages_workspace_conversation",
        "messages",
        ["workspace_id", "conversation_id", "sequence_no"],
    )

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence_no >= 1", name="agent_run_event_sequence"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_run_events_workspace_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_run_events_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "run_id", "sequence_no", name="uq_agent_run_events_run_seq"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_agent_run_events_workspace_run",
        "agent_run_events",
        ["workspace_id", "run_id", "sequence_no"],
    )

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','EXPIRED')",
            name="approval_request_status",
        ),
        sa.CheckConstraint("version >= 1", name="approval_request_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_approval_requests_workspace_run",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_approval_requests_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "run_id", "tool_call_id", name="uq_approval_workspace_run_tool_call"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "run_id",
            "tool_call_id",
            name="uq_approval_workspace_id_run_tool",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_approval_workspace_status_expiry",
        "approval_requests",
        ["workspace_id", "status", "expires_at"],
    )

    op.create_table(
        "todos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('OPEN','COMPLETED','CANCELLED')", name="todo_status"),
        sa.CheckConstraint("version >= 1", name="todo_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_todos_workspace_source_run",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "approval_id",
                "source_run_id",
                "source_tool_call_id",
            ],
            [
                "approval_requests.workspace_id",
                "approval_requests.id",
                "approval_requests.run_id",
                "approval_requests.tool_call_id",
            ],
            ondelete="RESTRICT",
            name="fk_todos_workspace_approval_source",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_todos_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "source_run_id", "source_tool_call_id", name="uq_todos_source_call"
        ),
        sa.UniqueConstraint("workspace_id", "approval_id", name="uq_todos_workspace_approval"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_todos_workspace_status_due", "todos", ["workspace_id", "status", "due_at"])
    op.create_index(
        "ix_todos_workspace_assignee",
        "todos",
        ["workspace_id", "assignee_user_id", "status"],
    )

    op.create_table(
        "agent_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("source_span_id", sa.String(64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal >= 1", name="agent_citation_ordinal"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_citations_workspace_run",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["messages.workspace_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_agent_citations_workspace_message",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id", "chunk_id", "source_span_id"],
            [
                "document_chunks.workspace_id",
                "document_chunks.document_version_id",
                "document_chunks.id",
                "document_chunks.source_span_id",
            ],
            ondelete="RESTRICT",
            name="fk_agent_citations_workspace_chunk_span",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_citations_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "run_id", "ordinal", name="uq_agent_citations_run_ordinal"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_agent_citations_workspace_run",
        "agent_citations",
        ["workspace_id", "run_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_citations_workspace_run", table_name="agent_citations")
    op.drop_table("agent_citations")
    op.drop_index("ix_todos_workspace_assignee", table_name="todos")
    op.drop_index("ix_todos_workspace_status_due", table_name="todos")
    op.drop_table("todos")
    op.drop_index("ix_approval_workspace_status_expiry", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_agent_run_events_workspace_run", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_messages_workspace_conversation", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_agent_runs_workspace_conversation", table_name="agent_runs")
    op.drop_index("ix_agent_runs_workspace_status_updated", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_conversations_workspace_updated", table_name="conversations")
    op.drop_table("conversations")
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_constraint("uq_chunks_workspace_version_id_span", type_="unique")
