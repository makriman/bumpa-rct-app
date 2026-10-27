from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import app.core.config as config_module
from alembic import command

HEAD = "0021_consultant_boundaries"
PREVIOUS = "0020_canonical_run_boundaries"


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'consultant.db'}"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=url, migration_database_url=None),
    )
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, HEAD)
    engine = create_engine(url)
    yield config, engine
    engine.dispose()


def seed_legacy_rows(engine):
    statements = (
        "INSERT INTO tenants (id,slug,name,status,timezone,currency_code,research_consent_status,created_at,updated_at) VALUES ('t','t','Synthetic','active','UTC','NGN','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO users (id,primary_phone_e164,status,created_at,updated_at) VALUES ('u','+15555550100','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO phone_identities (id,tenant_id,user_id,phone_e164,status,opt_out,created_at,updated_at) VALUES ('p','t','u','+15555550100','approved',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO conversations (id,tenant_id,user_id,channel,status,created_at,updated_at) VALUES ('c','t','u','web','open',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO agent_messages (id,tenant_id,user_id,conversation_id,channel,direction,content,content_parts,media_metadata,created_at) VALUES ('m','t','u','c','web','inbound','Retained legacy evidence','[]','{}',CURRENT_TIMESTAMP)",
        "INSERT INTO agent_execution_runs (id,tenant_id,user_id,conversation_id,initiating_message_id,status,expires_at,created_at,updated_at) VALUES ('r','t','u','c','m','completed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO pending_agent_actions (id,tenant_id,user_id,conversation_id,tool_name,target_summary,action_input,status,idempotency_key,confirmation_token_hash,expires_at,created_at,updated_at) VALUES ('a','t','u','c','synthetic','Synthetic','{}','pending','synthetic-action','synthetic-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO provider_webhook_events (id,provider,external_event_id,signature_valid,payload,processing_status,attempts,created_at) VALUES ('w','whatsapp','synthetic',1,'{}','received',0,CURRENT_TIMESTAMP)",
        "INSERT INTO async_jobs (id,queue_name,kind,payload,status,idempotency_key,attempts,max_attempts,available_at,created_at,updated_at) VALUES ('j','default','system.noop','{}','pending','synthetic-job',0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
    )
    with engine.begin() as db:
        for statement in statements:
            db.execute(text(statement))


def assert_head_retained(engine):
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
    assert "ingress_streams" in inspect(engine).get_table_names()
    assert "reconcile_epoch" in {
        column["name"] for column in inspect(engine).get_columns("agent_execution_runs")
    }


def test_unused_local_downgrade_reverses_schema_and_reupgrades(migrated):
    config, engine = migrated
    command.downgrade(config, PREVIOUS)
    inspector = inspect(engine)
    assert not {"ingress_streams", "scoped_agent_memories", "proactive_subscriptions"}.intersection(
        inspector.get_table_names()
    )
    assert "claim_epoch" not in {column["name"] for column in inspector.get_columns("async_jobs")}
    assert "initiating_message_id" not in {
        column["name"] for column in inspector.get_columns("agent_messages")
    }
    assert "request_snapshots_encrypted" not in {
        column["name"] for column in inspector.get_columns("agent_execution_runs")
    }
    command.upgrade(config, HEAD)
    assert_head_retained(engine)


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_nonlocal_downgrade_refuses_before_schema_changes(migrated, monkeypatch, environment):
    config, engine = migrated
    monkeypatch.setenv("APP_ENV", environment)
    with pytest.raises(RuntimeError, match="Production keeps|Only unused local"):
        command.downgrade(config, PREVIOUS)
    assert_head_retained(engine)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ingress_streams (scope_key,next_sequence,created_at,updated_at) VALUES ('scope',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO scoped_agent_memories (id,tenant_id,owner_user_id,scope,memory_key,content,revision,status,source_kind,source_message_id,source_run_id,created_at,updated_at) VALUES ('memory','t','u','private','synthetic','Retain',1,'deleted','member_statement','m','r',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        "INSERT INTO proactive_subscriptions (id,tenant_id,user_id,phone_identity_id,cadence,status,timezone,local_hour,weekday,revision,created_at,updated_at) VALUES ('subscription','t','u','p','weekly','paused','UTC',8,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
    ],
)
def test_any_new_table_row_blocks_downgrade_even_paused_or_deleted(migrated, statement):
    config, engine = migrated
    seed_legacy_rows(engine)
    with engine.begin() as db:
        db.execute(text(statement))
    with pytest.raises(RuntimeError, match="new feature data exists"):
        command.downgrade(config, PREVIOUS)
    assert_head_retained(engine)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE agent_execution_runs SET request_snapshots_encrypted='retained'",
        "UPDATE agent_execution_runs SET reconcile_epoch=1",
        "UPDATE agent_execution_runs SET output_message_id='m'",
        "UPDATE agent_execution_runs SET job_id='j'",
        "UPDATE agent_execution_runs SET job_claim_epoch=0",
        "UPDATE agent_execution_runs SET job_stale_lock_seconds=300",
        "UPDATE agent_execution_runs SET status='unknown'",
        "UPDATE agent_execution_runs SET status='reconciling'",
        "UPDATE agent_execution_runs SET delivery_effect_keys='[\"effect\"]'",
        "UPDATE agent_messages SET initiating_message_id='m'",
        "UPDATE pending_agent_actions SET authorization_revision='revision'",
        'UPDATE pending_agent_actions SET approval_binding=\'{"source":"retained"}\'',
        "UPDATE provider_webhook_events SET ordering_scope='scope'",
        "UPDATE provider_webhook_events SET arrival_sequence=1",
        "UPDATE async_jobs SET claim_epoch=1",
        'UPDATE agent_messages SET media_metadata=\'{"event_kind":"reply"}\'',
        'UPDATE agent_messages SET media_metadata=\'{"voice_playback_state":"generated"}\'',
        "UPDATE agent_messages SET media_metadata='{\"recovered\": true}'",
    ],
)
def test_used_boundary_or_recovery_metadata_blocks_downgrade(migrated, statement):
    config, engine = migrated
    seed_legacy_rows(engine)
    with engine.begin() as db:
        db.execute(text(statement))
    with pytest.raises(RuntimeError, match="boundary metadata is used|recovery metadata exists"):
        command.downgrade(config, PREVIOUS)
    assert_head_retained(engine)


def test_pristine_0021_can_reverse_without_weakening_0020_used_data_guard(migrated):
    config, engine = migrated
    seed_legacy_rows(engine)
    command.downgrade(config, PREVIOUS)
    with engine.connect() as db:
        assert (
            db.scalar(text("SELECT content FROM agent_messages WHERE id='m'"))
            == "Retained legacy evidence"
        )
        assert db.scalar(text("SELECT count(*) FROM agent_execution_runs")) == 1
    with pytest.raises(RuntimeError, match="canonical data exists"):
        command.downgrade(config, "0019_business_onboarding")
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS
