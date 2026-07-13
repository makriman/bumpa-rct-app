from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.logging import JsonFormatter
from app.db.base import Base
from app.db.models import (
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaOrder,
    BumpaOrderItem,
    BumpaRawResponse,
    BumpaSyncRun,
    HermesProfile,
    Tenant,
    User,
)
from app.providers.bumpa import BumpaProviderError, BumpaResponse, BumpaSyncResult
from app.providers.contracts import ProviderDataset, ProviderOrder
from app.providers.hermes import HermesEndpoint, HermesResult
from app.services import bumpa as bumpa_service
from app.services import chat as chat_service
from app.services.bumpa import (
    ACCEPTED_UNAVAILABLE_PROFIT_ERRORS,
    EXPECTED_SYNC_DATASETS,
    SyncCompletion,
    classify_sync_completion,
)


class JsonCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(JsonFormatter().format(record))


def _datasets(
    overrides: dict[str, tuple[str, str | None]] | None = None,
    *,
    total_sales: Decimal = Decimal("100"),
) -> list[ProviderDataset]:
    effective = overrides or {}
    datasets: list[ProviderDataset] = []
    for key in sorted(EXPECTED_SYNC_DATASETS):
        resource, dataset = key.split(".", 1)
        availability, error = effective.get(key, ("available", None))
        value = total_sales if key == "sales.total_sales" else Decimal("0")
        datasets.append(
            ProviderDataset(
                resource=resource,
                dataset=dataset,
                availability=availability,
                value=value if availability == "available" else None,
                title=key,
                error=error,
                payload={"value": str(value)} if error is None else {"error": error},
            )
        )
    return datasets


@pytest.mark.parametrize(
    ("datasets", "orders_availability", "expected"),
    [
        (_datasets(), "available", SyncCompletion("success", "complete", None, True)),
        (
            _datasets(
                {
                    key: ("unavailable", message)
                    for key, message in ACCEPTED_UNAVAILABLE_PROFIT_ERRORS.items()
                }
            ),
            "available",
            SyncCompletion("partial", "accepted_partial", "profit_not_calculable", True),
        ),
        (
            _datasets(
                {
                    "sales.gross_profit": (
                        "unavailable",
                        "Gross profit is temporarily unavailable",
                    )
                }
            ),
            "available",
            SyncCompletion("partial", "degraded", "dataset_unavailable", False),
        ),
        (
            _datasets({"products.overview": ("unavailable", "No product data")}),
            "available",
            SyncCompletion("partial", "degraded", "dataset_unavailable", False),
        ),
        (
            _datasets({"sales.gross_profit": ("error", "HTTP error")}),
            "available",
            SyncCompletion("partial", "degraded", "dataset_error", False),
        ),
        (
            _datasets(),
            "unavailable",
            SyncCompletion("partial", "degraded", "orders_unavailable", False),
        ),
        (
            _datasets()[:-1],
            "available",
            SyncCompletion("partial", "degraded", "incomplete_dataset_set", False),
        ),
        (
            [*_datasets(), _datasets()[0]],
            "available",
            SyncCompletion("partial", "degraded", "incomplete_dataset_set", False),
        ),
    ],
)
def test_sync_completion_policy_is_narrow_and_typed(
    datasets: list[ProviderDataset],
    orders_availability: str,
    expected: SyncCompletion,
) -> None:
    assert classify_sync_completion(datasets, orders_availability=orders_availability) == expected


def test_orders_error_prevents_freshness_even_when_availability_says_available() -> None:
    assert classify_sync_completion(
        _datasets(),
        orders_availability="available",
        orders_error="Orders payload was incomplete",
    ) == SyncCompletion("partial", "degraded", "orders_unavailable", False)


