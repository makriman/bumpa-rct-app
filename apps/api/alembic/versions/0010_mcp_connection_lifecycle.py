"""Harden the MCP approval and OAuth connection lifecycle.

Revision ID: 0010_mcp_lifecycle
Revises: 0009_research_instrumentation
"""

from sqlalchemy import text

from alembic import op

revision: str = "0010_mcp_lifecycle"
down_revision: str | None = "0009_research_instrumentation"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        text(
            "SELECT tenant_id, provider FROM mcp_connections "
            "GROUP BY tenant_id, provider HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "MCP connection lifecycle migration found duplicate tenant/provider rows"
        )
    invalid_status = connection.execute(
        text(
            "SELECT id FROM mcp_connections WHERE status NOT IN "
            "('disabled', 'admin_pending', 'approved', 'oauth_in_progress', "
            "'active', 'rejected', 'error') LIMIT 1"
        )
    ).first()
    if invalid_status is not None:
        raise RuntimeError("MCP connection lifecycle migration found an invalid status")
    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.create_check_constraint(
            "ck_mcp_connections_status",
            "status IN ('disabled', 'admin_pending', 'approved', 'oauth_in_progress', "
            "'active', 'rejected', 'error')",
        )
        batch_op.create_unique_constraint(
            "uq_mcp_connections_tenant_provider",
            ["tenant_id", "provider"],
        )


def downgrade() -> None:
    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.drop_constraint("uq_mcp_connections_tenant_provider", type_="unique")
        batch_op.drop_constraint("ck_mcp_connections_status", type_="check")
