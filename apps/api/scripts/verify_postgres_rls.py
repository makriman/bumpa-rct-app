#!/usr/bin/env python3
"""Exercise tenant row-level security against a migrated PostgreSQL database.

This is intentionally not a pytest test: the normal unit suite uses SQLite fixtures.
CI invokes this script explicitly after Alembic has migrated its PostgreSQL service.
"""

from __future__ import annotations

import argparse
import os
import secrets
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar, cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException, Request, Response
from sqlalchemy import Engine, Table, create_engine, inspect, text, update
from sqlalchemy.engine import URL, Connection, RowMapping, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.security import find_login_eligible_user
from app.db import models
from app.db.base import Base
from app.providers.contracts import BumpaSnapshot, ProviderDataset, ProviderOrder
from app.routes.auth import request_otp, verify_code
from app.schemas import OtpRequest, OtpVerify
from app.services import bumpa as bumpa_service

FixtureRow = TypeVar("FixtureRow")
SYNC_COMPAT_TENANT_ID = "sync-compat-tenant"
SYNC_COMPAT_CONNECTION_ID = "sync-compat-connection"
SYNC_COMPAT_RUNS = {
    "sync-compat-success": ("success", None),
    "sync-compat-partial": ("partial", None),
    "sync-compat-failed": ("failed", "legacy provider failure"),
}
RAW_COMPAT_RUN_ID = "sync-compat-success"
RAW_COMPAT_OLD_ID = "sync-compat-old-http"
RAW_COMPAT_STATUSLESS_IDS = frozenset({"sync-compat-timeout", "sync-compat-transport"})
LOGIN_GLOBAL_READ_TABLES = ("platform_roles", "tenants", "users")
LOGIN_GLOBAL_WRITE_TABLES = ("auth_sessions", "otp_sessions")


def _revision_in_lineage(current_revision: str, required_revision: str) -> bool:
    """Return whether a migration exists in the current revision's ancestry."""

    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    revisions = ScriptDirectory.from_config(config).iterate_revisions(
        current_revision,
        "base",
    )
    return any(revision.revision == required_revision for revision in revisions)


def _require_postgres_url() -> URL:
    raw_url = (
        os.environ.get("RLS_ADMIN_DATABASE_URL")
        or os.environ.get("SYNC_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not raw_url:
        raise RuntimeError(
            "Set RLS_ADMIN_DATABASE_URL, SYNC_DATABASE_URL, or DATABASE_URL to a migrated "
            "PostgreSQL database"
        )
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("The PostgreSQL RLS gate cannot run against SQLite or another database")
    return url.set(drivername="postgresql+psycopg")


def _quote(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _model_table(row: object) -> Table:
    inspection = inspect(row)
    if inspection is None:
        raise TypeError(f"Object is not a mapped SQLAlchemy row: {type(row).__name__}")
    return cast(Table, cast(Any, inspection).mapper.local_table)


def _discover_tenant_tables(connection: Connection) -> list[str]:
    return list(
        connection.scalars(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
                "ORDER BY table_name"
            )
        )
    )


def _assert_schema_and_policies(connection: Connection, tenant_tables: list[str]) -> None:
    model_tables = sorted(
        table.name for table in Base.metadata.tables.values() if "tenant_id" in table.c
    )
    if tenant_tables != model_tables:
        raise AssertionError(
            "Migrated tenant tables and SQLAlchemy schema differ: "
            f"database={tenant_tables}, models={model_tables}"
        )
    if not tenant_tables:
        raise AssertionError("No tenant-owned tables were discovered")

    table_state = {
        row.relname: (row.relrowsecurity, row.relforcerowsecurity)
        for row in connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
            ),
            {"tables": tenant_tables},
        )
    }
    bad_table_state = {
        table: table_state.get(table)
        for table in tenant_tables
        if table_state.get(table) != (True, True)
    }
    if bad_table_state:
        raise AssertionError(f"Tenant tables without enabled and forced RLS: {bad_table_state}")

    policies: dict[str, list[RowMapping]] = {table: [] for table in tenant_tables}
    for row in connection.execute(
        text(
            "SELECT tablename, policyname, permissive, roles, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' AND tablename = ANY(:tables)"
        ),
        {"tables": tenant_tables},
    ).mappings():
        policies[str(row["tablename"])].append(row)

    for table, table_policies in policies.items():
        if len(table_policies) != 1:
            raise AssertionError(
                f"{table} must have exactly one auditable RLS policy, found {len(table_policies)}"
            )
        policy = table_policies[0]
        expected_fragments = (
            "current_setting('app.is_privileged'::text, true)",
            "current_setting('app.current_tenant_id'::text, true)",
        )
        policy_sql = f"{policy['qual']} {policy['with_check']}"
        if (
            policy["policyname"] != "tenant_isolation"
            or policy["permissive"] != "PERMISSIVE"
            or policy["cmd"] != "ALL"
            or policy["roles"] != ["public"]
            or policy["qual"] != policy["with_check"]
            or any(fragment not in policy_sql for fragment in expected_fragments)
        ):
            raise AssertionError(f"Unexpected tenant policy on {table}: {policy}")


def _seed_sync_compatibility(admin_engine: Engine) -> None:
    """Seed the exact pre-0006 row shape while CI is pinned at migration 0005."""

    with admin_engine.begin() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0005_platform_roles":
            raise AssertionError(
                f"Sync compatibility seed must run at 0005_platform_roles, found {revision}"
            )
        connection.execute(
            text(
                "INSERT INTO tenants "
                "(slug, name, status, timezone, currency_code, research_consent_status, "
                "id, created_at, updated_at) VALUES "
                "('sync-compat', 'Sync compatibility', 'active', 'UTC', 'NGN', "
                "'unknown', :tenant_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"tenant_id": SYNC_COMPAT_TENANT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO bumpa_connections "
                "(tenant_id, encrypted_api_key, scope_type, scope_id, provider, status, "
                "id, created_at, updated_at) VALUES "
                "(:tenant_id, 'encrypted', 'business_id', 'sync-compat', 'bumpa', 'active', "
                ":connection_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "tenant_id": SYNC_COMPAT_TENANT_ID,
                "connection_id": SYNC_COMPAT_CONNECTION_ID,
            },
        )
        for run_id in SYNC_COMPAT_RUNS:
            connection.execute(
                text(
                    "INSERT INTO bumpa_sync_runs "
                    "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
                    "started_at, error, dataset_results, id) VALUES "
                    "(:tenant_id, :connection_id, 'running', '2026-07-01', '2026-07-12', "
                    "CURRENT_TIMESTAMP, NULL, CAST('{}' AS json), :run_id)"
                ),
                {
                    "tenant_id": SYNC_COMPAT_TENANT_ID,
                    "connection_id": SYNC_COMPAT_CONNECTION_ID,
                    "run_id": run_id,
                },
            )


