from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import app.core.config as config_module
from alembic import command
from app.db.base import Base

API_ROOT = Path(__file__).parents[1]
MIGRATION_PATH = API_ROOT / "alembic" / "versions" / "0011_tenant_onboarding.py"


def _config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url, migration_database_url=None),
    )
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return config


def test_onboarding_model_is_explicit_secret_free_and_constrained() -> None:
    table = Base.metadata.tables["tenant_onboardings"]
    assert {
        "owner_idempotency_key_hash",
        "owner_fingerprint",
        "phone_idempotency_key_hash",
        "phone_fingerprint",
        "bumpa_idempotency_key_hash",
        "bumpa_fingerprint",
        "initial_sync_idempotency_key_hash",
        "initial_sync_fingerprint",
        "initial_sync_accept_idempotency_key_hash",
        "initial_sync_accept_fingerprint",
        "hermes_idempotency_key_hash",
        "hermes_fingerprint",
        "complete_idempotency_key_hash",
        "complete_fingerprint",
        "owner_membership_id",
        "sync_attempt",
    } <= set(table.c.keys())
    assert "step_idempotency_key_hashes" not in table.c
    assert "step_fingerprints" not in table.c
    assert not any(
        unsafe in column.name
        for column in table.c
        for unsafe in ("phone_e164", "api_key", "secret", "credential")
    )
    assert "ck_tenants_status" in {
        constraint.name for constraint in Base.metadata.tables["tenants"].constraints
    }


def test_onboarding_migration_is_in_head_lineage_and_has_forced_tenant_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config("sqlite://", monkeypatch)
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == "0012_operational_retention"
    assert scripts.get_revision("0012_operational_retention").down_revision == (
        "0011_tenant_onboarding"
    )
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'ALTER TABLE "tenant_onboardings" ENABLE ROW LEVEL SECURITY' in source
    assert 'ALTER TABLE "tenant_onboardings" FORCE ROW LEVEL SECURITY' in source
    assert 'CREATE POLICY tenant_isolation ON "tenant_onboardings"' in source
    assert "current_setting('app.current_tenant_id', true)" in source


def test_postgres_rls_fixture_covers_two_onboarding_sagas() -> None:
    from scripts.verify_postgres_rls import _fixture_pair, _model_table

    rows, ids_by_table, _, _ = _fixture_pair("a1b2c3d4e5f6")
    onboarding_rows = [row for row in rows if _model_table(row).name == "tenant_onboardings"]
    assert len(onboarding_rows) == 2
    assert ids_by_table["tenant_onboardings"] == [
        "onboarding-a-a1b2c3d4e5f6",
        "onboarding-b-a1b2c3d4e5f6",
    ]


def test_onboarding_migration_constraints_and_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'onboarding.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "tenant_onboardings" in inspector.get_table_names()
        columns = {column["name"]: column for column in inspector.get_columns("tenant_onboardings")}
        assert columns["tenant_id"]["nullable"] is False
        assert columns["start_idempotency_key_hash"]["nullable"] is False
        assert columns["owner_idempotency_key_hash"]["nullable"] is True
        assert columns["sync_attempt"]["nullable"] is False
        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("tenant_onboardings")
        }
        assert {
            "ck_tenant_onboardings_status",
            "ck_tenant_onboardings_current_step",
            "ck_tenant_onboardings_revision_nonnegative",
            "ck_tenant_onboardings_sync_attempt_nonnegative",
            "ck_tenant_onboardings_completion_state",
        } <= checks
        assert "ck_tenants_status" in {
            constraint["name"] for constraint in inspector.get_check_constraints("tenants")
        }
        uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("tenant_onboardings")
        }
        assert {
            "uq_tenant_onboardings_tenant_id",
            "uq_tenant_onboardings_start_idempotency_key_hash",
        } <= uniques
        indexes = {index["name"] for index in inspector.get_indexes("tenant_onboardings")}
        assert {
            "ix_tenant_onboardings_tenant_id",
            "ix_tenant_onboardings_status_updated",
            "ix_tenant_onboardings_step_updated",
        } <= indexes

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('onboarding', 'Onboarding', 'provisioning', 'UTC', 'NGN', "
                    "'pending', 'tenant-onboarding', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_onboardings "
                    "(tenant_id, status, current_step, revision, sync_attempt, "
                    "start_idempotency_key_hash, start_fingerprint, created_at, updated_at, id) "
                    "VALUES ('tenant-onboarding', 'in_progress', 'owner', 0, 0, :key_hash, "
                    ":fingerprint, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'onboarding')"
                ),
                {"key_hash": "a" * 64, "fingerprint": "b" * 64},
            )
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "UPDATE tenant_onboardings SET status = 'completed', "
                    "current_step = 'review' WHERE id = 'onboarding'"
                )
            )
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE tenants SET status = 'invalid' WHERE id = 'tenant-onboarding'")
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0010_mcp_lifecycle")
    downgraded = create_engine(database_url)
    try:
        inspector = inspect(downgraded)
        assert "tenant_onboardings" not in inspector.get_table_names()
        assert "ck_tenants_status" not in {
            constraint["name"] for constraint in inspector.get_check_constraints("tenants")
        }
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")


def test_onboarding_migration_rejects_invalid_legacy_tenant_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'invalid-tenant-status.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "0010_mcp_lifecycle")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('legacy-invalid', 'Legacy invalid', 'mystery', 'UTC', 'NGN', "
                    "'pending', 'legacy-invalid', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(RuntimeError, match="invalid tenant status"):
            command.upgrade(config, "head")
    finally:
        engine.dispose()
