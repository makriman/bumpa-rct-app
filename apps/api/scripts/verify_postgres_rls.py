#!/usr/bin/env python3
"""Exercise tenant row-level security against a migrated PostgreSQL database.

This is intentionally not a pytest test: the normal unit suite uses SQLite fixtures.
CI invokes this script explicitly after Alembic has migrated its PostgreSQL service.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, TypeVar, cast

from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, RowMapping, make_url
from sqlalchemy.exc import DBAPIError

from app.db import models
from app.db.base import Base

FixtureRow = TypeVar("FixtureRow")


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
                primary_phone_e164=f"+1555000{index}{run_tag[:6]}",
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
                phone_e164=f"+1555100{index}{run_tag[:6]}",
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
                requested_from=date(2026, 1, 1),
                requested_to=date(2026, 1, 1),
                finished_at=now,
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
    admin_url = _require_postgres_url()
    admin_engine = create_engine(admin_url, pool_pre_ping=True)
    run_tag = secrets.token_hex(6)
    role = f"rls_gate_{run_tag}"
    password = secrets.token_hex(24)
    rows, ids_by_table, tenant_ids, user_ids = _fixture_pair(run_tag)
    app_engine: Engine | None = None

    try:
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
        _exercise_role(app_engine, admin_engine, role, tenant_tables, tenant_ids)
        print(
            "PostgreSQL RLS integration gate passed: "
            f"{len(tenant_tables)} tenant tables; no-context, tenant isolation, "
            "cross-tenant read/write denial, and privileged context verified."
        )
        print("Covered tables: " + ", ".join(tenant_tables))
    finally:
        _cleanup(admin_engine, app_engine, role, ids_by_table, tenant_ids, user_ids)
        admin_engine.dispose()


if __name__ == "__main__":
    main()