def _seed_bumpa_raw_compatibility(admin_engine: Engine) -> None:
    """Seed the exact pre-0008 raw-response writer shape at migration 0007."""

    with admin_engine.begin() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0007_legacy_sync_writer":
            raise AssertionError(
                "Raw-response compatibility seed must run at 0007_legacy_sync_writer, "
                f"found {revision}"
            )
        connection.execute(
            text(
                "INSERT INTO bumpa_raw_responses "
                "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
                "error_message, payload, pii_level, id, created_at) VALUES "
                "(:tenant_id, :run_id, 'sales', 'overview', 200, 'available', NULL, "
                "CAST('{}' AS json), 'sensitive', :raw_id, CURRENT_TIMESTAMP)"
            ),
            {
                "tenant_id": SYNC_COMPAT_TENANT_ID,
                "run_id": RAW_COMPAT_RUN_ID,
                "raw_id": RAW_COMPAT_OLD_ID,
            },
        )


def _assert_bumpa_raw_failure_schema(admin_engine: Engine) -> None:
    """Verify old-writer compatibility and every 0008 evidence constraint."""

    insert_sql = text(
        "INSERT INTO bumpa_raw_responses "
        "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
        "failure_kind, error_message, payload, pii_level, id, created_at) VALUES "
        "(:tenant_id, :run_id, 'products', 'overview', :status, :availability, :kind, "
        "'sanitized', CAST('{}' AS json), 'sensitive', :raw_id, CURRENT_TIMESTAMP) "
        "ON CONFLICT (id) DO UPDATE SET http_status = EXCLUDED.http_status, "
        "availability = EXCLUDED.availability, failure_kind = EXCLUDED.failure_kind"
    )
    with admin_engine.begin() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if not isinstance(revision, str) or not _revision_in_lineage(
            revision,
            "0008_bumpa_dataset_failures",
        ):
            raise AssertionError(
                "Expected migration 0008_bumpa_dataset_failures in the current ancestry, "
                f"found {revision}"
            )
        columns = {
            row.column_name: row.is_nullable
            for row in connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'bumpa_raw_responses' "
                    "AND column_name IN ('http_status', 'failure_kind')"
                )
            )
        }
        if columns != {"http_status": "YES", "failure_kind": "YES"}:
            raise AssertionError(f"Unexpected raw-response evidence columns: {columns}")
        checks = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'bumpa_raw_responses'::regclass AND contype = 'c'"
                )
            )
        )
        required_checks = {
            "ck_bumpa_raw_responses_http_status",
            "ck_bumpa_raw_responses_failure_kind",
            "ck_bumpa_raw_responses_status_evidence",
            "ck_bumpa_raw_responses_failure_availability",
        }
        if not required_checks <= checks:
            raise AssertionError(
                f"Missing raw-response evidence checks: {required_checks - checks}"
            )
        pre_0008_evidence = connection.execute(
            text("SELECT http_status, failure_kind FROM bumpa_raw_responses WHERE id = :raw_id"),
            {"raw_id": RAW_COMPAT_OLD_ID},
        ).one_or_none()
        if pre_0008_evidence is None or tuple(pre_0008_evidence) != (200, None):
            raise AssertionError(f"Pre-0008 HTTP evidence was not preserved: {pre_0008_evidence}")

        # A pre-0008 writer may keep omitting the nullable column after upgrade.
        connection.execute(
            text(
                "INSERT INTO bumpa_raw_responses "
                "(tenant_id, sync_run_id, resource, dataset, http_status, availability, "
                "error_message, payload, pii_level, id, created_at) VALUES "
                "(:tenant_id, :run_id, 'sales', 'total_sales', 200, 'available', NULL, "
                "CAST('{}' AS json), 'sensitive', 'sync-compat-old-writer-head', "
                "CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING"
            ),
            {"tenant_id": SYNC_COMPAT_TENANT_ID, "run_id": RAW_COMPAT_RUN_ID},
        )
        valid_rows: tuple[tuple[str, int | None, str], ...] = (
            ("sync-compat-timeout", None, "timeout"),
            ("sync-compat-transport", None, "transport"),
            ("sync-compat-gateway", 504, "upstream_http"),
        )
        for raw_id, status, valid_kind in valid_rows:
            connection.execute(
                insert_sql,
                {
                    "tenant_id": SYNC_COMPAT_TENANT_ID,
                    "run_id": RAW_COMPAT_RUN_ID,
                    "status": status,
                    "availability": "error",
                    "kind": valid_kind,
                    "raw_id": raw_id,
                },
            )

        typed_evidence = {
            str(row.id): (row.http_status, row.failure_kind)
            for row in connection.execute(
                text(
                    "SELECT id, http_status, failure_kind FROM bumpa_raw_responses "
                    "WHERE id IN ('sync-compat-timeout', 'sync-compat-transport', "
                    "'sync-compat-gateway')"
                )
            )
        }
        expected_typed_evidence = {
            "sync-compat-timeout": (None, "timeout"),
            "sync-compat-transport": (None, "transport"),
            "sync-compat-gateway": (504, "upstream_http"),
        }
        if typed_evidence != expected_typed_evidence:
            raise AssertionError(f"Typed raw-response evidence was not stored: {typed_evidence}")

        invalid_rows: tuple[tuple[str, int | None, str, str | None], ...] = (
            ("sync-compat-null-null", None, "error", None),
            ("sync-compat-null-http", None, "error", "upstream_http"),
            ("sync-compat-bad-availability", 504, "available", "upstream_http"),
        )
        for raw_id, status, availability, invalid_kind in invalid_rows:
            savepoint = connection.begin_nested()
            try:
                connection.execute(
                    insert_sql,
                    {
                        "tenant_id": SYNC_COMPAT_TENANT_ID,
                        "run_id": RAW_COMPAT_RUN_ID,
                        "status": status,
                        "availability": availability,
                        "kind": invalid_kind,
                        "raw_id": raw_id,
                    },
                )
            except DBAPIError as exc:
                savepoint.rollback()
                if getattr(exc.orig, "sqlstate", None) != "23514":
                    raise AssertionError("Raw evidence failed outside CHECK enforcement") from exc
            else:
                savepoint.rollback()
                raise AssertionError(f"Invalid raw evidence bypassed checks: {raw_id}")


def _remove_statusless_bumpa_evidence(admin_engine: Engine) -> None:
    """Remove only the two disposable CI fixtures required by the downgrade gate."""

    with admin_engine.begin() as connection:
        removed_ids = set(
            connection.scalars(
                text(
                    "DELETE FROM bumpa_raw_responses "
                    "WHERE tenant_id = :tenant_id AND sync_run_id = :run_id "
                    "AND id = ANY(:raw_ids) AND http_status IS NULL RETURNING id"
                ),
                {
                    "tenant_id": SYNC_COMPAT_TENANT_ID,
                    "run_id": RAW_COMPAT_RUN_ID,
                    "raw_ids": sorted(RAW_COMPAT_STATUSLESS_IDS),
                },
            )
        )
        if removed_ids != RAW_COMPAT_STATUSLESS_IDS:
            raise AssertionError(
                "Status-less fixture cleanup did not remove exactly the expected evidence: "
                f"{removed_ids}"
            )