@pytest.mark.parametrize(
    (
        "status",
        "quality",
        "reason",
        "orders_availability",
        "orders_count",
        "error",
    ),
    [
        ("failed", "complete", None, "available", 0, "provider failed"),
        ("success", "degraded", "dataset_unavailable", "available", 0, None),
        ("partial", "accepted_partial", "profit_not_calculable", None, None, None),
        ("partial", "accepted_partial", "profit_not_calculable", None, 0, None),
        ("partial", "accepted_partial", None, "available", 0, None),
        ("partial", "accepted_partial", "dataset_unavailable", "available", 0, None),
        ("partial", "degraded", "profit_not_calculable", "available", 0, None),
        ("partial", "degraded", None, "unavailable", None, None),
        ("running", "pending", None, None, None, "unexpected error"),
    ],
)
def test_database_rejects_incoherent_sync_completion_states(
    status: str,
    quality: str,
    reason: str | None,
    orders_availability: str | None,
    orders_count: int | None,
    error: str | None,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(slug="constraint-test", name="Constraint Test")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="encrypted",
            scope_type="business_id",
            scope_id="constraint-test",
            provider="bumpa",
            status="active",
        )
        db.add(connection)
        db.flush()
        db.add(
            BumpaSyncRun(
                tenant_id=tenant.id,
                bumpa_connection_id=connection.id,
                status=status,
                completion_quality=quality,
                partial_reason=reason,
                requested_from=date(2026, 7, 1),
                requested_to=date(2026, 7, 12),
                orders_availability=orders_availability,
                orders_count=orders_count,
                error=error,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def _sync_result(
    datasets: list[ProviderDataset], orders: list[ProviderOrder] | None = None
) -> BumpaSyncResult:
    responses = [
        BumpaResponse(
            dataset.resource,
            dataset.dataset,
            200,
            dataset.payload,
            {},
            dataset.availability,
            dataset.error,
        )
        for dataset in datasets
    ]
    responses.append(BumpaResponse("orders", None, 200, {"data": []}, {}, "available", None))
    return BumpaSyncResult(datasets, orders or [], responses, None, None, "available", None)


def _order(order_id: str, *, status: str, item_name: str) -> ProviderOrder:
    return ProviderOrder(
        order_id=order_id,
        order_number=order_id,
        status=status,
        payment_status="paid",
        currency_code="NGN",
        total_amount=Decimal("25"),
        order_date=datetime(2026, 7, 10, tzinfo=UTC),
        payload={
            "id": order_id,
            "status": status,
            "items": [{"id": f"{order_id}-item", "name": item_name, "quantity": "1"}],
        },
    )


def _run_with_result(
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
    connection: BumpaConnection,
    result: BumpaSyncResult,
    field_key: str,
):
    class FakeBumpaClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeBumpaClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def sync(self, _date_from: date, _date_to: date) -> BumpaSyncResult:
            return result

    monkeypatch.setattr(bumpa_service, "BumpaClient", FakeBumpaClient)
    return bumpa_service.run_sync(
        db,
        tenant_id=connection.tenant_id,
        connection=connection,
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 12),
        field_encryption_key=field_key,
        runtime_backend="bumpa",
    )


def test_isolated_timeout_persists_degraded_evidence_without_promoting_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    field_key = "degraded-evidence-field-key"
    raw_request_id = "bumpa-degraded-request-A1B2C3D4"
    raw_body_marker = "raw-degraded-provider-body-must-never-reach-logs"
    scope_marker = "scope-secret-must-never-reach-logs"
    api_key_marker = "api-key-secret-must-never-reach-logs"

    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="degraded-evidence", name="Degraded Evidence")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=FieldCipher(field_key).encrypt(api_key_marker),
            scope_type="business_id",
            scope_id=scope_marker,
            provider="bumpa",
            status="active",
        )
        db.add(connection)
        db.commit()

        complete = _run_with_result(
            monkeypatch,
            db,
            connection,
            _sync_result(
                _datasets(total_sales=Decimal("100")),
                [_order("order-existing", status="paid", item_name="Original item")],
            ),
            field_key,
        )
        original_freshness = complete.finished_at
        original_order = db.scalar(
            select(BumpaOrder).where(BumpaOrder.bumpa_order_id == "order-existing")
        )
        assert original_order is not None

        degraded_datasets = _datasets(
            {"products.overview": ("error", "Bumpa is temporarily unreachable")},
            total_sales=Decimal("999"),
        )
        degraded_result = _sync_result(
            degraded_datasets,
            [
                _order("order-existing", status="refunded", item_name="Changed item"),
                _order("order-new", status="paid", item_name="New item"),
            ],
        )
        degraded_result.responses[:] = [
            (
                BumpaResponse(
                    response.resource,
                    response.dataset,
                    None,
                    {},
                    {},
                    "error",
                    raw_body_marker,
                    "timeout",
                    True,
                    hashlib.sha256(raw_request_id.encode()).hexdigest(),
                    4,
                )
                if response.resource == "products" and response.dataset == "overview"
                else response
            )
            for response in degraded_result.responses
        ]

        capture = JsonCapture()
        provider_logger = logging.getLogger("bumpabestie.providers")
        previous_disabled = provider_logger.disabled
        previous_level = provider_logger.level
        provider_logger.disabled = False
        provider_logger.setLevel(logging.WARNING)
        provider_logger.addHandler(capture)
        try:
            degraded = _run_with_result(
                monkeypatch,
                db,
                connection,
                degraded_result,
                field_key,
            )
        finally:
            provider_logger.removeHandler(capture)
            provider_logger.setLevel(previous_level)
            provider_logger.disabled = previous_disabled

        assert len(capture.lines) == 1
        warning = json.loads(capture.lines[0])
        assert warning == {
            "level": "WARNING",
            "logger": "bumpabestie.providers",
            "message": "bumpa_sync_degraded",
            "correlation_id": None,
            "provider": "bumpa",
            "provider_operation": "sync",
            "provider_category": "timeout",
            "provider_retryable": True,
            "provider_request_id_hash": hashlib.sha256(raw_request_id.encode()).hexdigest(),
            "retry_after_seconds": 4,
            "sync_run_id": degraded.id,
        }
        serialized_warning = capture.lines[0]
        for secret in (
            api_key_marker,
            scope_marker,
            raw_request_id,
            raw_body_marker,
            "+2348000000000",
            "123456",
        ):
            assert secret not in serialized_warning
        assert "exception" not in warning

        assert degraded.status == "partial"
        assert degraded.completion_quality == "degraded"
        assert degraded.partial_reason == "dataset_error"
        assert connection.last_successful_sync_at is not None
        assert connection.last_successful_sync_at.replace(tzinfo=None) == (
            original_freshness.replace(tzinfo=None)
        )
        assert connection.last_failed_sync_at is not None
        failed_raw = db.scalar(
            select(BumpaRawResponse).where(
                BumpaRawResponse.sync_run_id == degraded.id,
                BumpaRawResponse.resource == "products",
                BumpaRawResponse.dataset == "overview",
            )
        )
        assert failed_raw is not None
        assert failed_raw.http_status is None
        assert failed_raw.failure_kind == "timeout"
        assert failed_raw.payload == {}
        failed_metric = db.scalar(
            select(BumpaMetricSnapshot).where(
                BumpaMetricSnapshot.sync_run_id == degraded.id,
                BumpaMetricSnapshot.metric_key == "products.overview",
            )
        )
        assert failed_metric is not None
        assert failed_metric.availability == "error"
        assert failed_metric.value_decimal is None

        unchanged_order = db.scalar(
            select(BumpaOrder).where(BumpaOrder.bumpa_order_id == "order-existing")
        )
        assert unchanged_order is not None
        assert unchanged_order.status == "paid"
        assert db.scalar(select(BumpaOrder).where(BumpaOrder.bumpa_order_id == "order-new")) is None
        unchanged_item = db.scalar(
            select(BumpaOrderItem).where(BumpaOrderItem.order_id == unchanged_order.id)
        )
        assert unchanged_item is not None
        assert unchanged_item.name == "Original item"

        # Page-one orders remain run evidence only when a later page fails; they
        # cannot partially overwrite the canonical order/item boundary.
        page_failure = _sync_result(
            _datasets(total_sales=Decimal("777")),
            [_order("order-existing", status="cancelled", item_name="Partial page item")],
        )
        page_failure.responses.append(
            BumpaResponse(
                "orders",
                None,
                422,
                {},
                {},
                "error",
                "Later orders page is unavailable",
                "upstream_http",
            )
        )
        page_failure = BumpaSyncResult(
            page_failure.datasets,
            page_failure.orders,
            page_failure.responses,
            None,
            None,
            "error",
            "Later orders page is unavailable",
        )
        page_run = _run_with_result(
            monkeypatch,
            db,
            connection,
            page_failure,
            field_key,
        )
        assert page_run.completion_quality == "degraded"
        assert page_run.partial_reason == "orders_unavailable"
        db.refresh(unchanged_order)
        db.refresh(unchanged_item)
        assert unchanged_order.status == "paid"
        assert unchanged_item.name == "Original item"

        context, freshness = chat_service.build_business_context(db, tenant.id)
        assert freshness == original_freshness
        assert "Total sales: NGN 100.00" in context
        assert "999" not in context


