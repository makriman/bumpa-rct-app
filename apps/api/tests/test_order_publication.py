from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BumpaConnection, BumpaOrder, BumpaRawResponse, Tenant
from app.providers.bumpa import BumpaProviderError, BumpaSyncResult
from app.providers.contracts import BumpaSnapshot, ProviderDataset, ProviderOrder
from app.services import bumpa
from app.services.business_tools import (
    business_overview,
    data_coverage,
    order_breakdown,
    order_publication_coverage,
    orders_publication_marker_valid,
    product_performance,
    published_orders_predicate,
    sales_trend,
)

START = date(2026, 10, 1)
END = date(2026, 10, 31)


@pytest.fixture
def store():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        tenant = Tenant(slug="publication", name="Publication", timezone="Africa/Lagos")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="synthetic-fixture",
            provider="local",
            scope_type="business_id",
            scope_id="synthetic-store",
            store_timezone="Africa/Lagos",
            store_currency="NGN",
        )
        db.add(connection)
        db.commit()
        yield db, tenant, connection
    engine.dispose()


def order(identifier="one", *, day=20, amount="100", currency="NGN", payload=None):
    return ProviderOrder(
        order_id=identifier,
        order_number=identifier,
        status="completed",
        payment_status="paid",
        currency_code=currency,
        total_amount=Decimal(amount) if amount is not None else None,
        order_date=datetime(2026, 10, day, 12, tzinfo=UTC),
        payload=payload
        or {
            "id": identifier,
            "total": amount,
            "items": [
                {"product_id": identifier, "name": identifier, "quantity": 1, "total": amount}
            ],
        },
    )


def publish(monkeypatch, db, connection, orders, start=START, end=END, *, unavailable=False):
    datasets = []
    for key in sorted(bumpa.EXPECTED_SYNC_DATASETS):
        resource, dataset = key.split(".", 1)
        datasets.append(ProviderDataset(resource, dataset, "available", {}, value=Decimal("0")))
    if unavailable:
        result = BumpaSyncResult(
            datasets, orders, [], None, None, "error", "Synthetic incomplete later page"
        )
        extracted = bumpa.ExtractedSync(result, result)
    else:
        extracted = bumpa.ExtractedSync(BumpaSnapshot(datasets, orders), None)
    monkeypatch.setattr(bumpa, "_extract_sync", lambda **kwargs: extracted)
    return bumpa.run_sync(
        db,
        tenant_id=connection.tenant_id,
        connection=connection,
        date_from=start,
        date_to=end,
    )


def overview(db, tenant, start=START, end=END):
    return business_overview(db, tenant.id, date_from=start, date_to=end, compare_to="none")[
        "metrics"
    ]


def test_complete_empty_resync_supersedes_cache_without_deleting_evidence(store, monkeypatch):
    db, tenant, connection = store
    first = publish(monkeypatch, db, connection, [order()])
    source_count = db.scalar(select(func.count(BumpaRawResponse.id)))
    assert overview(db, tenant)["sales_total"] == "100.000000"
    empty = publish(monkeypatch, db, connection, [])
    row = db.scalar(select(BumpaOrder))
    assert row.total_amount == Decimal("100")
    assert row.raw_payload["_bumpa_publication"] == {"sync_run_id": empty.id, "state": "superseded"}
    assert orders_publication_marker_valid(first)
    assert orders_publication_marker_valid(empty)
    assert db.scalar(select(func.count(BumpaRawResponse.id))) > source_count
    assert overview(db, tenant)["order_count"] == 0
    assert overview(db, tenant)["sales_total"] == "0"
    assert data_coverage(db, tenant.id)["orders"]["count"] == 0
    assert order_breakdown(db, tenant.id, date_from=START, date_to=END)["order_count"] == 0
    assert sales_trend(db, tenant.id, date_from=START, date_to=END)["points"] == []
    assert product_performance(db, tenant.id, date_from=START, date_to=END)["products"] == []


