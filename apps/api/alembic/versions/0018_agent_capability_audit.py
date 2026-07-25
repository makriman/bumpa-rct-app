"""Complete managed media delivery and dormant Home Assistant support.

Revision ID: 0018_agent_capability_audit
Revises: 0017_agent_capabilities
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_agent_capability_audit"
down_revision: str | None = "0017_agent_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generated_agent_media") as batch_op:
        batch_op.add_column(sa.Column("filename", sa.String(length=200)))
        batch_op.add_column(sa.Column("agent_message_id", sa.String(length=36)))
        batch_op.create_foreign_key(
            "fk_generated_agent_media_agent_message_id",
            "agent_messages",
            ["agent_message_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_generated_agent_media_agent_message_id",
        "generated_agent_media",
        ["agent_message_id"],
    )

    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.drop_constraint("ck_mcp_connections_provider", type_="check")
        batch_op.create_check_constraint(
            "ck_mcp_connections_provider",
            "provider IN ('google_drive', 'google_sheets', 'gmail', 'calendar', "
            "'meta_ads', 'home_assistant')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    remaining = bind.execute(
        sa.text(
            "SELECT id FROM mcp_connections WHERE provider = 'home_assistant' LIMIT 1"
        )
    ).first()
    if remaining is not None:
        raise RuntimeError(
            "Cannot downgrade while Home Assistant connections still exist"
        )
    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.drop_constraint("ck_mcp_connections_provider", type_="check")
        batch_op.create_check_constraint(
            "ck_mcp_connections_provider",
            "provider IN ('google_drive', 'google_sheets', 'gmail', 'calendar', 'meta_ads')",
        )

    op.drop_index(
        "ix_generated_agent_media_agent_message_id",
        table_name="generated_agent_media",
    )
    with op.batch_alter_table("generated_agent_media") as batch_op:
        batch_op.drop_constraint(
            "fk_generated_agent_media_agent_message_id",
            type_="foreignkey",
        )
        batch_op.drop_column("agent_message_id")
        batch_op.drop_column("filename")
