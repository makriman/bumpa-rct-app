"""Persist explicit acceptance of available data during tenant onboarding."""

import sqlalchemy as sa

from alembic import op

revision = "0022_partial_data_onboarding"
down_revision = "0021_consultant_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenant_onboardings",
        sa.Column(
            "initial_sync_partial_accepted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade():
    op.drop_column("tenant_onboardings", "initial_sync_partial_accepted")
