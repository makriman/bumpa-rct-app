from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.core.config as config_module
from alembic import command

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


def test_retention_indexes_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'operational-retention.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "ix_audit_logs_created_at" in {
            index["name"] for index in inspector.get_indexes("audit_logs")
        }
        assert "ix_system_errors_created_at" in {
            index["name"] for index in inspector.get_indexes("system_errors")
        }
    finally:
        engine.dispose()

    command.downgrade(config, "0011_tenant_onboarding")
    downgraded = create_engine(database_url)
    try:
        inspector = inspect(downgraded)
        assert "ix_audit_logs_created_at" not in {
            index["name"] for index in inspector.get_indexes("audit_logs")
        }
        assert "ix_system_errors_created_at" not in {
            index["name"] for index in inspector.get_indexes("system_errors")
        }
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
