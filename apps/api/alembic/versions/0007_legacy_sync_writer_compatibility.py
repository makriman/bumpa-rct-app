"""Keep pre-0006 Bumpa sync writers rollback-compatible.

Revision ID: 0007_legacy_sync_writer
Revises: 0006_sync_completion
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_legacy_sync_writer"
down_revision: str | None = "0006_sync_completion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPED_COMPLETION_QUALITY = (
    "completion_quality IN ('pending', 'complete', 'accepted_partial', 'degraded', 'failed')"
)
_COMPATIBLE_COMPLETION_QUALITY = (
    "completion_quality IN "
    "('legacy', 'pending', 'complete', 'accepted_partial', 'degraded', 'failed')"
)
_TYPED_COMPLETION_STATE = (
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
    "AND partial_reason IS NULL)"
)
_LEGACY_COMPLETION_STATE = (
    "(completion_quality = 'legacy' "
    "AND partial_reason IS NULL "
    "AND orders_availability IS NULL "
    "AND orders_count IS NULL "
    "AND ((status IN ('queued', 'running', 'success', 'partial') AND error IS NULL) "
    "OR (status = 'failed' AND error IS NOT NULL)))"
)


def upgrade() -> None:
    # A pre-0006 application omits every completion-evidence column. A distinct
    # server default lets that old writer finish in-flight work after an image
    # rollback without weakening the typed states written by the current ORM.
    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_state", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_quality", type_="check")

    # 0006 stamped its `pending` server default onto rows created by a legacy
    # writer. Convert only in-flight, evidence-free rows: the current writer can
    # also safely finish these because it always replaces the quality with a
    # fully typed terminal value.
    op.execute(
        sa.text(
            "UPDATE bumpa_sync_runs SET completion_quality = 'legacy' "
            "WHERE status IN ('queued', 'running') "
            "AND completion_quality = 'pending' "
            "AND partial_reason IS NULL "
            "AND orders_availability IS NULL "
            "AND orders_count IS NULL "
            "AND error IS NULL"
        )
    )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.alter_column(
            "completion_quality",
            existing_type=sa.String(length=24),
            existing_nullable=False,
            server_default="legacy",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_quality",
            _COMPATIBLE_COMPLETION_QUALITY,
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_state",
            f"({_TYPED_COMPLETION_STATE}) OR {_LEGACY_COMPLETION_STATE}",
        )


def downgrade() -> None:
    # A legacy terminal state does not carry 0006's evidence. Never invent that
    # evidence during a schema downgrade. Operators must first deploy a current
    # writer. Any later removal or archival of legacy audit evidence requires a
    # separately reviewed retention plan (the production runbook already
    # forbids destructive down-migrations during outage recovery).
    legacy_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM bumpa_sync_runs WHERE completion_quality = 'legacy'")
    )
    if legacy_count:
        raise RuntimeError(
            "Cannot downgrade while legacy Bumpa sync runs exist; "
            "retain the forward-compatible schema unless a reviewed retention plan "
            "removes or archives those rows"
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_state", type_="check")
        batch_op.drop_constraint("ck_bumpa_sync_runs_completion_quality", type_="check")
        batch_op.alter_column(
            "completion_quality",
            existing_type=sa.String(length=24),
            existing_nullable=False,
            server_default="pending",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_quality",
            _TYPED_COMPLETION_QUALITY,
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_completion_state",
            _TYPED_COMPLETION_STATE,
        )