def _assert_bumpa_raw_downgrade_schema(admin_engine: Engine) -> None:
    """Prove 0007 restored the old writer contract without losing HTTP evidence."""

    with admin_engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0007_legacy_sync_writer":
            raise AssertionError(f"Expected migration 0007 after downgrade, found {revision}")
        columns = {
            str(row.column_name): str(row.is_nullable)
            for row in connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'bumpa_raw_responses' "
                    "AND column_name IN ('http_status', 'failure_kind')"
                )
            )
        }
        if columns != {"http_status": "NO"}:
            raise AssertionError(f"Unexpected downgraded raw-response columns: {columns}")

        checks = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'bumpa_raw_responses'::regclass AND contype = 'c'"
                )
            )
        )
        if "ck_bumpa_raw_responses_http_status" not in checks:
            raise AssertionError("0007 HTTP status check was not restored")
        removed_checks = {
            "ck_bumpa_raw_responses_failure_kind",
            "ck_bumpa_raw_responses_status_evidence",
            "ck_bumpa_raw_responses_failure_availability",
        }
        if removed_checks & checks:
            raise AssertionError(
                f"0008 raw-response checks survived downgrade: {removed_checks & checks}"
            )

        preserved = {
            str(row.id): (int(row.http_status), str(row.availability))
            for row in connection.execute(
                text(
                    "SELECT id, http_status, availability FROM bumpa_raw_responses "
                    "WHERE tenant_id = :tenant_id AND sync_run_id = :run_id "
                    "AND id IN (:old_id, 'sync-compat-old-writer-head', "
                    "'sync-compat-gateway', 'sync-compat-timeout', "
                    "'sync-compat-transport')"
                ),
                {
                    "tenant_id": SYNC_COMPAT_TENANT_ID,
                    "run_id": RAW_COMPAT_RUN_ID,
                    "old_id": RAW_COMPAT_OLD_ID,
                },
            )
        }
        expected = {
            RAW_COMPAT_OLD_ID: (200, "available"),
            "sync-compat-old-writer-head": (200, "available"),
            "sync-compat-gateway": (504, "error"),
        }
        if preserved != expected:
            raise AssertionError(f"Downgrade did not preserve HTTP evidence: {preserved}")


