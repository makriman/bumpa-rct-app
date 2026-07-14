"""Allow provider-free temporary web PIN challenges.

Revision ID: 0013_web_pin_challenges
Revises: 0012_operational_retention
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_web_pin_challenges"
down_revision: str | None = "0012_operational_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("otp_sessions") as batch_op:
        batch_op.drop_constraint("ck_otp_sessions_purpose", type_="check")
        batch_op.create_check_constraint(
            "ck_otp_sessions_purpose",
            "purpose IN ('login', 'invite', 'phone_verify', 'temporary_web_pin')",
        )
    op.create_index(
        "uq_otp_sessions_active_temporary_web_pin",
        "otp_sessions",
        ["phone_e164"],
        unique=True,
        postgresql_where=sa.text("purpose = 'temporary_web_pin' AND consumed_at IS NULL"),
        sqlite_where=sa.text("purpose = 'temporary_web_pin' AND consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_otp_sessions_active_temporary_web_pin",
        table_name="otp_sessions",
    )
    op.execute("DELETE FROM otp_sessions WHERE purpose = 'temporary_web_pin'")
    with op.batch_alter_table("otp_sessions") as batch_op:
        batch_op.drop_constraint("ck_otp_sessions_purpose", type_="check")
        batch_op.create_check_constraint(
            "ck_otp_sessions_purpose",
            "purpose IN ('login', 'invite', 'phone_verify')",
        )
