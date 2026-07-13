from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.core.config as config_module
from alembic import command
from app.db.models import BumpaSyncRun

API_ROOT = Path(__file__).parents[1]


def _alembic_config() -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return config


def _completion_default(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        column = next(
            column
            for column in inspect(engine).get_columns("bumpa_sync_runs")
            if column["name"] == "completion_quality"
        )
        default = column["default"]
        return str(default) if default is not None else None
    finally:
        engine.dispose()


def _insert_parents(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, "
                    "research_consent_status, id, created_at, updated_at) "
                    "VALUES (:slug, :name, 'active', 'UTC', 'NGN', "
                    "'unknown', :id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"slug": "legacy-sync-writer", "name": "Legacy Sync Writer", "id": "tenant"},
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_connections "
                    "(tenant_id, encrypted_api_key, scope_type, scope_id, provider, status, "
                    "id, created_at, updated_at) "
                    "VALUES ('tenant', 'encrypted', 'business_id', 'legacy-business', "
                    "'bumpa', 'active', 'connection', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()


def _legacy_insert_sql() -> str:
    # This is exactly the pre-0006 column set: no completion-evidence column is
    # named, so compatibility must come exclusively from the database default.
    return (
        "INSERT INTO bumpa_sync_runs "
        "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
        "started_at, error, dataset_results, id) "
        "VALUES ('tenant', 'connection', 'running', :requested_from, :requested_to, "
        "CURRENT_TIMESTAMP, NULL, '{}', :id)"
    )


def test_migration_keeps_pre_0006_writer_compatible_without_weakening_typed_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-writer.db'}"
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url, migration_database_url=None),
    )
    config = _alembic_config()

    command.upgrade(config, "0005_platform_roles")
    _insert_parents(database_url)

    engine = create_engine(database_url)
    terminal_states = {
        "legacy-success": ("success", None),
        "legacy-partial": ("partial", None),
        "legacy-failed": ("failed", "legacy provider failure"),
    }
    try:
        # The rollback hazard includes work already in flight when 0006 lands.
        # Seed all three old-writer terminal paths before the new columns exist.
        with engine.begin() as connection:
            for run_id in terminal_states:
                connection.execute(
                    text(_legacy_insert_sql()),
                    {
                        "id": run_id,
                        "requested_from": date(2026, 7, 1),
                        "requested_to": date(2026, 7, 12),
                    },
                )
    finally:
        engine.dispose()

    command.upgrade(config, "0006_sync_completion")
    assert _completion_default(database_url) in {"'pending'", "pending"}
    pending_engine = create_engine(database_url)
    try:
        with pending_engine.connect() as connection:
            qualities = set(
                connection.scalars(text("SELECT completion_quality FROM bumpa_sync_runs"))
            )
        assert qualities == {"pending"}
    finally:
        pending_engine.dispose()

    command.upgrade(config, "head")
    assert _completion_default(database_url) in {"'legacy'", "legacy"}

    engine = create_engine(database_url)
    try:
        for run_id, (status, error) in terminal_states.items():
            with engine.begin() as connection:
                migrated = connection.execute(
                    text(
                        "SELECT completion_quality, partial_reason, orders_availability, "
                        "orders_count FROM bumpa_sync_runs WHERE id = :id"
                    ),
                    {"id": run_id},
                ).one()
                assert tuple(migrated) == ("legacy", None, None, None)
                connection.execute(
                    text(
                        "UPDATE bumpa_sync_runs SET status = :status, error = :error, "
                        "finished_at = CURRENT_TIMESTAMP WHERE id = :id"
                    ),
                    {"id": run_id, "status": status, "error": error},
                )

        with engine.connect() as connection:
            observed = dict(
                connection.execute(text("SELECT id, completion_quality FROM bumpa_sync_runs")).all()
            )
        assert observed == {run_id: "legacy" for run_id in terminal_states}

        invalid_states = {
            # A current writer cannot leave pending evidence on a terminal run.
            "typed-terminal-pending": {
                "status": "success",
                "quality": "pending",
                "reason": None,
                "orders_availability": None,
                "orders_count": None,
                "error": None,
            },
            # Calling a row legacy cannot smuggle in partially populated evidence.
            "legacy-with-evidence": {
                "status": "success",
                "quality": "legacy",
                "reason": None,
                "orders_availability": "available",
                "orders_count": None,
                "error": None,
            },
            # A failed legacy writer must preserve its old-schema error evidence.
            "legacy-failure-without-error": {
                "status": "failed",
                "quality": "legacy",
                "reason": None,
                "orders_availability": None,
                "orders_count": None,
                "error": None,
            },
        }
        for run_id, state_values in invalid_states.items():
            statement = text(
                "INSERT INTO bumpa_sync_runs "
                "(tenant_id, bumpa_connection_id, status, completion_quality, "
                "partial_reason, orders_availability, orders_count, error, requested_from, "
                "requested_to, dataset_results, id) VALUES "
                "('tenant', 'connection', :status, :quality, :reason, "
                ":orders_availability, :orders_count, :error, "
                "'2026-07-01', '2026-07-12', '{}', :id)"
            )
            with engine.begin() as connection, pytest.raises(IntegrityError):
                connection.execute(statement, {"id": run_id, **state_values})

        # SQLAlchemy's current model applies its client default explicitly; it
        # never depends on the server-only compatibility discriminator.
        with Session(engine) as session:
            current = BumpaSyncRun(
                tenant_id="tenant",
                bumpa_connection_id="connection",
                status="running",
                requested_from=date(2026, 7, 1),
                requested_to=date(2026, 7, 12),
            )
            session.add(current)
            session.flush()
            assert current.completion_quality == "pending"
            stored_quality = session.scalar(
                select(BumpaSyncRun.completion_quality).where(BumpaSyncRun.id == current.id)
            )
            assert stored_quality == "pending"
            session.rollback()
    finally:
        engine.dispose()

    # No migration may invent missing evidence merely to satisfy the previous
    # constraint. A dirty downgrade refuses before changing schema or data.
    with pytest.raises(RuntimeError, match="legacy Bumpa sync runs exist"):
        command.downgrade(config, "0006_sync_completion")
    assert _completion_default(database_url) in {"'legacy'", "legacy"}

    cleanup_engine = create_engine(database_url)
    try:
        with cleanup_engine.begin() as connection:
            connection.execute(text("DELETE FROM bumpa_sync_runs"))
    finally:
        cleanup_engine.dispose()

    command.downgrade(config, "0006_sync_completion")
    assert _completion_default(database_url) in {"'pending'", "pending"}
    command.upgrade(config, "head")
    assert _completion_default(database_url) in {"'legacy'", "legacy"}
