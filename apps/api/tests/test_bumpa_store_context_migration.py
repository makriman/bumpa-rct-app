from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import app.core.config as config_module
from alembic import command
from app.core.store_context import validate_store_currency, validate_store_timezone

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


def test_store_context_validation_is_explicit_and_normalized() -> None:
    assert validate_store_timezone("Africa/Lagos") == "Africa/Lagos"
    assert validate_store_timezone("Europe/London") == "Europe/London"
    assert validate_store_currency("ngn") == "NGN"
    with pytest.raises(ValueError, match="IANA"):
        validate_store_timezone("Not/A_Real_Zone")
    with pytest.raises(ValueError, match="normalized"):
        validate_store_timezone(" Africa/Lagos")
    with pytest.raises(ValueError, match="three-letter"):
        validate_store_currency("12!")


def test_store_context_migration_backfills_supports_rollback_writer_and_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'bumpa-store-context.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "0014_bumpa_canonical_metrics")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('london-store', 'London Store', 'active', 'Europe/London', 'GBP', "
                    "'pending', 'tenant-london', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_connections "
                    "(tenant_id, encrypted_api_key, scope_type, scope_id, provider, status, "
                    "id, created_at, updated_at) VALUES "
                    "('tenant-london', 'encrypted', 'business_id', 'business-london', "
                    "'bumpa', 'active', 'connection-london', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        columns = {
            column["name"]: column for column in inspect(engine).get_columns("bumpa_connections")
        }
        assert columns["store_timezone"]["nullable"] is False
        assert columns["store_currency"]["nullable"] is False
        assert columns["boundary_revision"]["nullable"] is False
        run_columns = {
            column["name"]: column for column in inspect(engine).get_columns("bumpa_sync_runs")
        }
        assert run_columns["boundary_revision"]["nullable"] is False
        checks = {
            check["name"] for check in inspect(engine).get_check_constraints("bumpa_connections")
        }
        assert {
            "ck_bumpa_connections_store_timezone_length",
            "ck_bumpa_connections_store_currency",
            "ck_bumpa_connections_boundary_revision_positive",
        } <= checks
        assert "ck_bumpa_sync_runs_boundary_revision_positive" in {
            check["name"] for check in inspect(engine).get_check_constraints("bumpa_sync_runs")
        }
        assert "ix_bumpa_sync_runs_connection_boundary_finished" in {
            index["name"] for index in inspect(engine).get_indexes("bumpa_sync_runs")
        }
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT store_timezone, store_currency, boundary_revision "
                    "FROM bumpa_connections "
                    "WHERE id = 'connection-london'"
                )
            ).one() == ("Europe/London", "GBP", 1)
            connection.execute(
                text(
                    "INSERT INTO bumpa_sync_runs "
                    "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
                    "dataset_results, id) VALUES "
                    "('tenant-london', 'connection-london', 'running', '2026-01-01', "
                    "'2026-01-01', '{}', 'rollback-run')"
                )
            )
            assert (
                connection.execute(
                    text("SELECT boundary_revision FROM bumpa_sync_runs WHERE id = 'rollback-run'")
                ).scalar_one()
                == 1
            )
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('rollback-writer', 'Rollback Writer', 'active', 'UTC', 'USD', "
                    "'pending', 'tenant-rollback', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # The previous application can still write during a schema-forward
            # rollback. Defaults are only this temporary hybrid-release floor.
            connection.execute(
                text(
                    "INSERT INTO bumpa_connections "
                    "(tenant_id, encrypted_api_key, scope_type, scope_id, provider, status, "
                    "id, created_at, updated_at) VALUES "
                    "('tenant-rollback', 'encrypted', 'business_id', 'business-rollback', "
                    "'bumpa', 'active', 'connection-rollback', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            assert connection.execute(
                text(
                    "SELECT store_timezone, store_currency FROM bumpa_connections "
                    "WHERE id = 'connection-rollback'"
                )
            ).one() == ("Africa/Lagos", "NGN")
        for invalid_currency in ("usd", "123", "U$D"):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE bumpa_connections SET store_currency = :currency "
                            "WHERE id = 'connection-london'"
                        ),
                        {"currency": invalid_currency},
                    )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE bumpa_connections SET store_timezone = '' "
                        "WHERE id = 'connection-london'"
                    )
                )
        for statement, row_id in (
            (
                "UPDATE bumpa_connections SET boundary_revision = 0 WHERE id = :row_id",
                "connection-london",
            ),
            (
                "UPDATE bumpa_sync_runs SET boundary_revision = 0 WHERE id = :row_id",
                "rollback-run",
            ),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(statement),
                        {"row_id": row_id},
                    )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE bumpa_connections SET boundary_revision = 2 "
                    "WHERE id = 'connection-london'"
                )
            )
        with pytest.raises(RuntimeError, match="boundary has advanced"):
            command.downgrade(config, "0014_bumpa_canonical_metrics")
        assert "boundary_revision" in {
            column["name"] for column in inspect(engine).get_columns("bumpa_connections")
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE bumpa_connections SET boundary_revision = 1 "
                    "WHERE id = 'connection-london'"
                )
            )
            connection.execute(
                text("UPDATE bumpa_sync_runs SET boundary_revision = 2 WHERE id = 'rollback-run'")
            )
        with pytest.raises(RuntimeError, match="boundary has advanced"):
            command.downgrade(config, "0014_bumpa_canonical_metrics")
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE bumpa_sync_runs SET boundary_revision = 1 WHERE id = 'rollback-run'")
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0014_bumpa_canonical_metrics")
    downgraded = create_engine(database_url)
    try:
        assert not {"store_timezone", "store_currency", "boundary_revision"} & {
            column["name"] for column in inspect(downgraded).get_columns("bumpa_connections")
        }
        assert "boundary_revision" not in {
            column["name"] for column in inspect(downgraded).get_columns("bumpa_sync_runs")
        }
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    upgraded = create_engine(database_url)
    try:
        with upgraded.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT store_timezone, store_currency, boundary_revision "
                    "FROM bumpa_connections "
                    "WHERE id = 'connection-london'"
                )
            ).one() == ("Europe/London", "GBP", 1)
            assert connection.execute(
                text(
                    "SELECT store_timezone, store_currency FROM bumpa_connections "
                    "WHERE id = 'connection-rollback'"
                )
            ).one() == ("UTC", "USD")
    finally:
        upgraded.dispose()