def _verify_bumpa_sync_atomicity(admin_engine: Engine) -> None:
    """Exercise the generation publication fence with real PostgreSQL sessions."""

    factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    service_module: Any = bumpa_service
    original_provider = service_module.LocalCommerceProvider
    created_tenant_ids: list[str] = []

    def create_fixture(label: str) -> tuple[str, str]:
        tag = secrets.token_hex(4)
        tenant_id = f"sf-t-{label[:4]}-{tag}"
        connection_id = f"sf-c-{label[:4]}-{tag}"
        with factory() as db:
            db.add(
                models.Tenant(
                    id=tenant_id,
                    slug=f"sync-fence-{label}-{tag}",
                    name="Sync generation fence",
                    status="active",
                    timezone="UTC",
                    currency_code="NGN",
                    research_consent_status="unknown",
                )
            )
            db.flush()
            db.add(
                models.BumpaConnection(
                    id=connection_id,
                    tenant_id=tenant_id,
                    encrypted_api_key="local-gate-only",
                    scope_type="business_id",
                    scope_id=f"scope-{tag}",
                    provider="local",
                    status="active",
                )
            )
            db.commit()
        created_tenant_ids.append(tenant_id)
        return tenant_id, connection_id

    def snapshot(label: str, value: Decimal) -> BumpaSnapshot:
        response_from = datetime(2026, 7, 1, tzinfo=UTC)
        response_to = datetime(2026, 7, 12, tzinfo=UTC)
        datasets = [
            ProviderDataset(
                resource=resource,
                dataset=dataset,
                availability="available",
                payload={"value": str(value)},
                value=value,
                title=key,
                currency_code="NGN" if resource == "sales" else None,
                response_from=response_from,
                response_to=response_to,
            )
            for key in sorted(bumpa_service.EXPECTED_SYNC_DATASETS)
            for resource, dataset in (key.split(".", maxsplit=1),)
        ]
        return BumpaSnapshot(
            datasets=datasets,
            orders=[
                ProviderOrder(
                    order_id="generation-order",
                    order_number="GENERATION-1",
                    status=label,
                    payment_status="paid",
                    currency_code="NGN",
                    total_amount=value,
                    order_date=response_to,
                    payload={
                        "id": "generation-order",
                        "publication": label,
                        "items": [
                            {
                                "id": "generation-item",
                                "name": f"{label} item",
                                "quantity": "1",
                                "price": str(value),
                            }
                        ],
                    },
                )
            ],
        )

    def verify_pair(case: str) -> None:
        tenant_id, connection_id = create_fixture(case)
        entered = (threading.Event(), threading.Event())
        releases = (threading.Event(), threading.Event())
        call_lock = threading.Lock()
        result_lock = threading.Lock()
        call_count = 0
        outcomes: dict[str, tuple[str, int]] = {}
        http_failures: dict[str, tuple[int, str]] = {}
        unexpected: dict[str, str] = {}

        class ControlledProvider:
            def __init__(self, _tenant_seed: str) -> None:
                pass

            def sync(self, _date_from: date, _date_to: date) -> BumpaSnapshot:
                nonlocal call_count
                with call_lock:
                    index = call_count
                    call_count += 1
                if index > 1:
                    raise RuntimeError("Generation gate received an unexpected provider call")
                entered[index].set()
                if not releases[index].wait(timeout=30):
                    raise RuntimeError("Generation gate timed out at provider boundary")
                if case == "newer_failure" and index == 1:
                    raise RuntimeError("Synthetic newer extraction failure")
                return snapshot(
                    "older" if index == 0 else "newer",
                    Decimal("100") if index == 0 else Decimal("200"),
                )

        def worker(label: str) -> None:
            try:
                with factory() as db:
                    connection = db.get(models.BumpaConnection, connection_id)
                    if connection is None:
                        raise RuntimeError("Generation gate connection disappeared")
                    run = bumpa_service.run_sync(
                        db,
                        tenant_id=tenant_id,
                        connection=connection,
                        date_from=date(2026, 7, 1),
                        date_to=date(2026, 7, 12),
                    )
                    with result_lock:
                        outcomes[label] = (run.status, int(run.sync_generation or -1))
            except HTTPException as exc:
                with result_lock:
                    http_failures[label] = (exc.status_code, str(exc.detail))
            except Exception as exc:  # pragma: no cover - asserted below
                with result_lock:
                    unexpected[label] = type(exc).__name__

        first = threading.Thread(target=worker, args=("older",), daemon=True)
        second = threading.Thread(target=worker, args=("newer",), daemon=True)
        service_module.LocalCommerceProvider = ControlledProvider
        try:
            first.start()
            if not entered[0].wait(timeout=10):
                raise AssertionError("Older sync did not enter its provider boundary")
            second.start()
            if not entered[1].wait(timeout=10):
                raise AssertionError("Newer sync did not enter its provider boundary")

            if case == "older_then_newer":
                releases[0].set()
                first.join(timeout=20)
                releases[1].set()
            else:
                releases[1].set()
                second.join(timeout=20)
                releases[0].set()

            first.join(timeout=20)
            second.join(timeout=20)
            if first.is_alive() or second.is_alive():
                raise AssertionError(f"Generation scenario {case} did not terminate")
            if unexpected:
                raise AssertionError(f"Unexpected generation failures for {case}: {unexpected}")
        finally:
            releases[0].set()
            releases[1].set()
            first.join(timeout=5)
            second.join(timeout=5)
            service_module.LocalCommerceProvider = original_provider

        if case == "older_then_newer":
            if outcomes != {"older": ("success", 1), "newer": ("success", 2)}:
                raise AssertionError(f"Older/newer publication outcomes are invalid: {outcomes}")
            if http_failures:
                raise AssertionError(f"Unexpected older/newer HTTP failures: {http_failures}")
            expected_published = 2
            expected_order = "newer"
            expected_runs: dict[int, tuple[str, str | None]] = {
                1: ("success", None),
                2: ("success", None),
            }
        elif case == "newer_then_older":
            if outcomes != {"newer": ("success", 2)}:
                raise AssertionError(f"Newer-first outcome is invalid: {outcomes}")
            expected_http = {"older": (409, "Bumpa sync was superseded by a newer request")}
            if http_failures != expected_http:
                raise AssertionError(f"Older extraction was not superseded: {http_failures}")
            expected_published = 2
            expected_order = "newer"
            expected_runs = {
                1: ("failed", "Superseded by a newer Bumpa sync"),
                2: ("success", None),
            }
        else:
            if outcomes != {"older": ("success", 1)}:
                raise AssertionError(f"Failed-newer fallback outcome is invalid: {outcomes}")
            if http_failures != {"newer": (502, "Commerce sync failed")}:
                raise AssertionError(f"Newer failure was not audited: {http_failures}")
            expected_published = 1
            expected_order = "older"
            expected_runs = {
                1: ("success", None),
                2: ("failed", "Commerce sync failed"),
            }

        with admin_engine.connect() as connection:
            fence = connection.execute(
                text(
                    "SELECT sync_generation, published_sync_generation "
                    "FROM bumpa_connections WHERE id = :connection_id"
                ),
                {"connection_id": connection_id},
            ).one()
            if tuple(fence) != (2, expected_published):
                raise AssertionError(f"Generation counters are invalid for {case}: {fence}")
            runs = {
                int(row.sync_generation): (str(row.status), row.error)
                for row in connection.execute(
                    text(
                        "SELECT sync_generation, status, error FROM bumpa_sync_runs "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
            }
            if runs != expected_runs:
                raise AssertionError(f"Terminal generation audits are invalid for {case}: {runs}")
            order = connection.execute(
                text(
                    "SELECT status, raw_payload FROM bumpa_orders "
                    "WHERE tenant_id = :tenant_id AND bumpa_order_id = 'generation-order'"
                ),
                {"tenant_id": tenant_id},
            ).one_or_none()
            if (
                order is None
                or order.status != expected_order
                or not isinstance(order.raw_payload, dict)
                or order.raw_payload.get("publication") != expected_order
            ):
                raise AssertionError(f"Canonical generation order is invalid for {case}: {order}")

    def verify_rotation(column: str, value: str) -> None:
        tenant_id, connection_id = create_fixture(f"rot-{column[:3]}")
        entered = threading.Event()
        release = threading.Event()
        result_lock = threading.Lock()
        outcomes: dict[str, tuple[int, str]] = {}
        unexpected: list[str] = []

        class RotationProvider:
            def __init__(self, _tenant_seed: str) -> None:
                pass

            def sync(self, _date_from: date, _date_to: date) -> BumpaSnapshot:
                entered.set()
                if not release.wait(timeout=30):
                    raise RuntimeError("Rotation gate timed out at provider boundary")
                return snapshot("rotated", Decimal("300"))

        def worker() -> None:
            try:
                with factory() as db:
                    connection = db.get(models.BumpaConnection, connection_id)
                    if connection is None:
                        raise RuntimeError("Rotation gate connection disappeared")
                    bumpa_service.run_sync(
                        db,
                        tenant_id=tenant_id,
                        connection=connection,
                        date_from=date(2026, 7, 1),
                        date_to=date(2026, 7, 12),
                    )
            except HTTPException as exc:
                with result_lock:
                    outcomes["sync"] = (exc.status_code, str(exc.detail))
            except Exception as exc:  # pragma: no cover - asserted below
                with result_lock:
                    unexpected.append(type(exc).__name__)

        thread = threading.Thread(target=worker, daemon=True)
        service_module.LocalCommerceProvider = RotationProvider
        try:
            thread.start()
            if not entered.wait(timeout=10):
                raise AssertionError(f"{column} rotation sync never entered provider")
            with admin_engine.begin() as connection:
                connection.execute(
                    update(models.BumpaConnection)
                    .where(models.BumpaConnection.id == connection_id)
                    .values({column: value})
                )
            release.set()
            thread.join(timeout=20)
            if thread.is_alive():
                raise AssertionError(f"{column} rotation gate did not terminate")
            if unexpected:
                raise AssertionError(f"Unexpected {column} rotation failure: {unexpected}")
        finally:
            release.set()
            thread.join(timeout=5)
            service_module.LocalCommerceProvider = original_provider

        expected = {"sync": (409, "Bumpa connection changed during sync")}
        if outcomes != expected:
            raise AssertionError(f"{column} rotation did not fence publication: {outcomes}")
        with admin_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT sync_generation, published_sync_generation FROM bumpa_connections "
                    "WHERE id = :connection_id"
                ),
                {"connection_id": connection_id},
            ).one()
            run_count = int(
                connection.scalar(
                    text("SELECT COUNT(*) FROM bumpa_sync_runs WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                or 0
            )
            order_count = int(
                connection.scalar(
                    text("SELECT COUNT(*) FROM bumpa_orders WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                or 0
            )
            if tuple(state) != (1, 0) or run_count != 0 or order_count != 0:
                raise AssertionError(
                    f"{column} rotation leaked publication state: "
                    f"state={state}, runs={run_count}, orders={order_count}"
                )

    try:
        verify_pair("older_then_newer")
        verify_pair("newer_then_older")
        verify_pair("newer_failure")
        verify_rotation("encrypted_api_key", "rotated-local-key")
        verify_rotation("scope_id", "rotated-scope")
        verify_rotation("status", "inactive")
    finally:
        service_module.LocalCommerceProvider = original_provider
        if created_tenant_ids:
            with admin_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:tenant_ids)"),
                    {"tenant_ids": created_tenant_ids},
                )


def _assert_check_rejected(
    connection: Connection, run_id: str, values: Mapping[str, object]
) -> None:
    savepoint = connection.begin_nested()
    try:
        connection.execute(
            text(
                "INSERT INTO bumpa_sync_runs "
                "(tenant_id, bumpa_connection_id, status, completion_quality, "
                "partial_reason, orders_availability, orders_count, error, requested_from, "
                "requested_to, dataset_results, id) VALUES "
                "(:tenant_id, :connection_id, :status, :quality, :reason, "
                ":orders_availability, :orders_count, :error, '2026-07-01', '2026-07-12', "
                "CAST('{}' AS json), :run_id)"
            ),
            {
                "tenant_id": SYNC_COMPAT_TENANT_ID,
                "connection_id": SYNC_COMPAT_CONNECTION_ID,
                "run_id": run_id,
                **values,
            },
        )
    except DBAPIError as exc:
        savepoint.rollback()
        if getattr(exc.orig, "sqlstate", None) != "23514":
            raise AssertionError(
                f"Invalid sync state {run_id} failed for a reason other than CHECK enforcement"
            ) from exc
    else:
        savepoint.rollback()
        raise AssertionError(f"Invalid sync state {run_id} bypassed completion CHECKs")


def _assert_sync_writer_compatibility(admin_engine: Engine) -> None:
    """Prove PostgreSQL preserved old-writer rollback while typed states stay strict."""

    try:
        with admin_engine.begin() as connection:
            default = connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'bumpa_sync_runs' "
                    "AND column_name = 'completion_quality'"
                )
            )
            if default is None or "legacy" not in str(default):
                raise AssertionError(
                    f"completion_quality must have the legacy server default, found {default}"
                )

            migrated = {
                str(row.id): (
                    row.completion_quality,
                    row.partial_reason,
                    row.orders_availability,
                    row.orders_count,
                )
                for row in connection.execute(
                    text(
                        "SELECT id, completion_quality, partial_reason, orders_availability, "
                        "orders_count FROM bumpa_sync_runs WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": SYNC_COMPAT_TENANT_ID},
                )
            }
            expected_migrated = {
                run_id: ("legacy", None, None, None) for run_id in SYNC_COMPAT_RUNS
            }
            parent_count = int(
                connection.scalar(
                    text("SELECT COUNT(*) FROM tenants WHERE id = :tenant_id"),
                    {"tenant_id": SYNC_COMPAT_TENANT_ID},
                )
                or 0
            ) + int(
                connection.scalar(
                    text("SELECT COUNT(*) FROM bumpa_connections WHERE id = :connection_id"),
                    {"connection_id": SYNC_COMPAT_CONNECTION_ID},
                )
                or 0
            )
            if migrated and migrated != expected_migrated:
                raise AssertionError(
                    "0005 in-flight rows were not converted to evidence-free legacy states: "
                    f"{migrated}"
                )
            if not migrated and parent_count:
                raise AssertionError(
                    "PostgreSQL sync compatibility fixtures are incomplete: "
                    f"parents={parent_count}, runs={migrated}"
                )
            if migrated and parent_count != 2:
                raise AssertionError(
                    "PostgreSQL sync compatibility fixtures are missing parents: "
                    f"parents={parent_count}"
                )
            if migrated:
                for run_id, (status, error) in SYNC_COMPAT_RUNS.items():
                    connection.execute(
                        text(
                            "UPDATE bumpa_sync_runs SET status = :status, error = :error, "
                            "finished_at = CURRENT_TIMESTAMP WHERE id = :run_id"
                        ),
                        {"run_id": run_id, "status": status, "error": error},
                    )
                terminal = {
                    str(row.id): (row.status, row.error)
                    for row in connection.execute(
                        text(
                            "SELECT id, status, error FROM bumpa_sync_runs "
                            "WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": SYNC_COMPAT_TENANT_ID},
                    )
                }
                if terminal != SYNC_COMPAT_RUNS:
                    raise AssertionError(
                        "Legacy success/partial/failed terminal updates did not persist: "
                        f"{terminal}"
                    )
            else:
                # Keep the script useful as a standalone head-schema gate. CI's
                # baseline/seed path additionally proves the 0005 in-flight
                # transformation; a direct-head caller still proves the server
                # default and strict constraints below.
                connection.execute(
                    text(
                        "INSERT INTO tenants "
                        "(slug, name, status, timezone, currency_code, "
                        "research_consent_status, id, created_at, updated_at) VALUES "
                        "('sync-compat', 'Sync compatibility', 'active', 'UTC', 'NGN', "
                        "'unknown', :tenant_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"tenant_id": SYNC_COMPAT_TENANT_ID},
                )
                connection.execute(
                    text(
                        "INSERT INTO bumpa_connections "
                        "(tenant_id, encrypted_api_key, scope_type, scope_id, provider, status, "
                        "id, created_at, updated_at) VALUES "
                        "(:tenant_id, 'encrypted', 'business_id', 'sync-compat', 'bumpa', "
                        "'active', :connection_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "tenant_id": SYNC_COMPAT_TENANT_ID,
                        "connection_id": SYNC_COMPAT_CONNECTION_ID,
                    },
                )

            # An old process started after rollback must receive the same server
            # discriminator without naming any 0006+ column.
            connection.execute(
                text(
                    "INSERT INTO bumpa_sync_runs "
                    "(tenant_id, bumpa_connection_id, status, requested_from, requested_to, "
                    "started_at, error, dataset_results, id) VALUES "
                    "(:tenant_id, :connection_id, 'running', '2026-07-01', '2026-07-12', "
                    "CURRENT_TIMESTAMP, NULL, CAST('{}' AS json), 'sync-compat-post-head')"
                ),
                {
                    "tenant_id": SYNC_COMPAT_TENANT_ID,
                    "connection_id": SYNC_COMPAT_CONNECTION_ID,
                },
            )
            post_head_quality = connection.scalar(
                text(
                    "SELECT completion_quality FROM bumpa_sync_runs "
                    "WHERE id = 'sync-compat-post-head'"
                )
            )
            if post_head_quality != "legacy":
                raise AssertionError(
                    "Post-migration old-schema insert did not receive legacy server default"
                )

            invalid_states = {
                "sync-invalid-pending-success": {
                    "status": "success",
                    "quality": "pending",
                    "reason": None,
                    "orders_availability": None,
                    "orders_count": None,
                    "error": None,
                },
                "sync-invalid-legacy-evidence": {
                    "status": "success",
                    "quality": "legacy",
                    "reason": None,
                    "orders_availability": "available",
                    "orders_count": None,
                    "error": None,
                },
                "sync-invalid-legacy-failure": {
                    "status": "failed",
                    "quality": "legacy",
                    "reason": None,
                    "orders_availability": None,
                    "orders_count": None,
                    "error": None,
                },
            }
            for run_id, values in invalid_states.items():
                _assert_check_rejected(connection, run_id, values)
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM bumpa_sync_runs WHERE tenant_id = :tenant_id"),
                {"tenant_id": SYNC_COMPAT_TENANT_ID},
            )
            connection.execute(
                text("DELETE FROM bumpa_connections WHERE id = :connection_id"),
                {"connection_id": SYNC_COMPAT_CONNECTION_ID},
            )
            connection.execute(
                text("DELETE FROM tenants WHERE id = :tenant_id"),
                {"tenant_id": SYNC_COMPAT_TENANT_ID},
            )


