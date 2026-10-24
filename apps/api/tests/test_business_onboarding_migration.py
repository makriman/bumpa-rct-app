from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import app.core.config as config_module
from alembic import command


def test_business_profile_migration_and_old_writer_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'business-memory.db'}"
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=url, migration_database_url=None),
    )
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0018_agent_capability_audit")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id,slug,name,status,timezone,currency_code,research_consent_status,created_at,updated_at) VALUES ('old','old','Old business','active','Africa/Lagos','NGN','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    with engine.begin() as conn:
        assert "merchant_business_profiles" in inspect(conn).get_table_names()
        assert conn.scalar(text("SELECT count(*) FROM tenants WHERE id='old'")) == 1
        conn.execute(
            text(
                "INSERT INTO merchant_business_profiles (id,tenant_id,created_at,updated_at) VALUES ('profile','old',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        assert conn.scalar(text("SELECT state FROM merchant_business_profiles")) == "{}"
    command.downgrade(config, "0018_agent_capability_audit")
    assert "merchant_business_profiles" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "merchant_business_profiles" in inspect(engine).get_table_names()
    engine.dispose()
