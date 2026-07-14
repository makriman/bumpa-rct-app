from __future__ import annotations

import json
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


def test_canonical_metric_migration_backfills_defaults_scrubs_legacy_pii_and_interlocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'canonical-metrics.db'}"
    config = _config(database_url, monkeypatch)
    command.upgrade(config, "0013_web_pin_challenges")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(slug, name, status, timezone, currency_code, research_consent_status, "
                    "id, created_at, updated_at) VALUES "
                    "('legacy', 'Legacy', 'active', 'UTC', 'NGN', 'granted', "
                    "'tenant', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
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
                    "(tenant_id, bumpa_connection_id, status, completion_quality, "
                    "requested_from, requested_to, started_at, error, dataset_results, id) VALUES "
                    "('tenant', 'connection', 'running', 'pending', '2026-07-01', '2026-07-12', "
                    "CURRENT_TIMESTAMP, NULL, '{}', 'run')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_metric_snapshots "
                    "(tenant_id, sync_run_id, metric_key, value_decimal, requested_from, "
                    "requested_to, availability, id, created_at) VALUES "
                    "('tenant', 'run', 'sales.total_sales', 12, '2026-07-01', '2026-07-12', "
                    "'available', 'metric-before', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_orders "
                    "(tenant_id, bumpa_order_id, raw_payload, id, created_at, updated_at) VALUES "
                    "('tenant', 'order-1', :payload, 'order', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"payload": json.dumps({"customer_details": {"phone": "+2348000000999"}})},
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_raw_responses "
                    "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
                    "payload, pii_level, id, created_at) VALUES "
                    "('tenant', 'run', 'customers', 'top_customers_order', 200, 'available', "
                    ":payload, 'sensitive', 'raw', CURRENT_TIMESTAMP)"
                ),
                {"payload": json.dumps({"data": [{"label": "Private Name"}]})},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert "canonical_payload" in {
            column["name"] for column in inspect(engine).get_columns("bumpa_metric_snapshots")
        }
        assert {"sync_generation", "published_sync_generation"} <= {
            column["name"] for column in inspect(engine).get_columns("bumpa_connections")
        }
        assert "sync_generation" in {
            column["name"] for column in inspect(engine).get_columns("bumpa_sync_runs")
        }
        with engine.begin() as connection:
            generations = connection.execute(
                text(
                    "SELECT sync_generation, published_sync_generation "
                    "FROM bumpa_connections WHERE id = 'connection'"
                )
            ).one()
            assert tuple(generations) == (0, 0)
            assert (
                connection.scalar(
                    text("SELECT sync_generation FROM bumpa_sync_runs WHERE id = 'run'")
                )
                is None
            )
            assert (
                json.loads(
                    connection.scalar(
                        text(
                            "SELECT canonical_payload FROM bumpa_metric_snapshots "
                            "WHERE id = 'metric-before'"
                        )
                    )
                )
                == {}
            )
            connection.execute(
                text(
                    "INSERT INTO bumpa_sync_runs "
                    "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
                    "started_at, error, dataset_results, id) VALUES "
                    "('tenant', 'connection', 'running', '2026-07-01', '2026-07-12', "
                    "CURRENT_TIMESTAMP, NULL, '{}', 'run-old-writer')"
                )
            )
            assert (
                connection.scalar(
                    text("SELECT sync_generation FROM bumpa_sync_runs WHERE id = 'run-old-writer'")
                )
                is None
            )
            assert (
                json.loads(
                    connection.scalar(
                        text("SELECT raw_payload FROM bumpa_orders WHERE id = 'order'")
                    )
                )
                == {}
            )
            assert (
                json.loads(
                    connection.scalar(
                        text("SELECT payload FROM bumpa_raw_responses WHERE id = 'raw'")
                    )
                )
                == {}
            )
            # A writer deployed just before 0014 can still omit the additive column.
            connection.execute(
                text(
                    "INSERT INTO bumpa_metric_snapshots "
                    "(tenant_id, sync_run_id, metric_key, value_decimal, requested_from, "
                    "requested_to, availability, id, created_at) VALUES "
                    "('tenant', 'run', 'products.products_sold', 2, '2026-07-01', "
                    "'2026-07-12', 'available', 'metric-old-writer', CURRENT_TIMESTAMP)"
                )
            )
            assert (
                json.loads(
                    connection.scalar(
                        text(
                            "SELECT canonical_payload FROM bumpa_metric_snapshots "
                            "WHERE id = 'metric-old-writer'"
                        )
                    )
                )
                == {}
            )
            connection.execute(
                text(
                    "UPDATE bumpa_sync_runs SET status = 'partial', "
                    "completion_quality = 'accepted_partial', "
                    "partial_reason = 'optional_dataset_unavailable', "
                    "orders_availability = 'available', orders_count = 0, "
                    "finished_at = CURRENT_TIMESTAMP WHERE id = 'run'"
                )
            )
    finally:
        engine.dispose()

    engine = create_engine(database_url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO bumpa_sync_runs "
                        "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
                        "sync_generation, dataset_results, id) VALUES "
                        "('tenant', 'connection', 'running', '2026-07-01', '2026-07-12', "
                        "0, '{}', 'run-invalid-generation')"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE bumpa_connections SET sync_generation = 0, "
                        "published_sync_generation = 1 WHERE id = 'connection'"
                    )
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="optional-dataset"):
        command.downgrade(config, "0013_web_pin_challenges")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE bumpa_sync_runs SET partial_reason = 'profit_not_calculable' "
                    "WHERE id = 'run'"
                )
            )
    finally:
        engine.dispose()
    command.downgrade(config, "0013_web_pin_challenges")
    engine = create_engine(database_url)
    try:
        assert "sync_generation" not in {
            column["name"] for column in inspect(engine).get_columns("bumpa_sync_runs")
        }
        assert not {"sync_generation", "published_sync_generation"} & {
            column["name"] for column in inspect(engine).get_columns("bumpa_connections")
        }
    finally:
        engine.dispose()
