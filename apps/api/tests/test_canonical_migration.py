from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from sqlalchemy import create_engine, text

import app.core.config as config_module
from alembic import command


def test_additive_migration_quarantines_without_reassigning_or_erasing_history(
    tmp_path, monkeypatch
):
    url = f"sqlite:///{tmp_path / 'quarantine.db'}"
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=url, migration_database_url=None),
    )
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0019_business_onboarding")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO tenants (id,slug,name,status,timezone,currency_code,research_consent_status,created_at,updated_at) VALUES ('t','t','Synthetic','active','UTC','NGN','granted',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO users (id,primary_phone_e164,status,created_at,updated_at) VALUES ('u','+15555550100','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO conversations (id,tenant_id,user_id,channel,status,provider_session_id,created_at,updated_at) VALUES ('c','t','u','whatsapp','open','disputed-native',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO agent_messages (id,tenant_id,user_id,conversation_id,channel,direction,content,created_at) VALUES ('m','t','u','c','whatsapp','inbound','Preserved disputed evidence',CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as db:
        row = db.execute(
            text(
                "SELECT context_generation,receiver_waba_id,receiver_phone_id,environment_lane,provider_session_id FROM conversations WHERE id='c'"
            )
        ).one()
        assert tuple(row) == (0, "", "", "", "disputed-native")
        assert (
            db.scalar(text("SELECT content FROM agent_messages WHERE id='m'"))
            == "Preserved disputed evidence"
        )
        assert db.scalar(text("SELECT count(*) FROM agent_execution_runs")) == 0
    command.downgrade(config, "0019_business_onboarding")
    command.upgrade(config, "head")
    engine.dispose()
