from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import app.core.config as config_module
from alembic import command
from scripts.verify_postgres_rls import _revision_in_lineage

API_ROOT = Path(__file__).parents[1]


def _config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url, migration_database_url=None),
    )
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return config


def test_bumpa_raw_verifier_accepts_forward_migration_ancestry() -> None:
    assert _revision_in_lineage(
        "0010_mcp_lifecycle",
        "0008_bumpa_dataset_failures",
    )
    assert not _revision_in_lineage(
        "0007_legacy_sync_writer",
        "0008_bumpa_dataset_failures",
    )


def test_bumpa_dataset_failure_migration_preserves_old_writer_and_typed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'bumpa-failure-evidence.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "0007_legacy_sync_writer")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('raw-compat', 'Raw compatibility', 'active', 'UTC', 'NGN', "
                    "'unknown', 'tenant', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_connections "
                    "(tenant_id, encrypted_api_key, scope_type, scope_id, provider, status, "
                    "id, created_at, updated_at) VALUES "
                    "('tenant', 'encrypted', 'business_id', 'business', 'bumpa', 'active', "
                    "'connection', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_sync_runs "
                    "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
                    "started_at, error, dataset_results, id) VALUES "
                    "('tenant', 'connection', 'running', '2026-07-01', '2026-07-12', "
                    "CURRENT_TIMESTAMP, NULL, '{}', 'run')"
                )
            )
            # Exact pre-0008 writer shape: failure_kind does not exist yet.
            connection.execute(
                text(
                    "INSERT INTO bumpa_raw_responses "
                    "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
                    "error_message, payload, pii_level, id, created_at) VALUES "
                    "('tenant', 'run', 'sales', 'overview', 200, 'available', NULL, '{}', "
                    "'sensitive', 'old-http-row', CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        columns = {
            column["name"]: column for column in inspect(engine).get_columns("bumpa_raw_responses")
        }
        assert columns["http_status"]["nullable"] is True
        assert columns["failure_kind"]["nullable"] is True
        checks = {
            check["name"] for check in inspect(engine).get_check_constraints("bumpa_raw_responses")
        }
        assert {
            "ck_bumpa_raw_responses_http_status",
            "ck_bumpa_raw_responses_failure_kind",
            "ck_bumpa_raw_responses_status_evidence",
            "ck_bumpa_raw_responses_failure_availability",
        } <= checks

        insert_sql = text(
            "INSERT INTO bumpa_raw_responses "
            "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
            "failure_kind, error_message, payload, pii_level, id, created_at) VALUES "
            "('tenant', 'run', 'products', 'overview', :status, :availability, :kind, "
            "'sanitized', '{}', 'sensitive', :id, CURRENT_TIMESTAMP)"
        )
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT failure_kind FROM bumpa_raw_responses WHERE id = 'old-http-row'")
                )
                is None
            )
            # A pre-0008 writer that still omits the new nullable column remains valid.
            connection.execute(
                text(
                    "INSERT INTO bumpa_raw_responses "
                    "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
                    "error_message, payload, pii_level, id, created_at) VALUES "
                    "('tenant', 'run', 'sales', 'total_sales', 200, 'available', NULL, '{}', "
                    "'sensitive', 'old-writer-after-head', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                insert_sql,
                {"status": None, "availability": "error", "kind": "timeout", "id": "timeout"},
            )
            connection.execute(
                insert_sql,
                {
                    "status": None,
                    "availability": "error",
                    "kind": "transport",
                    "id": "transport",
                },
            )
            connection.execute(
                insert_sql,
                {
                    "status": 504,
                    "availability": "error",
                    "kind": "upstream_http",
                    "id": "gateway",
                },
            )

        invalid = [
            {"status": None, "availability": "error", "kind": None, "id": "null-null"},
            {"status": None, "availability": "error", "kind": "upstream_http", "id": "no-http"},
            {
                "status": 504,
                "availability": "available",
                "kind": "upstream_http",
                "id": "bad-availability",
            },
        ]
        for values in invalid:
            with engine.begin() as connection, pytest.raises(IntegrityError):
                connection.execute(insert_sql, values)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="transport failures without HTTP responses"):
        command.downgrade(config, "0007_legacy_sync_writer")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM bumpa_raw_responses WHERE http_status IS NULL"))
    finally:
        engine.dispose()

    command.downgrade(config, "0007_legacy_sync_writer")
    downgraded = create_engine(database_url)
    try:
        columns = {
            column["name"] for column in inspect(downgraded).get_columns("bumpa_raw_responses")
        }
        assert "failure_kind" not in columns
        with downgraded.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM bumpa_raw_responses WHERE http_status = 504")
                )
                == 1
            )
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
