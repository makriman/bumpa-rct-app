"""App-scoped memory, fenced recovery and ordered ingress; no history reassignment."""

import json
import os

import sqlalchemy as sa

from alembic import op

revision = "0021_consultant_boundaries"
down_revision = "0020_canonical_run_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ingress_streams",
        sa.Column("scope_key", sa.String(64), primary_key=True, nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "scoped_agent_memories",
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("memory_key", sa.String(120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column(
            "source_message_id",
            sa.String(36),
            sa.ForeignKey("agent_messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_run_id",
            sa.String(36),
            sa.ForeignKey("agent_execution_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reviewed_message_id",
            sa.String(36),
            sa.ForeignKey("agent_messages.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposal_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_review", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('private','shared')", name="ck_scoped_memory_scope"),
        sa.CheckConstraint("revision > 0", name="ck_scoped_memory_revision"),
        sa.CheckConstraint(
            "status IN ('active','pending_review','deleted')", name="ck_scoped_memory_status"
        ),
        sa.UniqueConstraint(
            "tenant_id", "owner_user_id", "scope", "memory_key", name="uq_scoped_memory_owner_key"
        ),
    )
    op.create_index("ix_scoped_agent_memories_tenant_id", "scoped_agent_memories", ["tenant_id"])
    op.create_table(
        "proactive_subscriptions",
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
            "phone_identity_id",
            sa.String(36),
            sa.ForeignKey("phone_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cadence", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("local_hour", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cadence IN ('daily','weekly')", name="ck_proactive_cadence"),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_proactive_status"),
        sa.UniqueConstraint("tenant_id", "user_id", "cadence", name="uq_proactive_user_cadence"),
        sa.CheckConstraint(
            "local_hour >= 0 AND local_hour < 24 AND weekday >= 0 AND weekday < 7",
            name="ck_proactive_schedule",
        ),
    )
    op.create_index(
        "ix_proactive_subscriptions_tenant_id", "proactive_subscriptions", ["tenant_id"]
    )
    op.add_column(
        "agent_execution_runs", sa.Column("request_snapshots_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "agent_execution_runs",
        sa.Column("reconcile_epoch", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_execution_runs", sa.Column("output_message_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "agent_execution_runs",
        sa.Column("delivery_effect_keys", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("agent_execution_runs", sa.Column("job_id", sa.String(36), nullable=True))
    op.add_column("agent_execution_runs", sa.Column("job_claim_epoch", sa.Integer(), nullable=True))
    op.add_column(
        "agent_execution_runs", sa.Column("job_stale_lock_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_messages", sa.Column("initiating_message_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "pending_agent_actions", sa.Column("authorization_revision", sa.String(64), nullable=True)
    )
    op.add_column(
        "pending_agent_actions",
        sa.Column("approval_binding", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "provider_webhook_events", sa.Column("ordering_scope", sa.String(64), nullable=True)
    )
    op.add_column(
        "provider_webhook_events", sa.Column("arrival_sequence", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "async_jobs", sa.Column("claim_epoch", sa.Integer(), nullable=False, server_default="0")
    )
    with op.batch_alter_table("agent_execution_runs") as batch:
        batch.drop_constraint("ck_agent_execution_run_status", type_="check")
        batch.create_check_constraint(
            "ck_agent_execution_run_status",
            "status IN ('running','completed','failed','expired','reconciling','unknown')",
        )
    with op.batch_alter_table("agent_messages") as batch:
        batch.create_foreign_key(
            "fk_agent_initiating_message",
            "agent_messages",
            ["initiating_message_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_agent_messages_initiating_message_id", ["initiating_message_id"])
    with op.batch_alter_table("provider_webhook_events") as batch:
        batch.create_unique_constraint(
            "uq_webhook_arrival_sequence", ["ordering_scope", "arrival_sequence"]
        )
        batch.create_index("ix_provider_webhook_events_ordering_scope", ["ordering_scope"])
    if op.get_bind().dialect.name == "postgresql":
        predicate = "current_setting('app.is_privileged', true) = 'true' OR tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
        for table in ("scoped_agent_memories", "proactive_subscriptions"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"
            )


def downgrade():
    # Application rollback keeps this additive production schema. Only a local
    # migration rehearsal that has never used the new contract may remove it.
    environment = os.environ.get("APP_ENV", "local").strip().lower()
    if environment == "production":
        raise RuntimeError("Production keeps the additive consultant boundary schema")
    if environment not in {"local", "test", "development"}:
        raise RuntimeError("Only unused local consultant boundary schema can be downgraded")
    bind = op.get_bind()
    # Check and reverse under one write boundary: another local process must
    # not begin using a field after its pristine-state check but before DDL.
    if bind.dialect.name == "sqlite":
        if not bind.connection.driver_connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "LOCK TABLE agent_execution_runs, agent_messages, async_jobs, ingress_streams, "
                "operational_agent_events, pending_agent_actions, proactive_subscriptions, "
                "provider_webhook_events, scoped_agent_memories, usage_events IN ACCESS EXCLUSIVE MODE"
            )
        )
        # FORCE RLS must not hide used rows from a schema-owner migration role.
        # Keep this transaction-local context through the earlier 0020 guard.
        bind.execute(sa.text("SELECT set_config('app.is_privileged', 'true', true)"))
    for table in ("ingress_streams", "scoped_agent_memories", "proactive_subscriptions"):
        if bind.execute(sa.select(sa.literal(1)).select_from(sa.table(table)).limit(1)).first():
            raise RuntimeError("Retain consultant boundary schema after new feature data exists")
    used_fields = (
        (
            "agent_execution_runs",
            "request_snapshots_encrypted IS NOT NULL OR reconcile_epoch <> 0 OR output_message_id IS NOT NULL OR job_id IS NOT NULL OR job_claim_epoch IS NOT NULL OR job_stale_lock_seconds IS NOT NULL OR status IN ('reconciling','unknown')",
        ),
        ("agent_messages", "initiating_message_id IS NOT NULL"),
        ("pending_agent_actions", "authorization_revision IS NOT NULL"),
        ("provider_webhook_events", "ordering_scope IS NOT NULL OR arrival_sequence IS NOT NULL"),
        ("async_jobs", "claim_epoch <> 0"),
        ("operational_agent_events", "event_type = 'run_reconciliation'"),
    )
    for table, predicate in used_fields:
        if bind.execute(
            sa.select(sa.literal(1)).select_from(sa.table(table)).where(sa.text(predicate)).limit(1)
        ).first():
            raise RuntimeError("Retain consultant boundary schema after boundary metadata is used")
    for table, column, empty in (
        ("agent_execution_runs", "delivery_effect_keys", []),
        ("pending_agent_actions", "approval_binding", {}),
    ):
        source = sa.table(table, sa.column(column))
        for value in bind.execute(sa.select(source.c[column])).scalars():
            if _json_value(value) != empty:
                raise RuntimeError(
                    "Retain consultant boundary schema after boundary metadata is used"
                )
    metadata_keys = {
        "event_kind",
        "execution_run_id",
        "recovered",
        "model_outcome",
        "provider_run_id",
        "voice_playback_state",
        "reconcile_epoch",
    }
    for table, column in (
        ("agent_messages", "media_metadata"),
        ("operational_agent_events", "metadata"),
        ("usage_events", "metadata"),
    ):
        source = sa.table(table, sa.column(column))
        for value in bind.execute(sa.select(source.c[column])).scalars():
            metadata = _json_value(value)
            if isinstance(metadata, dict) and metadata_keys.intersection(metadata):
                raise RuntimeError(
                    "Retain consultant boundary schema after recovery metadata exists"
                )

    with op.batch_alter_table("provider_webhook_events") as batch:
        batch.drop_index("ix_provider_webhook_events_ordering_scope")
        batch.drop_constraint("uq_webhook_arrival_sequence", type_="unique")
        batch.drop_column("arrival_sequence")
        batch.drop_column("ordering_scope")
    with op.batch_alter_table("agent_messages") as batch:
        batch.drop_index("ix_agent_messages_initiating_message_id")
        batch.drop_constraint("fk_agent_initiating_message", type_="foreignkey")
        batch.drop_column("initiating_message_id")
    with op.batch_alter_table("agent_execution_runs") as batch:
        batch.drop_constraint("ck_agent_execution_run_status", type_="check")
        batch.create_check_constraint(
            "ck_agent_execution_run_status", "status IN ('running','completed','failed','expired')"
        )
        for column in (
            "job_stale_lock_seconds",
            "job_claim_epoch",
            "job_id",
            "delivery_effect_keys",
            "output_message_id",
            "reconcile_epoch",
            "request_snapshots_encrypted",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("pending_agent_actions") as batch:
        batch.drop_column("approval_binding")
        batch.drop_column("authorization_revision")
    op.drop_column("async_jobs", "claim_epoch")
    op.drop_index("ix_proactive_subscriptions_tenant_id", table_name="proactive_subscriptions")
    op.drop_table("proactive_subscriptions")
    op.drop_index("ix_scoped_agent_memories_tenant_id", table_name="scoped_agent_memories")
    op.drop_table("scoped_agent_memories")
    op.drop_table("ingress_streams")


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            # Malformed metadata is evidence too; do not discard it.
            return value
    return value
