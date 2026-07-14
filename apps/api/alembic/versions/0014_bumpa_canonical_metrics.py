"""Persist canonical Bumpa metric structures and optional partial quality.

Revision ID: 0014_bumpa_canonical_metrics
Revises: 0013_web_pin_challenges
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_bumpa_canonical_metrics"
down_revision: str | None = "0013_web_pin_challenges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Earlier adapter versions stored full order payloads and customer-ranking
    # analytics. Portable SQL cannot safely deep-redact arbitrary JSON across
    # SQLite/PostgreSQL, so remove only those legacy raw documents; normalized
    # commerce columns and run evidence remain intact.
    orders = sa.table("bumpa_orders", sa.column("raw_payload", sa.JSON()))
    raw_responses = sa.table(
        "bumpa_raw_responses",
        sa.column("resource", sa.String()),
        sa.column("dataset", sa.String()),
        sa.column("payload", sa.JSON()),
    )
    bind = op.get_bind()
    bind.execute(sa.update(orders).values(raw_payload={}))
    bind.execute(
        sa.update(raw_responses)
        .where(
            raw_responses.c.resource == "customers",
            raw_responses.c.dataset == "top_customers_order",
        )
        .values(payload={})
    )

    with op.batch_alter_table("bumpa_metric_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column(
                "canonical_payload",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sync_generation",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "published_sync_generation",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_check_constraint(
            "ck_bumpa_connections_sync_generation_nonnegative",
            "sync_generation >= 0",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_connections_published_generation_valid",
            "published_sync_generation >= 0 AND published_sync_generation <= sync_generation",
        )

    with op.batch_alter_table("bumpa_raw_responses") as batch_op:
        batch_op.drop_constraint("ck_bumpa_raw_responses_failure_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_failure_kind",
            "failure_kind IS NULL OR failure_kind IN "
            "('timeout', 'transport', 'upstream_http', 'invalid_response')",
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.add_column(sa.Column("sync_generation", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_sync_generation_positive",
            "sync_generation IS NULL OR sync_generation > 0",
        )
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_state", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_partial_reason", type_="check")
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_partial_reason",
            "partial_reason IS NULL OR partial_reason IN "
            "('profit_not_calculable', 'dataset_unavailable', 'dataset_error', "
            "'orders_unavailable', 'incomplete_dataset_set', 'optional_dataset_unavailable')",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_state",
            "(status IN ('queued', 'running') AND completion_quality = 'pending' "
            "AND partial_reason IS NULL AND error IS NULL) OR "
            "(status = 'success' AND completion_quality = 'complete' "
            "AND partial_reason IS NULL AND error IS NULL) OR "
            "(status = 'partial' AND completion_quality = 'accepted_partial' "
            "AND partial_reason IN ('profit_not_calculable', 'optional_dataset_unavailable') "
            "AND error IS NULL AND orders_availability = 'available' "
            "AND orders_count IS NOT NULL) OR "
            "(status = 'partial' AND completion_quality = 'degraded' "
            "AND partial_reason IN ('dataset_unavailable', 'dataset_error', "
            "'orders_unavailable', 'incomplete_dataset_set')) OR "
            "(status = 'failed' AND completion_quality = 'failed' "
            "AND partial_reason IS NULL) OR "
            "(completion_quality = 'legacy' AND partial_reason IS NULL "
            "AND orders_availability IS NULL AND orders_count IS NULL "
            "AND ((status IN ('queued', 'running', 'success', 'partial') "
            "AND error IS NULL) OR (status = 'failed' AND error IS NOT NULL)))",
        )


def downgrade() -> None:
    optional_runs = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM bumpa_sync_runs "
            "WHERE partial_reason = 'optional_dataset_unavailable'"
        )
    )
    if optional_runs:
        raise RuntimeError(
            "Cannot downgrade while accepted optional-dataset Bumpa runs exist; "
            "archive or reclassify them under a reviewed freshness policy"
        )
    invalid_responses = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM bumpa_raw_responses WHERE failure_kind = 'invalid_response'")
    )
    if invalid_responses:
        raise RuntimeError(
            "Cannot downgrade while typed invalid Bumpa response evidence exists; "
            "retain or archive it under a reviewed evidence policy"
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.drop_constraint("ck_bumpa_sync_runs_sync_generation_positive", type_="check")
        batch_op.drop_column("sync_generation")
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_state", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_partial_reason", type_="check")
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_partial_reason",
            "partial_reason IS NULL OR partial_reason IN "
            "('profit_not_calculable', 'dataset_unavailable', 'dataset_error', "
            "'orders_unavailable', 'incomplete_dataset_set')",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_state",
            "(status IN ('queued', 'running') AND completion_quality = 'pending' "
            "AND partial_reason IS NULL AND error IS NULL) OR "
            "(status = 'success' AND completion_quality = 'complete' "
            "AND partial_reason IS NULL AND error IS NULL) OR "
            "(status = 'partial' AND completion_quality = 'accepted_partial' "
            "AND partial_reason = 'profit_not_calculable' AND error IS NULL "
            "AND orders_availability = 'available' AND orders_count IS NOT NULL) OR "
            "(status = 'partial' AND completion_quality = 'degraded' "
            "AND partial_reason IN ('dataset_unavailable', 'dataset_error', "
            "'orders_unavailable', 'incomplete_dataset_set')) OR "
            "(status = 'failed' AND completion_quality = 'failed' "
            "AND partial_reason IS NULL) OR "
            "(completion_quality = 'legacy' AND partial_reason IS NULL "
            "AND orders_availability IS NULL AND orders_count IS NULL "
            "AND ((status IN ('queued', 'running', 'success', 'partial') "
            "AND error IS NULL) OR (status = 'failed' AND error IS NOT NULL)))",
        )

    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.drop_constraint("ck_bumpa_connections_published_generation_valid", type_="check")
        batch_op.drop_constraint("ck_bumpa_connections_sync_generation_nonnegative", type_="check")
        batch_op.drop_column("published_sync_generation")
        batch_op.drop_column("sync_generation")

    with op.batch_alter_table("bumpa_metric_snapshots") as batch_op:
        batch_op.drop_column("canonical_payload")

    with op.batch_alter_table("bumpa_raw_responses") as batch_op:
        batch_op.drop_constraint("ck_bumpa_raw_responses_failure_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_failure_kind",
            "failure_kind IS NULL OR failure_kind IN ('timeout', 'transport', 'upstream_http')",
        )