def test_full_bumpa_provider_failure_log_is_typed_and_contains_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    field_key = "full-failure-field-key"
    api_key_marker = "api-key-secret-must-never-reach-logs"
    scope_marker = "scope-secret-must-never-reach-logs"
    raw_request_id = "bumpa-failure-request-A1B2C3D4"
    raw_body_marker = "raw-provider-body-must-never-reach-logs"
    cause_marker = "provider-cause-must-never-reach-logs"

    class FailingBumpaClient:
        def __init__(self, api_key: str, scope_type: str, scope_id: str, **_kwargs: object) -> None:
            assert api_key == api_key_marker
            assert scope_type == "business_id"
            assert scope_id == scope_marker

        def __enter__(self) -> FailingBumpaClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def sync(self, _date_from: date, _date_to: date) -> BumpaSyncResult:
            error = BumpaProviderError(
                "Bumpa is temporarily unavailable",
                status_code=503,
                retryable=True,
                failure_kind="provider",
                request_id_hash=hashlib.sha256(raw_request_id.encode()).hexdigest(),
                retry_after_seconds=7,
            )
            error.__cause__ = RuntimeError(
                f"{raw_body_marker} {cause_marker} +2348000000000 123456"
            )
            raise error

    monkeypatch.setattr(bumpa_service, "BumpaClient", FailingBumpaClient)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="full-provider-failure", name="Full Provider Failure")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=FieldCipher(field_key).encrypt(api_key_marker),
            scope_type="business_id",
            scope_id=scope_marker,
            provider="bumpa",
            status="active",
        )
        db.add(connection)
        db.commit()

        capture = JsonCapture()
        provider_logger = logging.getLogger("bumpabestie.providers")
        previous_disabled = provider_logger.disabled
        previous_level = provider_logger.level
        provider_logger.disabled = False
        provider_logger.setLevel(logging.WARNING)
        provider_logger.addHandler(capture)
        try:
            with pytest.raises(HTTPException) as raised:
                bumpa_service.run_sync(
                    db,
                    tenant_id=tenant.id,
                    connection=connection,
                    date_from=date(2026, 7, 1),
                    date_to=date(2026, 7, 12),
                    field_encryption_key=field_key,
                    runtime_backend="bumpa",
                )
        finally:
            provider_logger.removeHandler(capture)
            provider_logger.setLevel(previous_level)
            provider_logger.disabled = previous_disabled

        assert raised.value.status_code == 503
        assert raised.value.detail == "Bumpa is temporarily unavailable"
        failed_run = db.scalar(select(BumpaSyncRun))
        assert failed_run is not None
        assert failed_run.status == "failed"
        assert len(capture.lines) == 1
        warning = json.loads(capture.lines[0])
        assert warning == {
            "level": "WARNING",
            "logger": "bumpabestie.providers",
            "message": "bumpa_sync_provider_failed",
            "correlation_id": None,
            "provider": "bumpa",
            "provider_operation": "sync",
            "provider_category": "provider",
            "provider_retryable": True,
            "provider_http_status": 503,
            "provider_request_id_hash": hashlib.sha256(raw_request_id.encode()).hexdigest(),
            "retry_after_seconds": 7,
            "sync_run_id": failed_run.id,
        }
        serialized_warning = capture.lines[0]
        for secret in (
            api_key_marker,
            scope_marker,
            raw_request_id,
            raw_body_marker,
            cause_marker,
            "+2348000000000",
            "123456",
        ):
            assert secret not in serialized_warning
        assert "exception" not in warning