def test_overlapping_range_sweep_preserves_outside_membership(store, monkeypatch):
    db, tenant, connection = store
    publish(monkeypatch, db, connection, [order("early", day=5), order("late", day=20)])
    later = publish(monkeypatch, db, connection, [], start=date(2026, 10, 15))
    rows = {row.bumpa_order_id: row for row in db.scalars(select(BumpaOrder))}
    assert rows["early"].raw_payload["_bumpa_publication"]["state"] == "active"
    assert rows["late"].raw_payload["_bumpa_publication"] == {
        "sync_run_id": later.id,
        "state": "superseded",
    }
    assert overview(db, tenant)["order_count"] == 1
    assert overview(db, tenant)["sales_total"] == "100.000000"


def test_incomplete_pull_cannot_infer_absence_or_refresh_prior_publication(store, monkeypatch):
    db, tenant, connection = store
    first = publish(monkeypatch, db, connection, [order()])
    incomplete = publish(monkeypatch, db, connection, [], unavailable=True)
    assert incomplete.orders_count is None
    assert not orders_publication_marker_valid(incomplete)
    assert (
        db.scalar(select(BumpaOrder)).raw_payload["_bumpa_publication"]["sync_run_id"] == first.id
    )
    assert overview(db, tenant)["sales_total"] == "100.000000"
    assert (
        order_publication_coverage(db, tenant.id)["verified_periods"][0]["sync_run_id"] == first.id
    )
    with pytest.raises(ValueError):
        bumpa._promote_orders(db, tenant.id, incomplete.id, [], record_raw_responses=False)


@pytest.mark.parametrize("invalid", ["undated", "outside", "currency", "duplicate"])
def test_unbounded_membership_fails_closed_without_sweeping(store, monkeypatch, invalid):
    db, tenant, connection = store
    first = publish(monkeypatch, db, connection, [order()])
    candidate = order("candidate")
    orders = [candidate]
    if invalid == "undated":
        orders = [replace(candidate, order_date=None)]
    elif invalid == "outside":
        orders = [replace(candidate, order_date=datetime(2026, 11, 1, 12, tzinfo=UTC))]
    elif invalid == "currency":
        orders = [replace(candidate, currency_code="USD")]
    else:
        orders = [candidate, candidate]
    rejected = publish(monkeypatch, db, connection, orders)
    assert rejected.orders_availability == "error"
    assert rejected.orders_count is None
    assert not orders_publication_marker_valid(rejected)
    assert (
        db.scalar(select(BumpaOrder)).raw_payload["_bumpa_publication"]["sync_run_id"] == first.id
    )
    assert overview(db, tenant)["order_count"] == 1


def test_provider_cannot_forge_publication_marker(store, monkeypatch):
    db, tenant, connection = store
    run = publish(
        monkeypatch,
        db,
        connection,
        [
            order(
                payload={
                    "id": "one",
                    "_bumpa_publication": {"state": "superseded", "sync_run_id": "forged"},
                }
            )
        ],
    )
    assert db.scalar(select(BumpaOrder)).raw_payload["_bumpa_publication"] == {
        "state": "active",
        "sync_run_id": run.id,
    }


def test_legacy_rows_require_new_verified_sync_including_empty(store, monkeypatch):
    db, tenant, connection = store
    legacy = BumpaOrder(
        tenant_id=tenant.id,
        bumpa_order_id="legacy",
        order_date=order().order_date,
        total_amount=Decimal("100"),
        currency_code="NGN",
        raw_payload={},
    )
    db.add(legacy)
    db.commit()
    assert data_coverage(db, tenant.id)["orders"]["count"] is None
    assert overview(db, tenant)["availability"] == "unavailable"
    assert overview(db, tenant)["order_count"] is None
    publish(monkeypatch, db, connection, [])
    assert overview(db, tenant)["order_count"] == 0
    assert legacy.raw_payload["_bumpa_publication"]["state"] == "superseded"


def test_old_generation_cannot_republish_after_newer_success(store, monkeypatch):
    db, tenant, connection = store
    old = publish(monkeypatch, db, connection, [order()])
    publish(monkeypatch, db, connection, [])
    with pytest.raises(ValueError):
        bumpa._promote_orders(db, tenant.id, old.id, [order()], record_raw_responses=False)
    assert overview(db, tenant)["order_count"] == 0


