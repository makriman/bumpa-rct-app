from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.postgresql.base import PGDialect

import app.core.config as config_module
from alembic import command
from app.db.base import Base
from app.db.models import AuditLog, ResearchReport

API_ROOT = Path(__file__).parents[1]
MIGRATION_PATH = API_ROOT / "alembic" / "versions" / "0002_schema_completeness.py"


def _alembic_config() -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return config


def test_model_metadata_covers_planned_schema() -> None:
    tables = Base.metadata.tables
    assert {
        "bumpa_order_items",
        "agent_tool_calls",
        "mcp_tool_permissions",
    } <= set(tables)

    order_columns = tables["bumpa_orders"].c
    assert {
        "shipping_status",
        "channel",
        "origin",
        "subtotal_amount",
        "tax_amount",
        "shipping_amount",
        "amount_paid",
        "amount_due",
        "created_at_source",
        "updated_at_source",
    } <= set(order_columns.keys())

    profile_columns = tables["hermes_profiles"].c
    assert {"profile_path", "api_port"} <= set(profile_columns.keys())
    profile_constraints = {constraint.name for constraint in tables["hermes_profiles"].constraints}
    assert "ck_hermes_profiles_live_coordinates" in profile_constraints
    postgres_dialect = PGDialect()  # type: ignore[no-untyped-call]
    assert str(AuditLog.__table__.c.ip_address.type.dialect_impl(postgres_dialect)) == "INET"
    assert ResearchReport.__table__.c.generated_by.nullable is True

    permission_constraints = {
        constraint.name for constraint in tables["mcp_tool_permissions"].constraints
    }
    assert "ck_mcp_tool_permissions_permission" in permission_constraints
    assert "uq_mcp_tool_permissions_connection_tool" in permission_constraints
    connection_constraints = {
        constraint.name for constraint in tables["mcp_connections"].constraints
    }
    assert "ck_mcp_connections_status" in connection_constraints
    assert "uq_mcp_connections_tenant_provider" in connection_constraints

    for table_name in ("bumpa_order_items", "agent_tool_calls", "mcp_tool_permissions"):
        table = tables[table_name]
        assert table.c.tenant_id.nullable is False
        tenant_fk = next(iter(table.c.tenant_id.foreign_keys))
        assert tenant_fk.target_fullname == "tenants.id"
        assert tenant_fk.ondelete == "CASCADE"


def test_schema_migration_round_trip_and_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "schema.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=database_url,
            migration_database_url=None,
        ),
    )
    config = _alembic_config()
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    try:
        assert {
            "bumpa_order_items",
            "agent_tool_calls",
            "mcp_tool_permissions",
        } <= set(inspector.get_table_names())
        report_columns = {
            column["name"]: column for column in inspector.get_columns("research_reports")
        }
        assert report_columns["generated_by"]["nullable"] is True
        assert {column["name"] for column in inspector.get_columns("audit_logs")} >= {
            "ip_address",
            "user_agent",
        }
        assert {column["name"] for column in inspector.get_columns("system_errors")} >= {"stack"}
        assert {
            constraint["name"] for constraint in inspector.get_check_constraints("hermes_profiles")
        } >= {
            "ck_hermes_profiles_api_port_range",
            "ck_hermes_profiles_live_coordinates",
        }
        assert {
            constraint["name"] for constraint in inspector.get_check_constraints("agent_tool_calls")
        } >= {
            "ck_agent_tool_calls_status",
            "ck_agent_tool_calls_duration_nonnegative",
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("mcp_tool_permissions")
        } >= {"ck_mcp_tool_permissions_permission"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("mcp_tool_permissions")
        } >= {"uq_mcp_tool_permissions_connection_tool"}
        assert {
            constraint["name"] for constraint in inspector.get_check_constraints("mcp_connections")
        } >= {"ck_mcp_connections_status"}
        assert {
            constraint["name"] for constraint in inspector.get_unique_constraints("mcp_connections")
        } >= {"uq_mcp_connections_tenant_provider"}
    finally:
        engine.dispose()

    command.downgrade(config, "0001_initial")
    downgraded_engine = create_engine(database_url)
    try:
        downgraded = inspect(downgraded_engine)
        assert "agent_tool_calls" not in downgraded.get_table_names()
        report_columns = {
            column["name"]: column for column in downgraded.get_columns("research_reports")
        }
        assert report_columns["generated_by"]["nullable"] is False
    finally:
        downgraded_engine.dispose()

    command.upgrade(config, "head")


def test_new_tenant_tables_enable_and_force_postgres_rls() -> None:
    source = MIGRATION_PATH.read_text()
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('app.current_tenant_id', true)" in source
    for table_name in ("bumpa_order_items", "agent_tool_calls", "mcp_tool_permissions"):
        assert f'"{table_name}"' in source