def test_raw_response_rejects_statusless_untyped_failure_evidence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(slug="raw-evidence", name="Raw Evidence")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="encrypted",
            scope_type="business_id",
            scope_id="business-test",
            provider="bumpa",
            status="active",
        )
        db.add(connection)
        db.flush()
        run = BumpaSyncRun(
            tenant_id=tenant.id,
            bumpa_connection_id=connection.id,
            status="running",
            requested_from=date(2026, 7, 1),
            requested_to=date(2026, 7, 12),
        )
        db.add(run)
        db.flush()
        db.add(
            BumpaRawResponse(
                tenant_id=tenant.id,
                sync_run_id=run.id,
                resource="products",
                dataset="overview",
                http_status=None,
                failure_kind=None,
                availability="error",
                error_message="untyped",
                payload={},
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_sync_fails_closed_if_connection_boundary_changes_while_waiting_for_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    field_key = "connection-fence-field-key"
    with Session(engine) as db:
        tenant = Tenant(slug="connection-fence", name="Connection Fence")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=FieldCipher(field_key).encrypt("private-bumpa-key"),
            scope_type="business_id",
            scope_id="original-scope",
            provider="bumpa",
            status="active",
        )
        db.add(connection)
        db.commit()

        original_refresh = db.refresh

        def mutate_after_lock(instance, *args, **kwargs) -> None:
            original_refresh(instance, *args, **kwargs)
            if instance is connection and kwargs.get("with_for_update") is True:
                connection.scope_id = "rotated-scope"

        monkeypatch.setattr(db, "refresh", mutate_after_lock)
        with pytest.raises(HTTPException) as raised:
            bumpa_service.run_sync(
                db,
                tenant_id=tenant.id,
                connection=connection,
                date_from=date(2026, 7, 1),
                date_to=date(2026, 7, 12),
                field_encryption_key=field_key,
                runtime_backend="bumpa",
            )
        assert raised.value.status_code == 409
        assert raised.value.detail == "Bumpa connection changed before sync started"
        db.rollback()
        assert db.scalar(select(BumpaSyncRun)) is None


def test_outer_commit_failure_falls_back_to_one_sanitized_failed_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="outer-commit-failure", name="Outer Commit Failure")
        db.add(tenant)
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key="local-only",
            scope_type="business_id",
            scope_id="outer-commit-failure",
            provider="local",
            status="active",
        )
        db.add(connection)
        db.commit()

        real_commit = db.commit
        commit_attempts = 0

        def fail_first_outer_commit() -> None:
            nonlocal commit_attempts
            commit_attempts += 1
            if commit_attempts == 1:
                raise RuntimeError("synthetic outer commit failure")
            real_commit()

        monkeypatch.setattr(db, "commit", fail_first_outer_commit)
        with pytest.raises(HTTPException) as raised:
            bumpa_service.run_sync(
                db,
                tenant_id=tenant.id,
                connection=connection,
                date_from=date(2026, 7, 1),
                date_to=date(2026, 7, 12),
            )

        assert raised.value.status_code == 502
        assert raised.value.detail == "Commerce sync failed"
        assert commit_attempts == 2
        runs = list(db.scalars(select(BumpaSyncRun)).all())
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].completion_quality == "failed"
        assert runs[0].error == "Commerce sync failed"
        assert connection.last_failed_sync_at == runs[0].finished_at
        assert connection.last_error == "Commerce sync failed"
        assert db.scalar(select(BumpaMetricSnapshot)) is None
        assert db.scalar(select(BumpaRawResponse)) is None
        assert db.scalar(select(BumpaOrder)) is None


