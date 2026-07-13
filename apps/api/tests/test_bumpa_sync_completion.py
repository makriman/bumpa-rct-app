from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import BumpaConnection, BumpaSyncRun, HermesProfile, Tenant, User
from app.providers.bumpa import BumpaResponse, BumpaSyncResult
from app.providers.contracts import ProviderDataset
from app.providers.hermes import HermesEndpoint, HermesResult
from app.services import bumpa as bumpa_service
from app.services import chat as chat_service
from app.services.bumpa import (
    ACCEPTED_UNAVAILABLE_PROFIT_ERRORS,
    EXPECTED_SYNC_DATASETS,
    SyncCompletion,
    classify_sync_completion,
)


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


def _sync_result(datasets: list[ProviderDataset]) -> BumpaSyncResult:
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
    return BumpaSyncResult(datasets, [], responses, None, None, "available", None)


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
