"""Add the stable tenant-user conversation history index.

Revision ID: 0016_chat_pagination
Revises: 0015_bumpa_store_context
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_chat_pagination"
down_revision: str | None = "0015_bumpa_store_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.create_index(
            "ix_conversation_tenant_user_updated_id",
            ["tenant_id", "user_id", "updated_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_index("ix_conversation_tenant_user_updated_id")