def test_failed_newer_pull_preserves_prior_dated_evidence(store, monkeypatch):
    db, tenant, connection = store
    first = publish(monkeypatch, db, connection, [order()])

    def fail(**kwargs):
        raise BumpaProviderError("synthetic failure", failure_kind="invalid_response")

    monkeypatch.setattr(bumpa, "_extract_sync", fail)
    with pytest.raises(HTTPException):
        bumpa.run_sync(db, tenant_id=tenant.id, connection=connection, date_from=START, date_to=END)
    assert overview(db, tenant)["sales_total"] == "100.000000"
    assert (
        order_publication_coverage(db, tenant.id)["verified_periods"][0]["sync_run_id"] == first.id
    )


def test_connection_boundary_and_currency_replacement_do_not_adopt_old_rows(store, monkeypatch):
    db, tenant, connection = store
    publish(monkeypatch, db, connection, [order()])
    connection.boundary_revision += 1
    connection.scope_id = "replacement-store"
    connection.store_currency = "USD"
    connection.sync_generation = connection.published_sync_generation = 0
    db.commit()
    assert overview(db, tenant)["availability"] == "unavailable"
    publish(monkeypatch, db, connection, [order("new", currency="USD", amount="5")])
    assert overview(db, tenant)["sales_total"] == "5.000000"
    assert db.scalar(select(func.count(BumpaOrder.id))) == 2
    assert (
        len(db.scalars(select(BumpaOrder).where(published_orders_predicate(tenant.id))).all()) == 1
    )


def test_gaps_are_unknown_and_missing_amounts_never_become_zero(store, monkeypatch):
    db, tenant, connection = store
    publish(monkeypatch, db, connection, [order(amount=None)], start=date(2026, 10, 15))
    assert overview(db, tenant)["order_count"] is None
    covered = overview(db, tenant, start=date(2026, 10, 15))
    assert covered["order_count"] == 1
    assert covered["sales_total"] is None


def test_sweep_uses_store_local_midnight_and_preserves_outside_legacy(store, monkeypatch):
    db, tenant, connection = store
    outside = BumpaOrder(
        tenant_id=tenant.id,
        bumpa_order_id="outside",
        currency_code="NGN",
        order_date=datetime(2026, 10, 31, 22, 30, tzinfo=UTC),
        raw_payload={},
    )
    db.add(outside)
    db.commit()
    inside = replace(order("inside"), order_date=datetime(2026, 10, 31, 23, 30, tzinfo=UTC))
    day = date(2026, 11, 1)
    publish(monkeypatch, db, connection, [inside], start=day, end=day)
    publish(monkeypatch, db, connection, [], start=day, end=day)
    assert outside.raw_payload == {}
    assert overview(db, tenant, start=day, end=day)["order_count"] == 0


@pytest.mark.parametrize("second_start,available", [(16, True), (17, False)])
def test_composite_coverage_requires_every_requested_day(
    store, monkeypatch, second_start, available
):
    db, tenant, connection = store
    publish(monkeypatch, db, connection, [order(day=5)], end=date(2026, 10, 15))
    publish(monkeypatch, db, connection, [], start=date(2026, 10, second_start))
    result = overview(db, tenant)
    assert result["availability"] == ("available" if available else "unavailable")
    assert result["order_count"] == (1 if available else None)


def test_current_publications_remain_tenant_scoped(store, monkeypatch):
    db, tenant, connection = store
    publish(monkeypatch, db, connection, [order()])
    other = Tenant(slug="publication-other", name="Other")
    db.add(other)
    db.flush()
    other_connection = BumpaConnection(
        tenant_id=other.id,
        encrypted_api_key="synthetic",
        provider="local",
        scope_type="business_id",
        scope_id="other-store",
    )
    db.add(other_connection)
    db.commit()
    publish(monkeypatch, db, other_connection, [order("other", amount="999")])
    assert overview(db, tenant)["sales_total"] == "100.000000"
    assert overview(db, other)["sales_total"] == "999.000000"
