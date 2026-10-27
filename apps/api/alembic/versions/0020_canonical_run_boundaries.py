"""Add canonical generation/receiver boundaries and application run fences.

Existing conversations remain generation0; no transcript/session reassignment.
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_canonical_run_boundaries"
down_revision = "0019_business_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("context_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("receiver_waba_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("receiver_phone_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("environment_lane", sa.String(32), nullable=False, server_default=""),
        sa.Column("active_run_id", sa.String(36)),
        sa.Column("pending_utterance", sa.JSON(), nullable=False, server_default="{}"),
    ):
        op.add_column("conversations", column)
    op.create_table(
        "agent_execution_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "initiating_message_id",
            sa.String(36),
            sa.ForeignKey("agent_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_identity_id", sa.String(36)),
        sa.Column("provider_run_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("context_digest", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','expired')",
            name="ck_agent_execution_run_status",
        ),
    )
    op.create_index(
        "ix_execution_run_tenant_conversation",
        "agent_execution_runs",
        ["tenant_id", "conversation_id"],
    )
    op.add_column("generated_agent_media", sa.Column("execution_run_id", sa.String(36)))
    with op.batch_alter_table("generated_agent_media") as batch:
        batch.create_foreign_key(
            "fk_media_execution_run",
            "agent_execution_runs",
            ["execution_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_generated_agent_media_execution_run_id", ["execution_run_id"])
    op.add_column(
        "agent_messages", sa.Column("event_scope", sa.String(64), nullable=False, server_default="")
    )
    with op.batch_alter_table("agent_messages") as batch:
        batch.drop_constraint(
            "uq_agent_messages_tenant_channel_external_message_id", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_agent_messages_tenant_channel_external_message_id",
            ["tenant_id", "user_id", "channel", "event_scope", "external_message_id"],
        )
    op.add_column(
        "whatsapp_messages",
        sa.Column("receiver_scope", sa.String(64), nullable=False, server_default=""),
    )
    op.drop_index("ix_whatsapp_messages_meta_message_id", table_name="whatsapp_messages")
    op.create_index(
        "ix_whatsapp_messages_meta_message_id", "whatsapp_messages", ["meta_message_id"]
    )
    with op.batch_alter_table("whatsapp_messages") as batch:
        batch.create_unique_constraint(
            "uq_whatsapp_receiver_message", ["receiver_scope", "meta_message_id"]
        )
    op.add_column(
        "whatsapp_delivery_events",
        sa.Column("receiver_scope", sa.String(64), nullable=False, server_default=""),
    )
    # The initial schema used an unnamed delivery constraint. Name it deterministically
    # during SQLite batch reflection; PostgreSQL exposes its generated catalog name.
    bind = op.get_bind()
    unique = next(
        item
        for item in sa.inspect(bind).get_unique_constraints("whatsapp_delivery_events")
        if item["column_names"] == ["meta_message_id", "status", "event_timestamp"]
    )
    naming = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("whatsapp_delivery_events", naming_convention=naming) as batch:
        batch.drop_constraint(
            unique["name"] or "uq_whatsapp_delivery_events_meta_message_id", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_whatsapp_receiver_delivery",
            ["receiver_scope", "meta_message_id", "status", "event_timestamp"],
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE agent_execution_runs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE agent_execution_runs FORCE ROW LEVEL SECURITY")
        predicate = "current_setting('app.is_privileged', true) = 'true' OR tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
        op.execute(
            f"CREATE POLICY tenant_isolation ON agent_execution_runs USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade():
    # Only for unused local migration; preserve additive schema on production rollback.
    import os

    if os.environ.get("APP_ENV") == "production":
        raise RuntimeError("Production keeps the additive canonical schema")
    bind = op.get_bind()
    if (
        bind.execute(sa.text("SELECT count(*) FROM agent_execution_runs")).scalar()
        or bind.execute(
            sa.text("SELECT count(*) FROM conversations WHERE context_generation = 1")
        ).scalar()
        or bind.execute(
            sa.text("SELECT count(*) FROM agent_messages WHERE event_scope <> ''")
        ).scalar()
    ):
        raise RuntimeError("Retain the additive schema after canonical data exists")
    with op.batch_alter_table("agent_messages") as batch:
        batch.drop_constraint(
            "uq_agent_messages_tenant_channel_external_message_id", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_agent_messages_tenant_channel_external_message_id",
            ["tenant_id", "channel", "external_message_id"],
        )
    op.drop_column("agent_messages", "event_scope")
    with op.batch_alter_table("whatsapp_delivery_events") as batch:
        batch.drop_constraint("uq_whatsapp_receiver_delivery", type_="unique")
        batch.create_unique_constraint(
            "uq_whatsapp_delivery_legacy", ["meta_message_id", "status", "event_timestamp"]
        )
        batch.drop_column("receiver_scope")
    with op.batch_alter_table("whatsapp_messages") as batch:
        batch.drop_constraint("uq_whatsapp_receiver_message", type_="unique")
        batch.drop_index("ix_whatsapp_messages_meta_message_id")
        batch.create_index("ix_whatsapp_messages_meta_message_id", ["meta_message_id"], unique=True)
        batch.drop_column("receiver_scope")
    with op.batch_alter_table("generated_agent_media") as batch:
        batch.drop_index("ix_generated_agent_media_execution_run_id")
        batch.drop_constraint("fk_media_execution_run", type_="foreignkey")
        batch.drop_column("execution_run_id")
    op.drop_table("agent_execution_runs")
    for name in (
        "pending_utterance",
        "active_run_id",
        "environment_lane",
        "receiver_phone_id",
        "receiver_waba_id",
        "context_generation",
    ):
        op.drop_column("conversations", name)
