"""Add grounded-agent sessions, confirmations, media metadata and operations events.

Revision ID: 0017_agent_capabilities
Revises: 0016_chat_pagination
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_agent_capabilities"
down_revision: str | None = "0016_chat_pagination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_tenant_rls(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        "USING (current_setting('app.is_privileged', true) = 'true' "
        "OR tenant_id::text = current_setting('app.current_tenant_id', true)) "
        "WITH CHECK (current_setting('app.is_privileged', true) = 'true' "
        "OR tenant_id::text = current_setting('app.current_tenant_id', true))"
    )


def upgrade() -> None:
    # The consultant pilot materially broadens product capabilities. Stop new
    # research capture until every participant explicitly re-consents; product
    # chat and operational telemetry remain available.
    op.execute(
        "UPDATE tenants SET research_consent_status = 'pending' "
        "WHERE research_consent_status = 'granted'"
    )

    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "allowed_resources",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    with op.batch_alter_table("hermes_profiles") as batch_op:
        batch_op.add_column(sa.Column("mcp_token_hash", sa.String(length=64)))
        batch_op.create_unique_constraint(
            "uq_hermes_profiles_mcp_token_hash",
            ["mcp_token_hash"],
        )

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("provider_session_id", sa.String(length=160)))
        batch_op.add_column(sa.Column("provider_session_key", sa.String(length=160)))

    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.add_column(
            sa.Column("content_parts", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(sa.Column("reply_to_external_message_id", sa.String(length=160)))
        batch_op.add_column(
            sa.Column("media_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )

    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.add_column(sa.Column("reply_to_meta_message_id", sa.String(length=160)))
        batch_op.add_column(
            sa.Column("media_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(
            sa.Column("status_rank", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            "ck_whatsapp_messages_status_rank_nonnegative",
            "status_rank >= 0",
        )

    op.create_table(
        "pending_agent_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36)),
        sa.Column("mcp_connection_id", sa.String(length=36)),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("target_summary", sa.Text(), nullable=False),
        sa.Column("action_input", sa.JSON(), nullable=False),
        sa.Column("action_result", sa.JSON()),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("confirmation_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.String(length=80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'denied', 'expired', 'executing', "
            "'succeeded', 'failed')",
            name="ck_pending_agent_actions_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mcp_connection_id"], ["mcp_connections.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_pending_agent_actions_tenant_idempotency",
        ),
        sa.UniqueConstraint("confirmation_token_hash"),
    )
    op.create_index(
        "ix_pending_agent_actions_tenant_user_status",
        "pending_agent_actions",
        ["tenant_id", "user_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_pending_agent_actions_expires_at",
        "pending_agent_actions",
        ["expires_at"],
    )
    _enable_tenant_rls("pending_agent_actions")

    op.create_table(
        "operational_agent_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36)),
        sa.Column("user_id", sa.String(length=36)),
        sa.Column("conversation_id", sa.String(length=36)),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=40)),
        sa.Column("tool_name", sa.String(length=160)),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grounding_flags", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_operational_agent_events_duration_nonnegative",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operational_agent_events_tenant_created",
        "operational_agent_events",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_operational_agent_events_type_created",
        "operational_agent_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_operational_agent_events_event_type",
        "operational_agent_events",
        ["event_type"],
    )
    _enable_tenant_rls("operational_agent_events")

    op.create_table(
        "generated_agent_media",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("media_type", sa.String(length=24), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="ck_generated_agent_media_status",
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 8388608",
            name="ck_generated_agent_media_byte_size",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generated_agent_media_delivery",
        "generated_agent_media",
        ["tenant_id", "user_id", "conversation_id", "status"],
    )
    _enable_tenant_rls("generated_agent_media")


def downgrade() -> None:
    op.drop_index(
        "ix_generated_agent_media_delivery",
        table_name="generated_agent_media",
    )
    op.drop_table("generated_agent_media")
    op.drop_index(
        "ix_operational_agent_events_event_type",
        table_name="operational_agent_events",
    )
    op.drop_index(
        "ix_operational_agent_events_type_created",
        table_name="operational_agent_events",
    )
    op.drop_index(
        "ix_operational_agent_events_tenant_created",
        table_name="operational_agent_events",
    )
    op.drop_table("operational_agent_events")
    op.drop_index(
        "ix_pending_agent_actions_expires_at",
        table_name="pending_agent_actions",
    )
    op.drop_index(
        "ix_pending_agent_actions_tenant_user_status",
        table_name="pending_agent_actions",
    )
    op.drop_table("pending_agent_actions")
    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.drop_constraint(
            "ck_whatsapp_messages_status_rank_nonnegative",
            type_="check",
        )
        batch_op.drop_column("status_rank")
        batch_op.drop_column("media_metadata")
        batch_op.drop_column("reply_to_meta_message_id")
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_column("media_metadata")
        batch_op.drop_column("reply_to_external_message_id")
        batch_op.drop_column("content_parts")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("provider_session_key")
        batch_op.drop_column("provider_session_id")
    with op.batch_alter_table("hermes_profiles") as batch_op:
        batch_op.drop_constraint("uq_hermes_profiles_mcp_token_hash", type_="unique")
        batch_op.drop_column("mcp_token_hash")

    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.drop_column("allowed_resources")
