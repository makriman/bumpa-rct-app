from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.dependencies import Principal
from app.db.base import Base
from app.db.models import (
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaOrder,
    BumpaOrderItem,
    BumpaRawResponse,
    BumpaSyncRun,
    Tenant,
    User,
)
from app.routes.admin import connect_bumpa
from app.routes.bumpa import sync_runs
from app.schemas import BumpaConnectionCreate
from app.services import bumpa as bumpa_service
from app.services.bumpa_connections import BumpaBoundaryInput, replace_bumpa_connection
from app.services.bumpa_freshness import latest_available_metrics, latest_complete_orders_run
from app.services.chat import build_business_context


def _factory(path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _successful_run(
    tenant: Tenant,
    connection: BumpaConnection,
    *,
    run_id: str,
    finished_at: datetime,
) -> BumpaSyncRun:
    return BumpaSyncRun(
        id=run_id,
        tenant_id=tenant.id,
        bumpa_connection_id=connection.id,
        boundary_revision=connection.boundary_revision,
        status="success",
        completion_quality="complete",
        requested_from=date(2026, 7, 1),
        requested_to=date(2026, 7, 14),
        started_at=datetime(2026, 7, 15, 9, tzinfo=UTC),
        finished_at=finished_at,
        orders_availability="available",
        orders_count=1,
    )


def _metric(
    tenant: Tenant,
    run: BumpaSyncRun,
    *,
    key: str,
    value: str,
) -> BumpaMetricSnapshot:
    return BumpaMetricSnapshot(
        tenant_id=tenant.id,
        sync_run_id=run.id,
        metric_key=key,
        metric_title=key,
        value_decimal=Decimal(value),
        canonical_payload={"schema_version": 1, "kind": "scalar"},
        currency_code="NGN" if key.startswith("sales.") else None,
        requested_from=run.requested_from,
        requested_to=run.requested_to,
        response_from=datetime(2026, 7, 1, tzinfo=UTC),
        response_to=datetime(2026, 7, 14, 23, 59, tzinfo=UTC),
        availability="available",
    )


def test_material_replacement_retains_audit_history_but_never_falls_back_to_store_a(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "boundary-history.db")
    with factory() as db:
        tenant = Tenant(slug="boundary-history", name="Boundary History")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="key-a",
            scope_type="business_id",
            scope_id="store-a",
            store_timezone="Africa/Lagos",
            store_currency="NGN",
            provider="bumpa",
            status="active",
            last_successful_sync_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        db.add(connection)
        db.flush()
        run_a = _successful_run(
            tenant,
            connection,
            run_id="run-store-a",
            finished_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        db.add(run_a)
        db.flush()
        order = BumpaOrder(
            tenant_id=tenant.id,
            bumpa_order_id="order-store-a",
            order_number="A-1",
            currency_code="NGN",
            total_amount=Decimal("100"),
            raw_payload={"id": "order-store-a"},
        )
        db.add_all(
            [
                _metric(
                    tenant,
                    run_a,
                    key="sales.total_sales",
                    value="100",
                ),
                BumpaRawResponse(
                    tenant_id=tenant.id,
                    sync_run_id=run_a.id,
                    resource="sales",
                    dataset="total_sales",
                    http_status=200,
                    availability="available",
                    payload={"total_sales": 100},
                ),
                order,
            ]
        )
        db.flush()
        db.add(
            BumpaOrderItem(
                tenant_id=tenant.id,
                order_id=order.id,
                name="Store A item",
                quantity=Decimal("1"),
                raw_payload={"name": "Store A item"},
            )
        )
        db.commit()

        before, _ = build_business_context(db, tenant.id)
        assert "Total sales: NGN 100.00" in before
        assert "orders in current snapshot: 1" in before

        assert replace_bumpa_connection(
            db,
            connection,
            encrypted_api_key="key-b",
            boundary=BumpaBoundaryInput(
                scope_type="business_id",
                scope_id="store-b",
                store_timezone="Europe/London",
                store_currency="GBP",
                provider="bumpa",
            ),
        )
        db.commit()

        assert connection.boundary_revision == 2
        assert connection.last_successful_sync_at is None
        assert connection.last_failed_sync_at is None
        assert db.scalar(select(func.count()).select_from(BumpaSyncRun)) == 1
        assert db.scalar(select(func.count()).select_from(BumpaMetricSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(BumpaRawResponse)) == 1
        assert db.scalar(select(func.count()).select_from(BumpaOrder)) == 0
        assert db.scalar(select(func.count()).select_from(BumpaOrderItem)) == 0
        assert latest_available_metrics(db, tenant.id) == {}
        assert latest_complete_orders_run(db, tenant.id) is None
        assert build_business_context(db, tenant.id) == (
            "No synced Bumpa metrics are available yet. Data freshness: unavailable.",
            None,
        )
        principal = Principal(
            user=User(name="Boundary owner"),
            platform_roles=frozenset(),
            memberships=(),
            membership=None,
            tenant=tenant,
        )
        assert sync_runs(principal, db) == []

        failed_b = BumpaSyncRun(
            id="failed-store-b",
            tenant_id=tenant.id,
            bumpa_connection_id=connection.id,
            boundary_revision=connection.boundary_revision,
            status="failed",
            completion_quality="failed",
            requested_from=date(2026, 7, 1),
            requested_to=date(2026, 7, 14),
            started_at=datetime(2026, 7, 15, 11, tzinfo=UTC),
            finished_at=datetime(2026, 7, 15, 11, 1, tzinfo=UTC),
            error="Store B failed",
        )
        degraded_b = BumpaSyncRun(
            id="degraded-store-b",
            tenant_id=tenant.id,
            bumpa_connection_id=connection.id,
            boundary_revision=connection.boundary_revision,
            status="partial",
            completion_quality="degraded",
            partial_reason="dataset_unavailable",
            requested_from=date(2026, 7, 1),
            requested_to=date(2026, 7, 14),
            started_at=datetime(2026, 7, 15, 12, tzinfo=UTC),
            finished_at=datetime(2026, 7, 15, 12, 1, tzinfo=UTC),
            orders_availability="unavailable",
        )
        db.add_all([failed_b, degraded_b])
        db.flush()
        db.add(
            _metric(
                tenant,
                degraded_b,
                key="products.products_sold",
                value="7",
            )
        )
        db.commit()

        visible_runs = sync_runs(principal, db)
        assert {row["id"] for row in visible_runs} == {
            "failed-store-b",
            "degraded-store-b",
        }
        assert "run-store-a" not in {row["id"] for row in visible_runs}

        current = latest_available_metrics(db, tenant.id)
        assert set(current) == {"products.products_sold"}
        assert latest_complete_orders_run(db, tenant.id) is None
        after_degraded, _ = build_business_context(db, tenant.id)
        assert "Total sales: unavailable" in after_degraded
        assert "products sold: 7" in after_degraded
        assert "orders in current snapshot: unavailable" in after_degraded
        assert "100.00" not in after_degraded


def test_verified_same_boundary_key_rotation_preserves_revision_and_current_data(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "key-rotation.db")
    with factory() as db:
        tenant = Tenant(slug="key-rotation", name="Key Rotation")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="old-key",
            scope_type="business_id",
            scope_id="same-store",
            store_timezone="Africa/Lagos",
            store_currency="NGN",
            provider="bumpa",
            status="active",
            last_successful_sync_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        db.add(connection)
        db.flush()
        run = _successful_run(
            tenant,
            connection,
            run_id="same-boundary-run",
            finished_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        order = BumpaOrder(
            tenant_id=tenant.id,
            bumpa_order_id="same-boundary-order",
            raw_payload={"id": "same-boundary-order"},
        )
        db.add_all(
            [
                run,
                order,
                _metric(tenant, run, key="sales.total_sales", value="250"),
            ]
        )
        db.commit()

        changed = replace_bumpa_connection(
            db,
            connection,
            encrypted_api_key="rotated-key",
            boundary=BumpaBoundaryInput(
                scope_type="business_id",
                scope_id="same-store",
                store_timezone="Africa/Lagos",
                store_currency="NGN",
                provider="bumpa",
            ),
        )
        db.commit()

        assert changed is False
        assert connection.boundary_revision == 1
        assert connection.last_successful_sync_at == datetime(2026, 7, 15, 10, tzinfo=UTC)
        assert db.scalar(select(func.count()).select_from(BumpaOrder)) == 1
        assert set(latest_available_metrics(db, tenant.id)) == {"sales.total_sales"}
        assert latest_complete_orders_run(db, tenant.id) is not None


def test_in_flight_store_a_publication_is_fenced_after_store_b_replacement(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "in-flight-boundary.db")
    with factory() as setup:
        tenant = Tenant(slug="in-flight-boundary", name="In Flight Boundary")
        setup.add(tenant)
        setup.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="key-a",
            scope_type="business_id",
            scope_id="store-a",
            provider="bumpa",
            status="active",
        )
        setup.add(connection)
        setup.commit()
        connection_id = connection.id

    with factory() as extraction:
        connection_a = extraction.get(BumpaConnection, connection_id)
        assert connection_a is not None
        boundary_a = bumpa_service._capture_connection_boundary(connection_a)
        generation_a = bumpa_service._claim_sync_generation(extraction, boundary_a)

        with factory() as replacement:
            current = replacement.scalar(
                select(BumpaConnection).where(BumpaConnection.id == connection_id).with_for_update()
            )
            assert current is not None
            assert replace_bumpa_connection(
                replacement,
                current,
                encrypted_api_key="key-b",
                boundary=BumpaBoundaryInput(
                    scope_type="business_id",
                    scope_id="store-b",
                    store_timezone="Africa/Lagos",
                    store_currency="NGN",
                    provider="bumpa",
                ),
            )
            replacement.commit()

        with pytest.raises(HTTPException) as captured:
            bumpa_service._claim_publication_generation(
                extraction,
                boundary_a,
                generation_a,
            )
        assert captured.value.status_code == 409
        assert captured.value.detail == "Bumpa connection changed during sync"

    with factory() as observer:
        current = observer.get(BumpaConnection, connection_id)
        assert current is not None
        assert current.scope_id == "store-b"
        assert current.boundary_revision == 2
        assert current.published_sync_generation == 0


def test_admin_writer_preserves_key_rotation_and_invalidates_material_replacement(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "admin-boundary.db")
    settings = Settings(app_env="test")
    with factory() as db:
        tenant = Tenant(slug="admin-boundary", name="Admin Boundary")
        operator = User(name="Boundary Operator", primary_phone_e164="+15550100001")
        db.add_all([tenant, operator])
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="old-encrypted-key",
            scope_type="business_id",
            scope_id="admin-store-a",
            provider="local",
            status="active",
            last_successful_sync_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        db.add(connection)
        db.flush()
        run = _successful_run(
            tenant,
            connection,
            run_id="admin-store-a-run",
            finished_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        order = BumpaOrder(
            tenant_id=tenant.id,
            bumpa_order_id="admin-store-a-order",
            raw_payload={"id": "admin-store-a-order"},
        )
        db.add_all(
            [
                run,
                order,
                _metric(tenant, run, key="sales.total_sales", value="300"),
            ]
        )
        db.commit()
        principal = Principal(
            user=operator,
            platform_roles=frozenset({"operator"}),
            memberships=(),
            membership=None,
            tenant=None,
        )

        same_boundary = connect_bumpa(
            tenant.id,
            BumpaConnectionCreate(
                api_key="rotated-local-key",
                scope_type="business_id",
                scope_id="admin-store-a",
                store_timezone="Africa/Lagos",
                store_currency="NGN",
                provider="local",
            ),
            principal,
            db,
            settings,
        )
        assert same_boundary["id"] == connection.id
        assert connection.boundary_revision == 1
        assert db.scalar(select(func.count()).select_from(BumpaOrder)) == 1
        assert set(latest_available_metrics(db, tenant.id)) == {"sales.total_sales"}

        material = connect_bumpa(
            tenant.id,
            BumpaConnectionCreate(
                api_key="new-store-local-key",
                scope_type="business_id",
                scope_id="admin-store-b",
                store_timezone="Africa/Lagos",
                store_currency="NGN",
                provider="local",
            ),
            principal,
            db,
            settings,
        )
        assert material["id"] == connection.id
        assert connection.boundary_revision == 2
        assert connection.last_successful_sync_at is None
        assert db.scalar(select(func.count()).select_from(BumpaOrder)) == 0
        assert db.scalar(select(func.count()).select_from(BumpaSyncRun)) == 1
        assert db.scalar(select(func.count()).select_from(BumpaMetricSnapshot)) == 1
        assert latest_available_metrics(db, tenant.id) == {}
