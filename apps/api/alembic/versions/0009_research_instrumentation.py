"""Complete consent-gated research instrumentation fields.

Revision ID: 0009_research_instrumentation
Revises: 0008_bumpa_dataset_failures
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_research_instrumentation"
down_revision: str | None = "0008_bumpa_dataset_failures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_AGENT_MESSAGE_UQ_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s"}


def upgrade() -> None:
    bind = op.get_bind()
    legacy_constraint = next(
        constraint
        for constraint in sa.inspect(bind).get_unique_constraints("agent_messages")
        if constraint["column_names"] == ["channel", "external_message_id"]
    )
    legacy_constraint_name = (
        legacy_constraint["name"] or "uq_agent_messages_channel_external_message_id"
    )
    with op.batch_alter_table(
        "agent_messages",
        naming_convention=LEGACY_AGENT_MESSAGE_UQ_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(legacy_constraint_name, type_="unique")
        batch_op.create_unique_constraint(
            "uq_agent_messages_tenant_channel_external_message_id",
            ["tenant_id", "channel", "external_message_id"],
        )

    with op.batch_alter_table("research_events") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("language", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("agent_confidence", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("response_length_chars", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("response_latency_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("follow_up_detected", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "raw_text_present",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "business_outcome",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "quality_flags",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    # Historical events predate an explicit source key. Their primary key is
    # already globally unique, so this deterministic namespace preserves every
    # row while allowing the new writer to enforce exactly-once evidence.
    op.execute(
        sa.text(
            "UPDATE research_events "
            "SET idempotency_key = 'legacy:' || id "
            "WHERE idempotency_key IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE research_events SET raw_text_present = TRUE WHERE agent_message_id IS NOT NULL"
        )
    )

    with op.batch_alter_table("research_events") as batch_op:
        batch_op.alter_column(
            "idempotency_key",
            existing_type=sa.String(length=160),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_research_events_idempotency_key",
            ["idempotency_key"],
        )
        batch_op.create_check_constraint(
            "ck_research_events_agent_confidence",
            "agent_confidence IS NULL OR agent_confidence IN ('low', 'medium', 'high')",
        )
        batch_op.create_check_constraint(
            "ck_research_events_response_length_nonnegative",
            "response_length_chars IS NULL OR response_length_chars >= 0",
        )
        batch_op.create_check_constraint(
            "ck_research_events_response_latency_nonnegative",
            "response_latency_ms IS NULL OR response_latency_ms >= 0",
        )
        batch_op.create_index(
            "ix_research_event_type_created",
            ["event_type", "created_at"],
            unique=False,
        )

    with op.batch_alter_table("research_reports") as batch_op:
        batch_op.add_column(
            sa.Column(
                "artifact_kind",
                sa.String(length=16),
                nullable=False,
                server_default="report",
            )
        )
        batch_op.create_check_constraint(
            "ck_research_reports_artifact_kind",
            "artifact_kind IN ('report', 'export')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT channel, external_message_id "
            "FROM agent_messages "
            "WHERE external_message_id IS NOT NULL "
            "GROUP BY channel, external_message_id "
            "HAVING COUNT(*) > 1 "
            "LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade research instrumentation while tenant-scoped external "
            "message identifiers overlap"
        )

    with op.batch_alter_table("research_reports") as batch_op:
        batch_op.drop_constraint("ck_research_reports_artifact_kind", type_="check")
        batch_op.drop_column("artifact_kind")

    with op.batch_alter_table("research_events") as batch_op:
        batch_op.drop_index("ix_research_event_type_created")
        batch_op.drop_constraint(
            "ck_research_events_response_latency_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_research_events_response_length_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint("ck_research_events_agent_confidence", type_="check")
        batch_op.drop_constraint("uq_research_events_idempotency_key", type_="unique")
        batch_op.drop_column("quality_flags")
        batch_op.drop_column("business_outcome")
        batch_op.drop_column("follow_up_detected")
        batch_op.drop_column("raw_text_present")
        batch_op.drop_column("response_latency_ms")
        batch_op.drop_column("response_length_chars")
        batch_op.drop_column("agent_confidence")
        batch_op.drop_column("language")
        batch_op.drop_column("idempotency_key")

    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_constraint(
            "uq_agent_messages_tenant_channel_external_message_id",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_agent_messages_channel_external_message_id",
            ["channel", "external_message_id"],
        )
