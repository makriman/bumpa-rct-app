"""Add the durable tenant onboarding saga.

Revision ID: 0011_tenant_onboarding
Revises: 0010_mcp_lifecycle
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_tenant_onboarding"
down_revision: str | None = "0010_mcp_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_PREDICATE = (
    "current_setting('app.is_privileged', true) = 'true' OR "
    "tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
)


def upgrade() -> None:
    invalid_tenant_status = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id FROM tenants WHERE status NOT IN "
                "('active', 'suspended', 'archived', 'provisioning') LIMIT 1"
            )
        )
        .first()
    )
    if invalid_tenant_status is not None:
        raise RuntimeError("Tenant onboarding migration found an invalid tenant status")
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.create_check_constraint(
            "ck_tenants_status",
            "status IN ('active', 'suspended', 'archived', 'provisioning')",
        )

    op.create_table(
        "tenant_onboardings",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_step", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("start_idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("start_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("owner_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("owner_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("phone_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("phone_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("bumpa_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("bumpa_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("initial_sync_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("initial_sync_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "initial_sync_accept_idempotency_key_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("initial_sync_accept_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("hermes_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("hermes_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("complete_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("complete_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("owner_membership_id", sa.String(length=36), nullable=True),
        sa.Column("phone_identity_id", sa.String(length=36), nullable=True),
        sa.Column("bumpa_connection_id", sa.String(length=36), nullable=True),
        sa.Column("initial_sync_job_id", sa.String(length=36), nullable=True),
        sa.Column("initial_sync_run_id", sa.String(length=36), nullable=True),
        sa.Column("sync_attempt", sa.Integer(), nullable=False),
        sa.Column("hermes_profile_id", sa.String(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_step", sa.String(length=24), nullable=True),
        sa.Column("failure_retryable", sa.Boolean(), nullable=True),
        sa.Column("failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_progress', 'attention_required', 'completed')",
            name="ck_tenant_onboardings_status",
        ),
        sa.CheckConstraint(
            "current_step IN "
            "('owner', 'phone', 'bumpa', 'initial_sync', 'hermes', 'review', 'completed')",
            name="ck_tenant_onboardings_current_step",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_tenant_onboardings_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "sync_attempt >= 0",
            name="ck_tenant_onboardings_sync_attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND current_step = 'completed' AND completed_at IS NOT NULL) "
            "OR (status != 'completed' AND current_step != 'completed' AND completed_at IS NULL)",
            name="ck_tenant_onboardings_completion_state",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["owner_membership_id"], ["tenant_memberships.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["phone_identity_id"], ["phone_identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["bumpa_connection_id"], ["bumpa_connections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["initial_sync_job_id"], ["async_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["initial_sync_run_id"], ["bumpa_sync_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["hermes_profile_id"], ["hermes_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_onboardings_tenant_id"),
        sa.UniqueConstraint(
            "start_idempotency_key_hash",
            name="uq_tenant_onboardings_start_idempotency_key_hash",
        ),
    )
    op.create_index(
        "ix_tenant_onboardings_tenant_id",
        "tenant_onboardings",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tenant_onboardings_status_updated",
        "tenant_onboardings",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_tenant_onboardings_step_updated",
        "tenant_onboardings",
        ["current_step", "updated_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "tenant_onboardings" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "tenant_onboardings" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY tenant_isolation ON "tenant_onboardings" '
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )


def downgrade() -> None:
    op.drop_index("ix_tenant_onboardings_step_updated", table_name="tenant_onboardings")
    op.drop_index("ix_tenant_onboardings_status_updated", table_name="tenant_onboardings")
    op.drop_index("ix_tenant_onboardings_tenant_id", table_name="tenant_onboardings")
    op.drop_table("tenant_onboardings")
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_constraint("ck_tenants_status", type_="check")
