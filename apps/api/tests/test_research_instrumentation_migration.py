from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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


def test_research_instrumentation_migration_backfills_and_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'research-instrumentation.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "0008_bumpa_dataset_failures")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('research-legacy', 'Research legacy', 'active', 'UTC', 'NGN', "
                    "'granted', 'tenant', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # Exact pre-0009 writer shapes: neither artifact_kind nor the new
            # structured event dimensions existed yet.
            connection.execute(
                text(
                    "INSERT INTO research_events "
                    "(tenant_id, channel, event_type, redacted_text, outcome, pii_redacted, "
                    "created_at, id) VALUES "
                    "('tenant', 'web', 'question', 'safe legacy text', '{}', 1, "
                    "CURRENT_TIMESTAMP, 'legacy-event')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO research_reports "
                    "(report_type, generated_by, filters, status, title, summary, error, "
                    "created_at, finished_at, id) VALUES "
                    "('sme_usage', NULL, '{}', 'success', NULL, NULL, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'legacy-report')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "0009_research_instrumentation")
    upgraded = create_engine(database_url)
    try:
        inspector = inspect(upgraded)
        event_columns = {
            column["name"]: column for column in inspector.get_columns("research_events")
        }
        assert event_columns["idempotency_key"]["nullable"] is False
        assert {
            "language",
            "agent_confidence",
            "response_length_chars",
            "response_latency_ms",
            "follow_up_detected",
            "raw_text_present",
            "business_outcome",
            "quality_flags",
        } <= event_columns.keys()
        report_columns = {
            column["name"]: column for column in inspector.get_columns("research_reports")
        }
        assert report_columns["artifact_kind"]["nullable"] is False
        checks = {check["name"] for check in inspector.get_check_constraints("research_events")}
        assert {
            "ck_research_events_agent_confidence",
            "ck_research_events_response_length_nonnegative",
            "ck_research_events_response_latency_nonnegative",
        } <= checks
        assert "ck_research_reports_artifact_kind" in {
            check["name"] for check in inspector.get_check_constraints("research_reports")
        }
        assert "uq_research_events_idempotency_key" in {
            constraint["name"] for constraint in inspector.get_unique_constraints("research_events")
        }
        assert "ix_research_event_type_created" in {
            index["name"] for index in inspector.get_indexes("research_events")
        }
        agent_message_constraints = {
            constraint["name"]: constraint["column_names"]
            for constraint in inspector.get_unique_constraints("agent_messages")
        }
        assert agent_message_constraints[
            "uq_agent_messages_tenant_channel_external_message_id"
        ] == ["tenant_id", "channel", "external_message_id"]
        assert ["channel", "external_message_id"] not in agent_message_constraints.values()

        with upgraded.connect() as connection:
            legacy = connection.execute(
                text(
                    "SELECT idempotency_key, raw_text_present, business_outcome, quality_flags "
                    "FROM research_events WHERE id = 'legacy-event'"
                )
            ).one()
            assert legacy.idempotency_key == "legacy:legacy-event"
            assert legacy.raw_text_present == 0
            assert legacy.business_outcome == "{}"
            assert legacy.quality_flags == "[]"
            assert (
                connection.scalar(
                    text("SELECT artifact_kind FROM research_reports WHERE id = 'legacy-report'")
                )
                == "report"
            )

        insert_event = text(
            "INSERT INTO research_events "
            "(idempotency_key, tenant_id, channel, event_type, outcome, business_outcome, "
            "quality_flags, pii_redacted, created_at, id, agent_confidence, "
            "response_length_chars, response_latency_ms) VALUES "
            "(:key, 'tenant', 'web', 'assistant_response_sent', '{}', '{}', '[]', 1, "
            "CURRENT_TIMESTAMP, :id, :confidence, :length, :latency)"
        )
        with upgraded.begin() as connection:
            connection.execute(
                insert_event,
                {
                    "key": "new:event",
                    "id": "new-event",
                    "confidence": "high",
                    "length": 42,
                    "latency": 0,
                },
            )
        invalid_rows = (
            {
                "key": "new:event",
                "id": "duplicate-key",
                "confidence": "high",
                "length": 42,
                "latency": 0,
            },
            {
                "key": "bad:confidence",
                "id": "bad-confidence",
                "confidence": "certain",
                "length": 42,
                "latency": 0,
            },
            {
                "key": "bad:length",
                "id": "bad-length",
                "confidence": "low",
                "length": -1,
                "latency": 0,
            },
            {
                "key": "bad:latency",
                "id": "bad-latency",
                "confidence": "low",
                "length": 1,
                "latency": -1,
            },
        )
        for values in invalid_rows:
            with upgraded.begin() as connection, pytest.raises(IntegrityError):
                connection.execute(insert_event, values)

        with upgraded.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('research-second', 'Research second', 'active', 'UTC', 'NGN', "
                    "'granted', 'tenant-two', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for suffix in ("one", "two"):
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(primary_phone_e164, status, id, created_at, updated_at) VALUES "
                        "(:phone, 'active', :user_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "phone": "+2348000000001" if suffix == "one" else "+2348000000002",
                        "user_id": f"user-{suffix}",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO conversations "
                        "(tenant_id, user_id, channel, status, id, created_at, updated_at) VALUES "
                        "(:tenant_id, :user_id, 'web', 'open', :conversation_id, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "tenant_id": "tenant" if suffix == "one" else "tenant-two",
                        "user_id": f"user-{suffix}",
                        "conversation_id": f"conversation-{suffix}",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO agent_messages "
                        "(tenant_id, user_id, conversation_id, channel, direction, content, "
                        "external_message_id, created_at, id) VALUES "
                        "(:tenant_id, :user_id, :conversation_id, 'web', 'inbound', 'safe', "
                        "'tenant-local-client-id', CURRENT_TIMESTAMP, :message_id)"
                    ),
                    {
                        "tenant_id": "tenant" if suffix == "one" else "tenant-two",
                        "user_id": f"user-{suffix}",
                        "conversation_id": f"conversation-{suffix}",
                        "message_id": f"message-{suffix}",
                    },
                )
    finally:
        upgraded.dispose()

    with pytest.raises(RuntimeError, match="tenant-scoped external message identifiers overlap"):
        command.downgrade(config, "0008_bumpa_dataset_failures")
    cleanup = create_engine(database_url)
    try:
        with cleanup.begin() as connection:
            connection.execute(text("DELETE FROM agent_messages WHERE id = 'message-two'"))
    finally:
        cleanup.dispose()

    command.downgrade(config, "0008_bumpa_dataset_failures")
    downgraded = create_engine(database_url)
    try:
        inspector = inspect(downgraded)
        assert "artifact_kind" not in {
            column["name"] for column in inspector.get_columns("research_reports")
        }
        assert not {
            "idempotency_key",
            "language",
            "agent_confidence",
            "response_length_chars",
            "response_latency_ms",
            "follow_up_detected",
            "raw_text_present",
            "business_outcome",
            "quality_flags",
        } & {column["name"] for column in inspector.get_columns("research_events")}
        assert ["channel", "external_message_id"] in [
            constraint["column_names"]
            for constraint in inspector.get_unique_constraints("agent_messages")
        ]
        with downgraded.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM research_events WHERE id = 'legacy-event'")
                )
                == 1
            )
    finally:
        downgraded.dispose()

    command.upgrade(config, "0009_research_instrumentation")
    reupgraded = create_engine(database_url)
    try:
        agent_message_constraints = {
            constraint["name"]: constraint["column_names"]
            for constraint in inspect(reupgraded).get_unique_constraints("agent_messages")
        }
        assert agent_message_constraints[
            "uq_agent_messages_tenant_channel_external_message_id"
        ] == ["tenant_id", "channel", "external_message_id"]
        with reupgraded.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT idempotency_key FROM research_events WHERE id = 'legacy-event'")
                )
                == "legacy:legacy-event"
            )
    finally:
        reupgraded.dispose()

    # Prove the next migration is connected to this revision and head remains
    # reachable after the downgrade/upgrade rehearsal.
    command.upgrade(config, "head")