def _fixture_pair(
    run_tag: str,
) -> tuple[list[object], dict[str, list[str]], tuple[str, str], tuple[str, str]]:
    now = datetime.now(UTC)
    tenant_ids = (f"tenant-a-{run_tag}", f"tenant-b-{run_tag}")
    user_ids = (f"user-a-{run_tag}", f"user-b-{run_tag}")
    rows: list[object] = []
    ids_by_table: dict[str, list[str]] = {}

    def add(row: FixtureRow) -> FixtureRow:
        table_name = _model_table(row).name
        row_id = str(row.__dict__["id"])
        ids_by_table.setdefault(table_name, []).append(row_id)
        rows.append(row)
        return row

    for index, side in enumerate(("a", "b")):
        tenant_id = tenant_ids[index]
        user_id = user_ids[index]
        suffix = f"{side}-{run_tag}"
        phone = f"+1555{index}{int(run_tag, 16) % 10_000_000:07d}"
        add(
            models.Tenant(
                id=tenant_id,
                slug=f"rls-{suffix}",
                name=f"RLS tenant {side.upper()}",
                status="active",
                timezone="UTC",
                currency_code="NGN",
                research_consent_status="granted",
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.User(
                id=user_id,
                name=f"RLS user {side.upper()}",
                primary_phone_e164=phone,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.TenantMembership(
                id=f"membership-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                role="owner",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.PhoneIdentity(
                id=f"phone-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                phone_e164=phone,
                status="approved",
                opt_out=False,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.ResearchConsent(
                id=f"consent-{suffix}",
                tenant_id=tenant_id,
                status="granted",
                policy_version="rls-gate",
                actor_user_id=user_id,
                recorded_at=now,
            )
        )
        connection = add(
            models.BumpaConnection(
                id=f"bumpa-connection-{suffix}",
                tenant_id=tenant_id,
                encrypted_api_key="integration-test-ciphertext",
                scope_type="business_id",
                scope_id=f"business-{suffix}",
                provider="local",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        sync_run = add(
            models.BumpaSyncRun(
                id=f"sync-{suffix}",
                tenant_id=tenant_id,
                bumpa_connection_id=connection.id,
                status="success",
                completion_quality="complete",
                partial_reason=None,
                requested_from=date(2026, 1, 1),
                requested_to=date(2026, 1, 1),
                finished_at=now,
                error=None,
                orders_availability="available",
                orders_count=0,
                dataset_results={},
            )
        )
        add(
            models.BumpaRawResponse(
                id=f"raw-{suffix}",
                tenant_id=tenant_id,
                sync_run_id=sync_run.id,
                resource="orders",
                dataset="orders",
                http_status=200,
                availability="available",
                payload={},
                pii_level="sensitive",
                created_at=now,
            )
        )
        add(
            models.BumpaMetricSnapshot(
                id=f"metric-{suffix}",
                tenant_id=tenant_id,
                sync_run_id=sync_run.id,
                metric_key="rls_gate",
                value_decimal=Decimal("1"),
                requested_from=date(2026, 1, 1),
                requested_to=date(2026, 1, 1),
                availability="available",
                created_at=now,
            )
        )
        order = add(
            models.BumpaOrder(
                id=f"order-{suffix}",
                tenant_id=tenant_id,
                bumpa_order_id=f"provider-order-{suffix}",
                currency_code="NGN",
                total_amount=Decimal("1"),
                raw_payload={},
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.BumpaOrderItem(
                id=f"order-item-{suffix}",
                tenant_id=tenant_id,
                order_id=order.id,
                name="RLS item",
                quantity=Decimal("1"),
                raw_payload={},
                created_at=now,
                updated_at=now,
            )
        )
        profile = add(
            models.HermesProfile(
                id=f"hermes-{suffix}",
                tenant_id=tenant_id,
                profile_name=f"rls-profile-{suffix}",
                provider="local",
                api_internal_url="local://rls-gate",
                encrypted_api_key="integration-test-ciphertext",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        conversation = add(
            models.Conversation(
                id=f"conversation-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                channel="web",
                status="open",
                created_at=now,
                updated_at=now,
            )
        )
        message = add(
            models.AgentMessage(
                id=f"message-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                hermes_profile_id=profile.id,
                conversation_id=conversation.id,
                channel="web",
                direction="inbound",
                content="RLS integration fixture",
                external_message_id=f"external-{suffix}",
                created_at=now,
            )
        )
        add(
            models.AgentToolCall(
                id=f"tool-call-{suffix}",
                tenant_id=tenant_id,
                agent_message_id=message.id,
                tool_name="rls_gate",
                status="success",
                created_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.WhatsappMessage(
                id=f"whatsapp-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=f"rls-idempotency-{suffix}",
                meta_message_id=f"rls-meta-{suffix}",
                direction="inbound",
                message_type="text",
                text_body="RLS integration fixture",
                payload={},
                status="received",
                created_at=now,
            )
        )
        add(
            models.ResearchEvent(
                id=f"research-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation.id,
                agent_message_id=message.id,
                channel="web",
                event_type="rls_gate",
                outcome={},
                pii_redacted=True,
                created_at=now,
            )
        )
        mcp_connection = add(
            models.McpConnection(
                id=f"mcp-{suffix}",
                tenant_id=tenant_id,
                created_by=user_id,
                provider="google_drive",
                status="disabled",
                scopes=[],
                read_only=True,
                admin_approved=False,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.McpToolPermission(
                id=f"mcp-permission-{suffix}",
                tenant_id=tenant_id,
                mcp_connection_id=mcp_connection.id,
                tool_name="rls_gate",
                permission="deny",
                created_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.AuditLog(
                id=f"audit-{suffix}",
                tenant_id=tenant_id,
                actor_user_id=user_id,
                action="rls_gate",
                created_at=now,
            )
        )
        add(
            models.SystemError(
                id=f"error-{suffix}",
                tenant_id=tenant_id,
                service="rls_gate",
                severity="info",
                message="RLS integration fixture",
                error_metadata={},
                created_at=now,
            )
        )
        job = add(
            models.AsyncJob(
                id=f"job-{suffix}",
                tenant_id=tenant_id,
                queue_name="rls_gate",
                kind="rls_gate",
                payload={},
                status="pending",
                idempotency_key=f"job-{suffix}",
                attempts=0,
                max_attempts=1,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.JobOutbox(
                id=f"outbox-{suffix}",
                tenant_id=tenant_id,
                job_id=job.id,
                status="pending",
                available_at=now,
                dispatch_attempts=0,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.TenantOnboarding(
                id=f"onboarding-{suffix}",
                tenant_id=tenant_id,
                status="in_progress",
                current_step="owner",
                revision=0,
                sync_attempt=0,
                start_idempotency_key_hash=("a" if side == "a" else "b") * 64,
                start_fingerprint=("c" if side == "a" else "d") * 64,
                created_by=user_id,
                updated_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        add(
            models.UsageEvent(
                id=f"usage-{suffix}",
                tenant_id=tenant_id,
                user_id=user_id,
                event_name="rls_gate",
                units=Decimal("1"),
                event_metadata={},
                created_at=now,
            )
        )
    return rows, ids_by_table, tenant_ids, user_ids


def _seed(admin_engine: Engine, rows: Iterable[object]) -> None:
    from sqlalchemy.orm import Session

    rows_by_table: dict[str, list[object]] = {}
    for row in rows:
        rows_by_table.setdefault(_model_table(row).name, []).append(row)
    with Session(admin_engine) as session:
        # These lean models intentionally define no ORM relationships. Flush in the
        # metadata's FK-topological order so dependencies still precede children.
        for table in Base.metadata.sorted_tables:
            table_rows = rows_by_table.get(table.name)
            if table_rows:
                session.add_all(table_rows)
                session.flush()
        session.commit()


def _create_role(admin_engine: Engine, role: str, password: str, tables: list[str]) -> None:
    with admin_engine.begin() as connection:
        quoted_role = _quote(connection, role)
        database = str(connection.scalar(text("SELECT current_database()")))
        quoted_database = _quote(connection, database)
        # Both values are generated from fixed safe alphabets, never user-controlled.
        connection.exec_driver_sql(
            f"CREATE ROLE {quoted_role} LOGIN PASSWORD '{password}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
        )
        connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE {quoted_database} TO {quoted_role}")
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
        quoted_tables = ", ".join(f"public.{_quote(connection, table)}" for table in tables)
        connection.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {quoted_tables} TO {quoted_role}"
        )
        quoted_global_read_tables = ", ".join(
            f"public.{_quote(connection, table)}" for table in LOGIN_GLOBAL_READ_TABLES
        )
        connection.exec_driver_sql(
            f"GRANT SELECT ON TABLE {quoted_global_read_tables} TO {quoted_role}"
        )
        quoted_global_write_tables = ", ".join(
            f"public.{_quote(connection, table)}" for table in LOGIN_GLOBAL_WRITE_TABLES
        )
        connection.exec_driver_sql(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            f"{quoted_global_write_tables} TO {quoted_role}"
        )


def _set_context(connection: Connection, tenant_id: str = "", *, privileged: bool = False) -> None:
    connection.execute(
        text(
            "SELECT set_config('app.current_tenant_id', :tenant_id, true), "
            "set_config('app.is_privileged', :privileged, true)"
        ),
        {"tenant_id": tenant_id, "privileged": "true" if privileged else "false"},
    )


def _count(connection: Connection, table: str, where: str = "TRUE", **params: object) -> int:
    quoted_table = _quote(connection, table)
    return int(
        connection.scalar(
            text(
                f"SELECT count(*) FROM public.{quoted_table} WHERE {where}"  # noqa: S608 - catalog-discovered and quoted.
            ),
            params,
        )
        or 0
    )


def _exercise_role(
    app_engine: Engine,
    admin_engine: Engine,
    role: str,
    tables: list[str],
    tenant_ids: tuple[str, str],
    user_ids: tuple[str, str],
) -> None:
    tenant_a, tenant_b = tenant_ids
    with admin_engine.connect() as admin_connection:
        attributes = admin_connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": role},
        ).one()
        if tuple(attributes) != (False, False, False, False, False):
            raise AssertionError(f"RLS gate role is unexpectedly privileged: {tuple(attributes)}")

    with app_engine.connect() as connection:
        transaction = connection.begin()
        try:
            if connection.scalar(text("SELECT current_user")) != role:
                raise AssertionError(
                    "RLS checks are not executing as the disposable application role"
                )

            # No tenant or privileged context must expose no tenant-owned rows at all.
            _set_context(connection)
            for table in tables:
                if _count(connection, table) != 0:
                    raise AssertionError(f"{table}: no-context session could read tenant rows")

            # A tenant context sees exactly A, cannot observe B, and cannot move its own
            # row into B because WITH CHECK must reject the new tenant ownership.
            _set_context(connection, tenant_a)
            for table in tables:
                quoted_table = _quote(connection, table)
                visible_tenants = set(
                    connection.scalars(
                        text(
                            f"SELECT tenant_id FROM public.{quoted_table}"  # noqa: S608 - catalog-discovered and quoted.
                        )
                    )
                )
                if visible_tenants != {tenant_a}:
                    raise AssertionError(
                        f"{table}: tenant A visibility was {visible_tenants}, expected only tenant A"
                    )
                if _count(connection, table, "tenant_id = :tenant_b", tenant_b=tenant_b) != 0:
                    raise AssertionError(f"{table}: tenant A read tenant B")
                hidden_update = connection.execute(
                    text(
                        f"UPDATE public.{quoted_table} SET tenant_id = tenant_id "  # noqa: S608
                        "WHERE tenant_id = :tenant_b"
                    ),
                    {"tenant_b": tenant_b},
                )
                if hidden_update.rowcount != 0:
                    raise AssertionError(f"{table}: tenant A updated tenant B")

                savepoint = connection.begin_nested()
                try:
                    connection.execute(
                        text(
                            f"UPDATE public.{quoted_table} SET tenant_id = :tenant_b "  # noqa: S608
                            "WHERE tenant_id = :tenant_a"
                        ),
                        {"tenant_a": tenant_a, "tenant_b": tenant_b},
                    )
                except DBAPIError as exc:
                    savepoint.rollback()
                    if getattr(exc.orig, "sqlstate", None) != "42501":
                        raise AssertionError(
                            f"{table}: cross-tenant write failed for a reason other than RLS"
                        ) from exc
                else:
                    savepoint.rollback()
                    raise AssertionError(f"{table}: WITH CHECK allowed a cross-tenant write")

            # The application's explicit privileged context must see both fixture tenants.
            _set_context(connection, privileged=True)
            for table in tables:
                quoted_table = _quote(connection, table)
                visible_fixture_tenants = set(
                    connection.scalars(
                        text(
                            f"SELECT tenant_id FROM public.{quoted_table} "  # noqa: S608
                            "WHERE tenant_id IN (:tenant_a, :tenant_b)"
                        ),
                        {"tenant_a": tenant_a, "tenant_b": tenant_b},
                    )
                )
                if visible_fixture_tenants != {tenant_a, tenant_b}:
                    raise AssertionError(
                        f"{table}: privileged context did not see both fixture tenants"
                    )
                result = connection.execute(
                    text(
                        f"UPDATE public.{quoted_table} SET tenant_id = tenant_id "  # noqa: S608
                        "WHERE tenant_id = :tenant_b"
                    ),
                    {"tenant_b": tenant_b},
                )
                if result.rowcount != 1:
                    raise AssertionError(f"{table}: privileged context could not write tenant B")
        finally:
            transaction.rollback()

    with admin_engine.connect() as connection:
        mapped_phone = connection.scalar(
            text("SELECT primary_phone_e164 FROM users WHERE id = :user_id"),
            {"user_id": user_ids[0]},
        )
    if not isinstance(mapped_phone, str):
        raise AssertionError("RLS login fixture user does not have a primary phone")

    session_factory = sessionmaker(bind=app_engine, expire_on_commit=False, autoflush=False)
    with session_factory() as session:
        if find_login_eligible_user(session, mapped_phone) is not None:
            raise AssertionError(
                "No-context application session resolved a tenant-mapped login identity"
            )

    auth_settings = Settings(
        app_env="test",
        auth_login_mode="whatsapp_otp",
        whatsapp_backend="mock",
        auth_rate_limit_enabled=False,
        expose_local_otp=True,
        seed_demo_data=False,
        dev_fixed_otp=None,
        local_otp_code="654321",
    )
    request_scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/auth/request-otp",
        "raw_path": b"/v1/auth/request-otp",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    with session_factory() as session:
        requested = request_otp(
            OtpRequest(phone_e164=mapped_phone),
            Request(request_scope),
            session,
            auth_settings,
        )
        if requested.dev_code != auth_settings.effective_local_otp_code:
            raise AssertionError(
                "The auth request route did not issue an OTP for the active tenant mapping"
            )

    request_scope["path"] = "/v1/auth/verify-otp"
    request_scope["raw_path"] = b"/v1/auth/verify-otp"
    with session_factory() as session:
        authenticated = verify_code(
            OtpVerify(
                phone_e164=mapped_phone,
                code=auth_settings.effective_local_otp_code,
            ),
            Request(request_scope),
            Response(),
            session,
            auth_settings,
        )
        if not authenticated.access_token or authenticated.user.get("id") != user_ids[0]:
            raise AssertionError(
                "The auth verify route did not resolve the active tenant-mapped login identity"
            )


def _cleanup(
    admin_engine: Engine,
    app_engine: Engine | None,
    role: str,
    ids_by_table: dict[str, list[str]],
    tenant_ids: tuple[str, str],
    user_ids: tuple[str, str],
) -> None:
    if app_engine is not None:
        app_engine.dispose()
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM otp_sessions WHERE phone_e164 IN "
                "(SELECT primary_phone_e164 FROM users WHERE id = ANY(:ids))"
            ),
            {"ids": list(user_ids)},
        )
        for table in reversed(Base.metadata.sorted_tables):
            row_ids = ids_by_table.get(table.name)
            if row_ids:
                quoted_table = _quote(connection, table.name)
                connection.execute(
                    text(
                        f"DELETE FROM public.{quoted_table} WHERE id = ANY(:ids)"  # noqa: S608 - metadata-derived and quoted.
                    ),
                    {"ids": row_ids},
                )
        connection.execute(
            text("DELETE FROM tenants WHERE id = ANY(:ids)"), {"ids": list(tenant_ids)}
        )
        connection.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": list(user_ids)})
        if connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"), {"role": role}
        ):
            quoted_role = _quote(connection, role)
            connection.exec_driver_sql(f"DROP OWNED BY {quoted_role}")
            connection.exec_driver_sql(f"DROP ROLE {quoted_role}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-sync-compat", action="store_true")
    parser.add_argument("--seed-bumpa-raw-compat", action="store_true")
    parser.add_argument("--verify-bumpa-raw-compat", action="store_true")
    parser.add_argument("--remove-statusless-bumpa-raw", action="store_true")
    parser.add_argument("--verify-bumpa-raw-downgrade", action="store_true")
    parser.add_argument("--verify-bumpa-sync-atomicity", action="store_true")
    args = parser.parse_args()
    admin_url = _require_postgres_url()
    admin_engine = create_engine(admin_url, pool_pre_ping=True)
    if args.seed_sync_compat:
        try:
            _seed_sync_compatibility(admin_engine)
            print("Seeded pre-0006 PostgreSQL sync-writer compatibility fixtures.")
        finally:
            admin_engine.dispose()
        return
    if args.seed_bumpa_raw_compat:
        try:
            _seed_bumpa_raw_compatibility(admin_engine)
            print("Seeded pre-0008 PostgreSQL raw-response compatibility fixture.")
        finally:
            admin_engine.dispose()
        return
    if args.verify_bumpa_raw_compat:
        try:
            _assert_bumpa_raw_failure_schema(admin_engine)
            print("PostgreSQL Bumpa raw-response evidence gate passed.")
        finally:
            admin_engine.dispose()
        return
    if args.remove_statusless_bumpa_raw:
        try:
            _remove_statusless_bumpa_evidence(admin_engine)
            print("Removed status-less Bumpa evidence for the downgrade contract.")
        finally:
            admin_engine.dispose()
        return
    if args.verify_bumpa_raw_downgrade:
        try:
            _assert_bumpa_raw_downgrade_schema(admin_engine)
            print("PostgreSQL Bumpa raw-response downgrade boundary passed.")
        finally:
            admin_engine.dispose()
        return
    if args.verify_bumpa_sync_atomicity:
        try:
            _verify_bumpa_sync_atomicity(admin_engine)
            print(
                "PostgreSQL Bumpa sync generation gate passed: atomic claims, deterministic "
                "publication ordering, failed-newer fallback, and connection rotation verified."
            )
        finally:
            admin_engine.dispose()
        return

    run_tag = secrets.token_hex(6)
    role = f"rls_gate_{run_tag}"
    password = secrets.token_hex(24)
    rows, ids_by_table, tenant_ids, user_ids = _fixture_pair(run_tag)
    app_engine: Engine | None = None

    try:
        _assert_sync_writer_compatibility(admin_engine)
        with admin_engine.connect() as connection:
            tenant_tables = _discover_tenant_tables(connection)
            _assert_schema_and_policies(connection, tenant_tables)
        fixture_tables = sorted(
            {_model_table(row).name for row in rows if "tenant_id" in _model_table(row).c}
        )
        if fixture_tables != tenant_tables:
            raise AssertionError(
                "RLS fixtures must cover every discovered tenant table: "
                f"fixtures={fixture_tables}, database={tenant_tables}"
            )

        _seed(admin_engine, rows)
        _create_role(admin_engine, role, password, tenant_tables)
        app_url = admin_url.set(username=role, password=password)
        app_engine = create_engine(app_url, pool_pre_ping=True)
        _exercise_role(app_engine, admin_engine, role, tenant_tables, tenant_ids, user_ids)
        print(
            "PostgreSQL RLS integration gate passed: "
            f"{len(tenant_tables)} tenant tables; no-context, tenant isolation, "
            "cross-tenant read/write denial, privileged context, and login eligibility verified."
        )
        print("Covered tables: " + ", ".join(tenant_tables))
    finally:
        _cleanup(admin_engine, app_engine, role, ids_by_table, tenant_ids, user_ids)
        admin_engine.dispose()


if __name__ == "__main__":
    main()
