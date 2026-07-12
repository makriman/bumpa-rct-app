"""Constrain platform roles to supported authorization values.

Revision ID: 0005_platform_roles
Revises: 0004_provider_delivery
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_platform_roles"
down_revision: str | None = "0004_provider_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("platform_roles") as batch_op:
        batch_op.create_check_constraint(
            "ck_platform_roles_role",
            "role IN ('operator', 'researcher', 'superadmin')",
        )


def downgrade() -> None:
    with op.batch_alter_table("platform_roles") as batch_op:
        batch_op.drop_constraint("ck_platform_roles_role", type_="check")
