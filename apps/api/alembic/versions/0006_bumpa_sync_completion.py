"""Persist typed Bumpa sync completion quality.

Revision ID: 0006_sync_completion
Revises: 0005_platform_roles
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_sync_completion"
down_revision: str | None = "0005_platform_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "completion_quality",
                sa.String(length=24),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(sa.Column("partial_reason", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("orders_availability", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("orders_count", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE bumpa_sync_runs SET completion_quality = CASE "
            "WHEN status = 'success' THEN 'complete' "
            "WHEN status = 'partial' THEN 'degraded' "
            "WHEN status = 'failed' THEN 'failed' "
            "ELSE 'pending' END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE bumpa_sync_runs SET partial_reason = 'dataset_unavailable' "
            "WHERE status = 'partial'"
        )
    )
    # A historical `success` was only assigned after the previous service had
    # observed all analytics and orders as available. Preserve that known fact
    # without inventing an order count that was not stored per run.
    op.execute(
        sa.text(
            "UPDATE bumpa_sync_runs SET orders_availability = 'available' WHERE status = 'success'"
        )
    )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_quality",
            "completion_quality IN "
            "('pending', 'complete', 'accepted_partial', 'degraded', 'failed')",
        )
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
            "AND partial_reason IS NOT NULL "
            "AND partial_reason = 'profit_not_calculable' AND error IS NULL "
            "AND orders_availability IS NOT NULL "
            "AND orders_availability = 'available' AND orders_count IS NOT NULL) OR "
            "(status = 'partial' AND completion_quality = 'degraded' "
            "AND partial_reason IS NOT NULL "
            "AND partial_reason IN ('dataset_unavailable', 'dataset_error', "
            "'orders_unavailable', 'incomplete_dataset_set')) OR "
            "(status = 'failed' AND completion_quality = 'failed' "
            "AND partial_reason IS NULL)",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_orders_count_nonnegative",
            "orders_count IS NULL OR orders_count >= 0",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_orders_availability",
            "orders_availability IS NULL OR orders_availability IN "
            "('available', 'unavailable', 'error')",
        )


def downgrade() -> None:
    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.drop_constraint("ck_bumpa_sync_runs_orders_availability", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_orders_count_nonnegative", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_state", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_partial_reason", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_quality", type_="check")
        batch_op.drop_column("orders_count")
        batch_op.drop_column("orders_availability")
        batch_op.drop_column("partial_reason")
        batch_op.drop_column("completion_quality")
