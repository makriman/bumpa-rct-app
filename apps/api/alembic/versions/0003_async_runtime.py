"""Add durable asynchronous jobs and transactional queue outbox.

Revision ID: 0003_async_runtime
Revises: 0002_schema_completeness
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_async_runtime"
down_revision: str | None = "0002_schema_completeness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_PREDICATE = (
    "current_setting('app.is_privileged', true) = 'true' OR "
    "tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        "async_jobs",
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("queue_name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=160), nullable=True),
        sa.Column("last_error", sa.String(length=240), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_async_jobs_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_async_jobs_max_attempts_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'retry', 'succeeded', "
            "'dead_letter', 'cancelled')",
            name="ck_async_jobs_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "queue_name", "idempotency_key", name="uq_async_jobs_idempotency"
        ),
    )
    op.create_index("ix_async_jobs_kind", "async_jobs", ["kind"])
    op.create_index(
        "ix_async_jobs_dispatch",
        "async_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_async_jobs_tenant_created", "async_jobs", ["tenant_id", "created_at"]
    )

    op.create_table(
        "job_outbox",
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "dispatch_attempts >= 0", name="ck_job_outbox_attempts_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched')", name="ck_job_outbox_status"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["async_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_job_outbox_job"),
    )
    op.create_index("ix_job_outbox_job_id", "job_outbox", ["job_id"])
    op.create_index("ix_job_outbox_tenant_id", "job_outbox", ["tenant_id"])
    op.create_index(
        "ix_job_outbox_due", "job_outbox", ["status", "available_at", "created_at"]
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in ("async_jobs", "job_outbox"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
            )


def downgrade() -> None:
    op.drop_index("ix_job_outbox_due", table_name="job_outbox")
    op.drop_index("ix_job_outbox_tenant_id", table_name="job_outbox")
    op.drop_index("ix_job_outbox_job_id", table_name="job_outbox")
    op.drop_table("job_outbox")
    op.drop_index("ix_async_jobs_tenant_created", table_name="async_jobs")
    op.drop_index("ix_async_jobs_dispatch", table_name="async_jobs")
    op.drop_index("ix_async_jobs_kind", table_name="async_jobs")
    op.drop_table("async_jobs")
