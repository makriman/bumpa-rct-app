"""Add the missing commerce, agent, MCP, and operations schema.

Revision ID: 0002_schema_completeness
Revises: 0001_initial
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_schema_completeness"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_RLS_TABLES = (
    "bumpa_order_items",
    "agent_tool_calls",
    "mcp_tool_permissions",
)

_TENANT_PREDICATE = (
    "current_setting('app.is_privileged', true) = 'true' OR "
    "tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
)


def _ip_address_type() -> sa.types.TypeEngine[object]:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.INET()
    return sa.String(length=45)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TENANT_RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )


def upgrade() -> None:
    with op.batch_alter_table("otp_sessions") as batch_op:
        batch_op.create_check_constraint(
            "ck_otp_sessions_purpose",
            "purpose IN ('login', 'invite', 'phone_verify')",
        )
        batch_op.create_check_constraint(
            "ck_otp_sessions_attempts_nonnegative",
            "attempts >= 0",
        )
        batch_op.create_index(
            "ix_otp_sessions_phone_expires",
            ["phone_e164", "expires_at"],
            unique=False,
        )

    with op.batch_alter_table("tenant_memberships") as batch_op:
        batch_op.create_check_constraint(
            "ck_tenant_memberships_role",
            "role IN ('owner', 'admin', 'member', 'researcher', 'operator', 'superadmin')",
        )

    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.create_check_constraint(
            "ck_bumpa_connections_scope_type",
            "scope_type IN ('business_id', 'location_id')",
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.add_column(sa.Column("rate_limit_limit", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_remaining", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_status",
            "status IN ('queued', 'running', 'success', 'partial', 'failed')",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_rate_limit_nonnegative",
            "rate_limit_limit IS NULL OR rate_limit_limit >= 0",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_rate_remaining_nonnegative",
            "rate_limit_remaining IS NULL OR rate_limit_remaining >= 0",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_rate_remaining_within_limit",
            "rate_limit_limit IS NULL OR rate_limit_remaining IS NULL "
            "OR rate_limit_remaining <= rate_limit_limit",
        )

    with op.batch_alter_table("bumpa_raw_responses") as batch_op:
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_availability",
            "availability IN ('available', 'unavailable', 'error')",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_raw_responses_http_status",
            "http_status BETWEEN 100 AND 599",
        )

    with op.batch_alter_table("bumpa_metric_snapshots") as batch_op:
        batch_op.add_column(sa.Column("response_from", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("response_to", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("bumpa_orders") as batch_op:
        batch_op.add_column(sa.Column("shipping_status", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("channel", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("origin", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("subtotal_amount", sa.Numeric(precision=24, scale=6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("tax_amount", sa.Numeric(precision=24, scale=6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("shipping_amount", sa.Numeric(precision=24, scale=6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("amount_paid", sa.Numeric(precision=24, scale=6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("amount_due", sa.Numeric(precision=24, scale=6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("created_at_source", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("updated_at_source", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_bumpa_orders_currency_code_length",
            "currency_code IS NULL OR length(currency_code) = 3",
        )
        batch_op.create_index(
            "ix_bumpa_orders_tenant_order_date",
            ["tenant_id", "order_date"],
            unique=False,
        )

    op.create_table(
        "bumpa_order_items",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("bumpa_item_id", sa.String(length=120), nullable=True),
        sa.Column("product_id", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("unit", sa.String(length=80), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=80), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_bumpa_order_items_quantity_nonnegative",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["bumpa_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bumpa_order_items_tenant_order",
        "bumpa_order_items",
        ["tenant_id", "order_id"],
        unique=False,
    )

    with op.batch_alter_table("hermes_profiles") as batch_op:
        # Nullable is intentional during the local-provider transition. The Hermes
        # provisioner must allocate both values before a profile becomes live.
        batch_op.add_column(sa.Column("profile_path", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("api_port", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_hermes_profiles_api_port_range",
            "api_port IS NULL OR api_port BETWEEN 1024 AND 65535",
        )
        batch_op.create_check_constraint(
            "ck_hermes_profiles_live_coordinates",
            "provider != 'hermes' OR (profile_path IS NOT NULL AND api_port IS NOT NULL)",
        )
        batch_op.create_unique_constraint("uq_hermes_profiles_api_port", ["api_port"])

    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.create_check_constraint(
            "ck_agent_messages_channel",
            "channel IN ('web', 'whatsapp', 'system', 'admin')",
        )
        batch_op.create_check_constraint(
            "ck_agent_messages_direction",
            "direction IN ('inbound', 'outbound')",
        )

    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.create_check_constraint(
            "ck_whatsapp_messages_direction",
            "direction IN ('inbound', 'outbound')",
        )

    op.create_table(
        "agent_tool_calls",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("agent_message_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("tool_input", sa.JSON(), nullable=True),
        sa.Column("tool_output", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=80), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'success', 'failed', 'denied')",
            name="ck_agent_tool_calls_status",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_tool_calls_duration_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["agent_message_id"],
            ["agent_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_tool_calls_tenant_message_created",
        "agent_tool_calls",
        ["tenant_id", "agent_message_id", "created_at"],
        unique=False,
    )

    with op.batch_alter_table("research_reports") as batch_op:
        batch_op.alter_column(
            "generated_by",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_research_reports_status",
            "status IN ('queued', 'running', 'success', 'failed')",
        )

    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.create_check_constraint(
            "ck_mcp_connections_provider",
            "provider IN ('google_drive', 'google_sheets', 'gmail', 'calendar', 'meta_ads')",
        )

    op.create_table(
        "mcp_tool_permissions",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("mcp_connection_id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=80), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "permission IN ('deny', 'read', 'write_with_confirmation')",
            name="ck_mcp_tool_permissions_permission",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["mcp_connection_id"],
            ["mcp_connections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mcp_connection_id",
            "tool_name",
            name="uq_mcp_tool_permissions_connection_tool",
        ),
    )
    op.create_index(
        "ix_mcp_tool_permissions_tenant_connection",
        "mcp_tool_permissions",
        ["tenant_id", "mcp_connection_id"],
        unique=False,
    )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("ip_address", _ip_address_type(), nullable=True))
        batch_op.add_column(sa.Column("user_agent", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_audit_logs_tenant_created",
            ["tenant_id", "created_at"],
            unique=False,
        )

    with op.batch_alter_table("system_errors") as batch_op:
        batch_op.add_column(sa.Column("stack", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_system_errors_service_severity_created",
            ["service", "severity", "created_at"],
            unique=False,
        )

    _enable_rls()


def downgrade() -> None:
    with op.batch_alter_table("system_errors") as batch_op:
        batch_op.drop_index("ix_system_errors_service_severity_created")
        batch_op.drop_column("stack")

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index("ix_audit_logs_tenant_created")
        batch_op.drop_column("user_agent")
        batch_op.drop_column("ip_address")

    op.drop_index(
        "ix_mcp_tool_permissions_tenant_connection",
        table_name="mcp_tool_permissions",
    )
    op.drop_table("mcp_tool_permissions")

    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.drop_constraint("ck_mcp_connections_provider", type_="check")

    with op.batch_alter_table("research_reports") as batch_op:
        batch_op.drop_constraint("ck_research_reports_status", type_="check")
        # This intentionally fails instead of deleting data if a report became
        # orphaned after its generator was deleted while this revision was active.
        batch_op.alter_column(
            "generated_by",
            existing_type=sa.String(length=36),
            nullable=False,
        )

    op.drop_index(
        "ix_agent_tool_calls_tenant_message_created",
        table_name="agent_tool_calls",
    )
    op.drop_table("agent_tool_calls")

    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.drop_constraint("ck_whatsapp_messages_direction", type_="check")

    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_constraint("ck_agent_messages_direction", type_="check")
        batch_op.drop_constraint("ck_agent_messages_channel", type_="check")

    with op.batch_alter_table("hermes_profiles") as batch_op:
        batch_op.drop_constraint("uq_hermes_profiles_api_port", type_="unique")
        batch_op.drop_constraint("ck_hermes_profiles_live_coordinates", type_="check")
        batch_op.drop_constraint("ck_hermes_profiles_api_port_range", type_="check")
        batch_op.drop_column("api_port")
        batch_op.drop_column("profile_path")

    op.drop_index(
        "ix_bumpa_order_items_tenant_order",
        table_name="bumpa_order_items",
    )
    op.drop_table("bumpa_order_items")

    with op.batch_alter_table("bumpa_orders") as batch_op:
        batch_op.drop_index("ix_bumpa_orders_tenant_order_date")
        batch_op.drop_constraint("ck_bumpa_orders_currency_code_length", type_="check")
        batch_op.drop_column("updated_at_source")
        batch_op.drop_column("created_at_source")
        batch_op.drop_column("amount_due")
        batch_op.drop_column("amount_paid")
        batch_op.drop_column("shipping_amount")
        batch_op.drop_column("tax_amount")
        batch_op.drop_column("subtotal_amount")
        batch_op.drop_column("origin")
        batch_op.drop_column("channel")
        batch_op.drop_column("shipping_status")

    with op.batch_alter_table("bumpa_metric_snapshots") as batch_op:
        batch_op.drop_column("response_to")
        batch_op.drop_column("response_from")

    with op.batch_alter_table("bumpa_raw_responses") as batch_op:
        batch_op.drop_constraint("ck_bumpa_raw_responses_http_status", type_="check")
        batch_op.drop_constraint("ck_bumpa_raw_responses_availability", type_="check")

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_bumpa_sync_runs_rate_remaining_within_limit",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_bumpa_sync_runs_rate_remaining_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_bumpa_sync_runs_rate_limit_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint("ck_bumpa_sync_runs_status", type_="check")
        batch_op.drop_column("rate_limit_remaining")
        batch_op.drop_column("rate_limit_limit")

    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.drop_constraint("ck_bumpa_connections_scope_type", type_="check")

    with op.batch_alter_table("tenant_memberships") as batch_op:
        batch_op.drop_constraint("ck_tenant_memberships_role", type_="check")

    with op.batch_alter_table("otp_sessions") as batch_op:
        batch_op.drop_index("ix_otp_sessions_phone_expires")
        batch_op.drop_constraint("ck_otp_sessions_attempts_nonnegative", type_="check")
        batch_op.drop_constraint("ck_otp_sessions_purpose", type_="check")
