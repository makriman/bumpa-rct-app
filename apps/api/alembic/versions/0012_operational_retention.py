"""Add indexes for bounded audit and system-error retention scans.

Revision ID: 0012_operational_retention
Revises: 0011_tenant_onboarding
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_operational_retention"
down_revision: str | None = "0011_tenant_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_created_at",
        "audit_logs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_errors_created_at",
        "system_errors",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_system_errors_created_at", table_name="system_errors")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
