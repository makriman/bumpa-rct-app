"""Add durable merchant business memory with forced tenant RLS.

Revision ID: 0019_business_onboarding
Revises: 0018_agent_capability_audit
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_business_onboarding"
down_revision: str | None = "0018_agent_capability_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_business_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_merchant_business_profiles_tenant_id",
        "merchant_business_profiles",
        ["tenant_id"],
        unique=True,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE merchant_business_profiles ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE merchant_business_profiles FORCE ROW LEVEL SECURITY")
        predicate = (
            "current_setting('app.is_privileged', true) = 'true' OR "
            "tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation ON merchant_business_profiles USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_merchant_business_profiles_tenant_id", table_name="merchant_business_profiles"
    )
    op.drop_table("merchant_business_profiles")