def test_accepted_partial_persists_quality_and_hermes_gets_one_safe_usable_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    field_key = "sync-completion-field-key"
    private_bumpa_key = "private-bumpa-key-must-not-reach-hermes"
    captured: dict[str, str] = {}

    class FakeHermesClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        def respond(
            self,
            endpoint: HermesEndpoint,
            *,
            message: str,
            business_context: str,
        ) -> HermesResult:
            captured["context"] = business_context
            captured["endpoint_key"] = endpoint.api_key
            return HermesResult("safe answer", 1, 1, 2, 3)

    monkeypatch.setattr(chat_service, "HermesClient", FakeHermesClient)
    settings = Settings(
        app_env="test",
        field_encryption_key=field_key,
        agent_backend="hermes",
    )

    with factory() as db:
        tenant = Tenant(slug="sync-completion", name="Sync Completion")
        user = User(name="Owner", primary_phone_e164="+2348000000999")
        db.add_all((tenant, user))
        db.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=FieldCipher(field_key).encrypt(private_bumpa_key),
            scope_type="business_id",
            scope_id="business-test",
            provider="bumpa",
            status="active",
            last_error="older degraded sync",
        )
        profile = HermesProfile(
            tenant_id=tenant.id,
            profile_name="sync-completion",
            profile_path="/profiles/sync-completion",
            provider="hermes",
            api_internal_url="http://hermes:8700/v1",
            api_port=8700,
            encrypted_api_key=FieldCipher(field_key).encrypt("private-hermes-key"),
            status="active",
        )
        db.add_all((connection, profile))
        db.commit()

        accepted_datasets = _datasets(
            {
                key: ("unavailable", message)
                for key, message in ACCEPTED_UNAVAILABLE_PROFIT_ERRORS.items()
            },
            total_sales=Decimal("0"),
        )
        accepted = _run_with_result(
            monkeypatch,
            db,
            connection,
            _sync_result(accepted_datasets),
            field_key,
        )
        accepted_freshness = accepted.finished_at
        assert accepted.status == "partial"
        assert accepted.completion_quality == "accepted_partial"
        assert accepted.partial_reason == "profit_not_calculable"
        assert accepted.orders_availability == "available"
        assert accepted.orders_count == 0
        assert connection.last_successful_sync_at is not None
        assert connection.last_successful_sync_at.replace(
            tzinfo=None
        ) == accepted_freshness.replace(tzinfo=None)
        assert connection.last_error is None

        near_miss_error = "Gross profit cannot be calculated for another reason"
        degraded_datasets = _datasets(
            {"sales.gross_profit": ("unavailable", near_miss_error)},
            total_sales=Decimal("999"),
        )
        degraded = _run_with_result(
            monkeypatch,
            db,
            connection,
            _sync_result(degraded_datasets),
            field_key,
        )
        assert degraded.completion_quality == "degraded"
        assert degraded.partial_reason == "dataset_unavailable"
        assert connection.last_successful_sync_at is not None
        assert connection.last_successful_sync_at.replace(
            tzinfo=None
        ) == accepted_freshness.replace(tzinfo=None)
        assert connection.last_failed_sync_at is not None
        assert connection.last_failed_sync_at.replace(tzinfo=None) == degraded.finished_at.replace(
            tzinfo=None
        )

        _conversation, _incoming, _outgoing, freshness = chat_service.handle_chat(
            db,
            tenant=tenant,
            user=user,
            message="Show my numbers",
            channel="web",
            settings=settings,
        )

    context = captured["context"]
    assert freshness == accepted_freshness
    assert "Total sales: NGN 0.00" in context
    assert "gross profit: unavailable" in context
    assert "net profit: unavailable" in context
    assert "products sold: 0" in context
    assert "orders in current snapshot: 0" in context
    assert "999" not in context
    assert near_miss_error not in context
    assert all(error not in context for error in ACCEPTED_UNAVAILABLE_PROFIT_ERRORS.values())
    assert private_bumpa_key not in context
    assert captured["endpoint_key"] == "private-hermes-key"
