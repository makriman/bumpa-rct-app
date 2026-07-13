"""Persist typed transport evidence for degraded Bumpa datasets.

Revision ID: 0008_bumpa_dataset_failures
Revises: 0007_legacy_sync_writer
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_bumpa_dataset_failures"
down_revision: str | None = "0007_legacy_sync_writer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bumpa_raw_responses") as batch_op:
        batch_op.drop_constraint("ck_bumpa_raw_responses_http_status", type_="check")
        batch_op.alter_column("http_status", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("failure_kind", sa.String(length=32), nullable=True))
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_http_status",
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_failure_kind",
            "failure_kind IS NULL OR failure_kind IN ('timeout', 'transport', 'upstream_http')",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_status_evidence",
            "http_status IS NOT NULL OR "
            "(failure_kind IS NOT NULL AND failure_kind IN ('timeout', 'transport'))",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_failure_availability",
            "failure_kind IS NULL OR availability = 'error'",
        )


def downgrade() -> None:
    missing_statuses = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM bumpa_raw_responses WHERE http_status IS NULL")
    )
    if missing_statuses:
        raise RuntimeError(
            "Cannot downgrade while Bumpa transport failures without HTTP responses exist; "
            "retain the typed evidence or archive it under a reviewed retention policy"
        )

    with op.batch_alter_table("bumpa_raw_responses") as batch_op:
        batch_op.drop_constraint("ck_bumpa_raw_responses_failure_availability", type_="check")
        batch_op.drop_constraint("ck_bumpa_raw_responses_status_evidence", type_="check")
        batch_op.drop_constraint("ck_bumpa_raw_responses_failure_kind", type_="check")
        batch_op.drop_constraint("ck_bumpa_raw_responses_http_status", type_="check")
        batch_op.drop_column("failure_kind")
        batch_op.alter_column("http_status", existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_http_status",
            "http_status BETWEEN 100 AND 599",
        )
