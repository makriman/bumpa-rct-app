"""Persist the Bumpa store context and connection-boundary contract.

Revision ID: 0015_bumpa_store_context
Revises: 0014_bumpa_canonical_metrics
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_bumpa_store_context"
down_revision: str | None = "0014_bumpa_canonical_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Server defaults preserve the schema-forward rollback floor for the old
    # application during promotion. Existing rows are then backfilled from the
    # tenant configuration; every writer in this revision persists an explicit
    # Bumpa store context rather than relying on those compatibility defaults.
    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "store_timezone",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("'Africa/Lagos'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "store_currency",
                sa.String(length=3),
                nullable=False,
                server_default=sa.text("'NGN'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "boundary_revision",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "boundary_revision",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

    connections = sa.table(
        "bumpa_connections",
        sa.column("tenant_id", sa.String()),
        sa.column("store_timezone", sa.String()),
        sa.column("store_currency", sa.String()),
    )
    tenants = sa.table(
        "tenants",
        sa.column("id", sa.String()),
        sa.column("timezone", sa.String()),
        sa.column("currency_code", sa.String()),
    )
    bind = op.get_bind()
    bind.execute(
        sa.update(connections).values(
            store_timezone=sa.select(tenants.c.timezone)
            .where(tenants.c.id == connections.c.tenant_id)
            .scalar_subquery(),
            store_currency=sa.select(tenants.c.currency_code)
            .where(tenants.c.id == connections.c.tenant_id)
            .scalar_subquery(),
        )
    )

    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.create_check_constraint(
            "ck_bumpa_connections_store_timezone_length",
            "length(store_timezone) BETWEEN 1 AND 64",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_connections_store_currency",
            "length(store_currency) = 3 AND store_currency = upper(store_currency) "
            "AND substr(store_currency, 1, 1) BETWEEN 'A' AND 'Z' "
            "AND substr(store_currency, 2, 1) BETWEEN 'A' AND 'Z' "
            "AND substr(store_currency, 3, 1) BETWEEN 'A' AND 'Z'",
        )
        batch_op.create_check_constraint(
            "ck_bumpa_connections_boundary_revision_positive",
            "boundary_revision > 0",
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.create_check_constraint(
            "ck_bumpa_sync_runs_boundary_revision_positive",
            "boundary_revision > 0",
        )
        batch_op.create_index(
            "ix_bumpa_sync_runs_connection_boundary_finished",
            ["bumpa_connection_id", "boundary_revision", "finished_at"],
            unique=False,
        )


def downgrade() -> None:
    # Dropping revision state after a material connection replacement would make
    # older retained evidence indistinguishable from the active boundary on a
    # later re-upgrade. Never fabricate that eligibility. A reviewed archival or
    # contraction migration is required once any connection or run has advanced.
    bind = op.get_bind()
    advanced_connections = bind.scalar(
        sa.text("SELECT COUNT(*) FROM bumpa_connections WHERE boundary_revision <> 1")
    )
    advanced_runs = bind.scalar(
        sa.text("SELECT COUNT(*) FROM bumpa_sync_runs WHERE boundary_revision <> 1")
    )
    if advanced_connections or advanced_runs:
        raise RuntimeError(
            "Cannot downgrade after a Bumpa connection boundary has advanced; "
            "retain schema 0015 unless a reviewed migration preserves or archives "
            "the boundary evidence"
        )

    with op.batch_alter_table("bumpa_sync_runs") as batch_op:
        batch_op.drop_index("ix_bumpa_sync_runs_connection_boundary_finished")
        batch_op.drop_constraint("ck_bumpa_sync_runs_boundary_revision_positive", type_="check")
        batch_op.drop_column("boundary_revision")

    with op.batch_alter_table("bumpa_connections") as batch_op:
        batch_op.drop_constraint("ck_bumpa_connections_boundary_revision_positive", type_="check")
        batch_op.drop_constraint("ck_bumpa_connections_store_currency", type_="check")
        batch_op.drop_constraint("ck_bumpa_connections_store_timezone_length", type_="check")
        batch_op.drop_column("store_currency")
        batch_op.drop_column("store_timezone")
        batch_op.drop_column("boundary_revision")
