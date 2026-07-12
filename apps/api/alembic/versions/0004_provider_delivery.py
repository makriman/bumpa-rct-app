"""Add outbound provider delivery idempotency tracking.

Revision ID: 0004_provider_delivery
Revises: 0003_async_runtime
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_provider_delivery"
down_revision: str | None = "0003_async_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=160), nullable=True))
        batch_op.create_index(
            "ix_whatsapp_messages_idempotency_key",
            ["idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.drop_index("ix_whatsapp_messages_idempotency_key")
        batch_op.drop_column("idempotency_key")
