from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.models import (
    AsyncJob,
    AuditLog,
    BumpaConnection,
    BumpaOrder,
    BumpaSyncRun,
    HermesProfile,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    TenantOnboarding,
    User,
)
from app.db.session import SessionLocal
from app.onboarding_contracts import (
    OnboardingBumpaRequest,
    OnboardingCompleteRequest,
    OnboardingError,
    OnboardingHermesRequest,
    OnboardingInitialSyncAcceptRequest,
    OnboardingInitialSyncRequest,
    OnboardingOwnerRequest,
    OnboardingPhoneRequest,
    OnboardingStartRequest,
    OnboardingView,
)
from app.services.onboarding import OnboardingService
from app.services.production_readiness import ProductionReadiness, ProviderSelectors

OPERATOR_PHONE = "+2348099990001"


@dataclass(frozen=True)
class SagaInput:
    token: str
    actor_user_id: str
    start: OnboardingStartRequest
    owner: OnboardingOwnerRequest
    phone: OnboardingPhoneRequest
    bumpa: OnboardingBumpaRequest
    api_key: str

    def key(self, command: str) -> str:
        return f"saga-{self.token}-{command}-idempotency"


@pytest.fixture
def db(client: TestClient) -> Generator[Session, None, None]:
    # Entering the session-scoped TestClient runs schema creation and the
    # deterministic local seed before these direct service calls.
    del client
    with SessionLocal() as session:
        yield session


@pytest.fixture
def service() -> OnboardingService:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "whatsapp_backend": "mock",
            "bumpa_backend": "mock",
            "agent_backend": "mock",
        }
    )

    def ready(_settings: Settings) -> ProductionReadiness:
        return ProductionReadiness(
            ready=True,
            database="ok",
            async_runtime={"enabled": False},
            providers=ProviderSelectors(
                whatsapp="mock",
                bumpa="mock",
                agent="mock",
            ),
        )

    return OnboardingService(settings, readiness_checker=ready)


def test_field_encryption_rotation_does_not_change_onboarding_command_identity(
    service: OnboardingService,
) -> None:
    actor_id = "operator-stable-identity"
    idempotency_key = "stable-command-key"
    before = service._key_hash(actor_id, idempotency_key)

    rotated_field_cipher = OnboardingService(
        service.settings.model_copy(
            update={"field_encryption_key": "rotated-field-key-" + "x" * 32}
        )
    )
    assert rotated_field_cipher._key_hash(actor_id, idempotency_key) == before

    rotated_integrity_key = OnboardingService(
        service.settings.model_copy(
            update={"onboarding_integrity_key": "rotated-integrity-" + "y" * 32}
        )
    )
    assert rotated_integrity_key._key_hash(actor_id, idempotency_key) != before


def _operator(db: Session) -> User:
    operator = db.scalar(select(User).where(User.primary_phone_e164 == OPERATOR_PHONE))
    assert operator is not None
    return operator


def _input(db: Session, *, owner: User | None = None) -> SagaInput:
    token = uuid4().hex[:12]
    actor = _operator(db)
    phone = owner.primary_phone_e164 if owner is not None else f"+1555{int(token, 16) % 10**7:07d}"
    name = owner.name if owner is not None else f"Synthetic Owner {token[:6]}"
    # email-validator intentionally rejects reserved `.test` domains. The
    # dual-role fixture may already contain one from the seed, so omit it from
    # that command rather than attempting to rewrite the shared user.
    email = None if owner is not None else f"owner-{token}@example.com"
    assert name is not None
    api_key = f"synthetic-bumpa-key-{token}"
    return SagaInput(
        token=token,
        actor_user_id=actor.id,
        start=OnboardingStartRequest(
            slug=f"saga-{token}",
            name=f"Synthetic Store {token}",
            business_category="retail",
            country="NG",
            city="Lagos",
            timezone="Africa/Lagos",
            currency_code="NGN",
        ),
        owner=OnboardingOwnerRequest(name=name, phone_e164=phone, email=email),
        phone=OnboardingPhoneRequest(confirmation="approve", label="Owner"),
        bumpa=OnboardingBumpaRequest(
            api_key=SecretStr(api_key),
            scope_type="business_id",
            scope_id=f"business-{token}",
            store_timezone="Africa/Lagos",
            store_currency="NGN",
        ),
        api_key=api_key,
    )


def _start_to_sync(
    service: OnboardingService,
    db: Session,
    data: SagaInput,
) -> tuple[OnboardingView, OnboardingView, OnboardingView, OnboardingView, OnboardingView]:
    started = service.start(
        db,
        data.start,
        actor_user_id=data.actor_user_id,
        idempotency_key=data.key("start"),
    ).view
    owner = service.set_owner(
        db,
        started.id,
        data.owner,
        actor_user_id=data.actor_user_id,
        expected_revision=started.revision,
        idempotency_key=data.key("owner"),
    ).view
    phone = service.approve_phone(
        db,
        started.id,
        data.phone,
        actor_user_id=data.actor_user_id,
        expected_revision=owner.revision,
        idempotency_key=data.key("phone"),
    ).view
    bumpa = service.connect_bumpa(
        db,
        started.id,
        data.bumpa,
        actor_user_id=data.actor_user_id,
        expected_revision=phone.revision,
        idempotency_key=data.key("bumpa"),
    ).view
    assert bumpa.bumpa is not None
    assert bumpa.bumpa.store_timezone == data.bumpa.store_timezone
    assert bumpa.bumpa.store_currency == data.bumpa.store_currency
    sync = service.trigger_initial_sync(
        db,
        started.id,
        OnboardingInitialSyncRequest(
            date_from=utcnow().date() - timedelta(days=30),
            date_to=utcnow().date(),
        ),
        actor_user_id=data.actor_user_id,
        expected_revision=bumpa.revision,
        idempotency_key=data.key("sync-1"),
    ).view
    assert sync.initial_sync is not None
    sync_job = db.get(AsyncJob, sync.initial_sync.job_id)
    assert sync_job is not None
    connection = db.get(BumpaConnection, bumpa.bumpa.connection_id)
    assert connection is not None
    assert sync_job.payload["boundary_revision"] == connection.boundary_revision
    return started, owner, phone, bumpa, sync


def _finish_sync(
    db: Session,
    view: OnboardingView,
    *,
    quality: str = "complete",
    requested_offset_days: int = 0,
) -> BumpaSyncRun:
    assert view.bumpa is not None
    assert view.initial_sync is not None
    job = db.get(AsyncJob, view.initial_sync.job_id)
    assert job is not None
    requested_from = view.initial_sync.requested_from + timedelta(days=requested_offset_days)
    requested_to = view.initial_sync.requested_to + timedelta(days=requested_offset_days)
    if quality == "complete":
        status = "success"
        partial_reason = None
        orders_availability = "available"
        orders_count = 11
    else:
        status = "partial"
        partial_reason = "orders_unavailable"
        orders_availability = "unavailable"
        orders_count = None
    run = BumpaSyncRun(
        tenant_id=view.tenant_id,
        bumpa_connection_id=view.bumpa.connection_id,
        status=status,
        completion_quality=quality,
        partial_reason=partial_reason,
        requested_from=requested_from,
        requested_to=requested_to,
        finished_at=utcnow(),
        error=None,
        orders_availability=orders_availability,
        orders_count=orders_count,
        dataset_results={},
    )
    db.add(run)
    db.flush()
    job.status = "succeeded"
    job.result = {
        "sync_run_id": run.id,
        "status": run.status,
        "completion_quality": run.completion_quality,
        "partial_reason": run.partial_reason,
        "orders_availability": run.orders_availability,
        "orders_count": run.orders_count,
    }
    job.finished_at = utcnow()
    db.commit()
    return run


def _accept_to_review(
    service: OnboardingService,
    db: Session,
    data: SagaInput,
    sync: OnboardingView,
) -> tuple[OnboardingView, OnboardingView]:
    _finish_sync(db, sync)
    accepted = service.accept_initial_sync(
        db,
        sync.id,
        OnboardingInitialSyncAcceptRequest(confirmation="accept"),
        actor_user_id=data.actor_user_id,
        expected_revision=sync.revision,
        idempotency_key=data.key("sync-accept"),
    ).view
    review = service.provision_hermes(
        db,
        sync.id,
        OnboardingHermesRequest(confirmation="provision"),
        actor_user_id=data.actor_user_id,
        expected_revision=accepted.revision,
        idempotency_key=data.key("hermes"),
    ).view
    return accepted, review


def _assert_error(code: str, operation: object) -> None:
    assert isinstance(operation, OnboardingError)
    assert operation.code == code


def test_full_local_saga_replays_without_duplicate_resources_or_audits(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    started, owner, _phone, _bumpa, sync = _start_to_sync(service, db, data)
    _accepted, review = _accept_to_review(service, db, data, sync)
    completion_request = OnboardingCompleteRequest(confirmation="activate")
    completed = service.complete(
        db,
        started.id,
        completion_request,
        actor_user_id=data.actor_user_id,
        expected_revision=review.revision,
        idempotency_key=data.key("complete"),
    )

    assert completed.view.status == "completed"
    assert completed.view.current_step == "completed"
    assert completed.view.tenant.status == "active"
    assert completed.view.owner is not None
    assert completed.view.phone is not None
    assert completed.view.bumpa is not None
    assert completed.view.initial_sync is not None
    assert completed.view.hermes is not None

    complete_replay = service.complete(
        db,
        started.id,
        completion_request,
        actor_user_id=data.actor_user_id,
        expected_revision=review.revision,
        idempotency_key=data.key("complete"),
    )
    owner_replay = service.set_owner(
        db,
        started.id,
        data.owner,
        actor_user_id=data.actor_user_id,
        expected_revision=started.revision,
        idempotency_key=data.key("owner"),
    )
    start_replay = service.start(
        db,
        data.start,
        actor_user_id=data.actor_user_id,
        idempotency_key=data.key("start"),
    )

    assert complete_replay.replayed is True
    assert owner_replay.replayed is True
    assert start_replay.replayed is True
    assert {
        complete_replay.view.id,
        owner_replay.view.id,
        start_replay.view.id,
    } == {started.id}
    assert {
        complete_replay.view.tenant_id,
        owner_replay.view.tenant_id,
        start_replay.view.tenant_id,
    } == {started.tenant_id}
    assert owner_replay.view.owner is not None
    assert owner_replay.view.owner.user_id == owner.owner.user_id  # type: ignore[union-attr]

    audits = list(db.scalars(select(AuditLog).where(AuditLog.tenant_id == started.tenant_id)).all())
    actions = [event.action for event in audits]
    for action in (
        "tenant.created",
        "tenant.onboarding.started",
        "tenant.onboarding.owner_saved",
        "phone.approved",
        "tenant.onboarding.bumpa_connected",
        "tenant.onboarding.initial_sync_requested",
        "tenant.onboarding.initial_sync_accepted",
        "hermes.profile.created",
        "tenant.onboarding.completed",
    ):
        assert actions.count(action) == 1

    saga = db.get(TenantOnboarding, started.id)
    assert saga is not None
    canonical_start = json.dumps(
        data.start.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    plain_start_fingerprint = hashlib.sha256(canonical_start.encode()).hexdigest()
    plain_start_key_hash = hashlib.sha256(
        f"onboarding:v1:{data.actor_user_id}:{data.key('start')}".encode()
    ).hexdigest()
    assert saga.start_fingerprint != plain_start_fingerprint
    assert saga.start_idempotency_key_hash != plain_start_key_hash
    saga_text = json.dumps(
        {column.name: getattr(saga, column.name) for column in saga.__table__.columns},
        default=str,
        sort_keys=True,
    )
    audit_text = json.dumps(
        [
            {
                "action": event.action,
                "before": event.before,
                "after": event.after,
            }
            for event in audits
        ],
        default=str,
        sort_keys=True,
    )
    view_text = completed.view.model_dump_json()
    for raw_value in (
        data.owner.phone_e164,
        str(data.owner.email),
        data.api_key,
        *(data.key(command) for command in ("start", "owner", "phone", "bumpa", "complete")),
    ):
        assert raw_value not in saga_text
        assert raw_value not in audit_text
        assert raw_value not in view_text

    connection = db.get(BumpaConnection, completed.view.bumpa.connection_id)
    assert connection is not None
    assert connection.encrypted_api_key != data.api_key
    assert (
        FieldCipher(service.settings.field_encryption_key).decrypt(connection.encrypted_api_key)
        == data.api_key
    )


def test_changed_bumpa_secret_with_same_key_is_an_idempotency_conflict(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    _started, _owner, _phone, bumpa, _sync = _start_to_sync(service, db, data)
    changed = data.bumpa.model_copy(update={"api_key": SecretStr("different-synthetic-key")})

    with pytest.raises(OnboardingError) as captured:
        service.connect_bumpa(
            db,
            bumpa.id,
            changed,
            actor_user_id=data.actor_user_id,
            expected_revision=bumpa.revision - 1,
            idempotency_key=data.key("bumpa"),
        )

    _assert_error("idempotency_conflict", captured.value)


def test_resumable_onboarding_rejects_material_change_but_rotates_same_boundary_key(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    started = service.start(
        db,
        data.start,
        actor_user_id=data.actor_user_id,
        idempotency_key=data.key("start"),
    ).view
    owner = service.set_owner(
        db,
        started.id,
        data.owner,
        actor_user_id=data.actor_user_id,
        expected_revision=started.revision,
        idempotency_key=data.key("owner"),
    ).view
    phone = service.approve_phone(
        db,
        started.id,
        data.phone,
        actor_user_id=data.actor_user_id,
        expected_revision=owner.revision,
        idempotency_key=data.key("phone"),
    ).view
    prior_freshness = utcnow()
    existing = BumpaConnection(
        tenant_id=started.tenant_id,
        encrypted_api_key=FieldCipher(service.settings.field_encryption_key).encrypt(
            "prior-synthetic-key"
        ),
        scope_type=data.bumpa.scope_type,
        scope_id=data.bumpa.scope_id,
        store_timezone=data.bumpa.store_timezone,
        store_currency=data.bumpa.store_currency,
        provider="local",
        status="active",
        last_successful_sync_at=prior_freshness,
    )
    db.add(existing)
    db.flush()
    db.add(
        BumpaOrder(
            tenant_id=started.tenant_id,
            bumpa_order_id=f"onboarding-order-{data.token}",
            raw_payload={"id": f"onboarding-order-{data.token}"},
        )
    )
    db.commit()

    conflicting = data.bumpa.model_copy(update={"scope_id": f"other-{data.token}"})
    with pytest.raises(OnboardingError) as captured:
        service.connect_bumpa(
            db,
            started.id,
            conflicting,
            actor_user_id=data.actor_user_id,
            expected_revision=phone.revision,
            idempotency_key=data.key("bumpa-conflict"),
        )
    _assert_error("bumpa_connection_conflict", captured.value)
    db.rollback()

    connected = service.connect_bumpa(
        db,
        started.id,
        data.bumpa,
        actor_user_id=data.actor_user_id,
        expected_revision=phone.revision,
        idempotency_key=data.key("bumpa"),
    ).view
    current = db.get(BumpaConnection, existing.id)
    assert connected.bumpa is not None
    assert connected.bumpa.connection_id == existing.id
    assert current is not None
    assert current.boundary_revision == 1
    assert current.last_successful_sync_at is not None
    assert current.last_successful_sync_at.replace(tzinfo=None) == prior_freshness.replace(
        tzinfo=None
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(BumpaOrder)
            .where(
                BumpaOrder.tenant_id == started.tenant_id,
                BumpaOrder.bumpa_order_id == f"onboarding-order-{data.token}",
            )
        )
        == 1
    )
    assert (
        FieldCipher(service.settings.field_encryption_key).decrypt(current.encrypted_api_key)
        == data.api_key
    )


def test_platform_operator_can_be_owner_without_role_mutation(
    db: Session,
    service: OnboardingService,
) -> None:
    operator = _operator(db)
    before_roles = tuple(
        db.scalars(
            select(PlatformRole.role)
            .where(PlatformRole.user_id == operator.id)
            .order_by(PlatformRole.role)
        ).all()
    )
    data = _input(db, owner=operator)
    started = service.start(
        db,
        data.start,
        actor_user_id=operator.id,
        idempotency_key=data.key("start"),
    ).view
    owner = service.set_owner(
        db,
        started.id,
        data.owner,
        actor_user_id=operator.id,
        expected_revision=started.revision,
        idempotency_key=data.key("owner"),
    ).view

    assert owner.owner is not None
    assert owner.owner.user_id == operator.id
    membership = db.get(TenantMembership, owner.owner.membership_id)
    assert membership is not None
    assert membership.role == "owner"
    assert membership.status == "active"
    assert (
        db.scalar(
            select(func.count()).select_from(User).where(User.primary_phone_e164 == OPERATOR_PHONE)
        )
        == 1
    )
    after_roles = tuple(
        db.scalars(
            select(PlatformRole.role)
            .where(PlatformRole.user_id == operator.id)
            .order_by(PlatformRole.role)
        ).all()
    )
    assert after_roles == before_roles


def test_out_of_order_stale_and_changed_replay_commands_fail_closed(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    started = service.start(
        db,
        data.start,
        actor_user_id=data.actor_user_id,
        idempotency_key=data.key("start"),
    ).view

    with pytest.raises(OnboardingError) as out_of_order:
        service.approve_phone(
            db,
            started.id,
            data.phone,
            actor_user_id=data.actor_user_id,
            expected_revision=started.revision,
            idempotency_key=data.key("phone"),
        )
    _assert_error("invalid_step", out_of_order.value)

    db.rollback()
    with pytest.raises(OnboardingError) as stale:
        service.set_owner(
            db,
            started.id,
            data.owner,
            actor_user_id=data.actor_user_id,
            expected_revision=started.revision + 1,
            idempotency_key=data.key("owner"),
        )
    _assert_error("revision_conflict", stale.value)

    db.rollback()
    service.set_owner(
        db,
        started.id,
        data.owner,
        actor_user_id=data.actor_user_id,
        expected_revision=started.revision,
        idempotency_key=data.key("owner"),
    )
    changed_owner = data.owner.model_copy(update={"name": "Changed Synthetic Owner"})
    with pytest.raises(OnboardingError) as changed_replay:
        service.set_owner(
            db,
            started.id,
            changed_owner,
            actor_user_id=data.actor_user_id,
            expected_revision=started.revision,
            idempotency_key=data.key("owner"),
        )
    _assert_error("idempotency_conflict", changed_replay.value)


def test_dead_letter_and_degraded_syncs_allow_new_controlled_attempts(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    _started, _owner, _phone, _bumpa, first = _start_to_sync(service, db, data)
    assert first.initial_sync is not None
    first_job = db.get(AsyncJob, first.initial_sync.job_id)
    assert first_job is not None
    first_job.status = "dead_letter"
    first_job.last_error = "synthetic terminal failure"
    first_job.finished_at = utcnow()
    db.commit()

    with pytest.raises(OnboardingError) as dead_letter:
        service.accept_initial_sync(
            db,
            first.id,
            OnboardingInitialSyncAcceptRequest(confirmation="accept"),
            actor_user_id=data.actor_user_id,
            expected_revision=first.revision,
            idempotency_key=data.key("accept-dead"),
        )
    _assert_error("initial_sync_not_ready", dead_letter.value)
    after_dead = service.get(db, first.id)
    assert after_dead.status == "attention_required"

    second = service.trigger_initial_sync(
        db,
        first.id,
        OnboardingInitialSyncRequest(
            date_from=first.initial_sync.requested_from,
            date_to=first.initial_sync.requested_to,
        ),
        actor_user_id=data.actor_user_id,
        expected_revision=after_dead.revision,
        idempotency_key=data.key("sync-2"),
    ).view
    assert second.initial_sync is not None
    assert second.initial_sync.attempt == 2
    assert second.initial_sync.job_id != first.initial_sync.job_id
    _finish_sync(db, second, quality="degraded")

    with pytest.raises(OnboardingError) as degraded:
        service.accept_initial_sync(
            db,
            first.id,
            OnboardingInitialSyncAcceptRequest(confirmation="accept"),
            actor_user_id=data.actor_user_id,
            expected_revision=second.revision,
            idempotency_key=data.key("accept-degraded"),
        )
    _assert_error("initial_sync_not_ready", degraded.value)
    after_degraded = service.get(db, first.id)

    third = service.trigger_initial_sync(
        db,
        first.id,
        OnboardingInitialSyncRequest(
            date_from=second.initial_sync.requested_from,
            date_to=second.initial_sync.requested_to,
        ),
        actor_user_id=data.actor_user_id,
        expected_revision=after_degraded.revision,
        idempotency_key=data.key("sync-3"),
    ).view
    assert third.initial_sync is not None
    assert third.initial_sync.attempt == 3
    assert third.initial_sync.job_id not in {
        first.initial_sync.job_id,
        second.initial_sync.job_id,
    }


def test_accept_rejects_usable_run_from_a_different_requested_window(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    _started, _owner, _phone, _bumpa, sync = _start_to_sync(service, db, data)
    _finish_sync(db, sync, requested_offset_days=1)

    with pytest.raises(OnboardingError) as captured:
        service.accept_initial_sync(
            db,
            sync.id,
            OnboardingInitialSyncAcceptRequest(confirmation="accept"),
            actor_user_id=data.actor_user_id,
            expected_revision=sync.revision,
            idempotency_key=data.key("accept-window-mismatch"),
        )

    _assert_error("initial_sync_not_ready", captured.value)


def test_completion_rechecks_phone_and_exact_async_job_invariants(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    started, _owner, _phone, _bumpa, sync = _start_to_sync(service, db, data)
    _accepted, review = _accept_to_review(service, db, data, sync)
    assert review.phone is not None
    assert review.initial_sync is not None

    phone = db.get(PhoneIdentity, review.phone.identity_id)
    assert phone is not None
    original_phone_e164 = phone.phone_e164
    phone.opt_out = True
    db.commit()
    with pytest.raises(OnboardingError) as opted_out:
        service.complete(
            db,
            started.id,
            OnboardingCompleteRequest(confirmation="activate"),
            actor_user_id=data.actor_user_id,
            expected_revision=review.revision,
            idempotency_key=data.key("complete-opted-out"),
        )
    _assert_error("completion_requirements_not_met", opted_out.value)

    db.rollback()
    phone = db.get(PhoneIdentity, review.phone.identity_id)
    assert phone is not None
    phone.opt_out = False
    phone.phone_e164 = f"+1558{int(data.token, 16) % 10**7:07d}"
    db.commit()
    with pytest.raises(OnboardingError) as mismatched_phone:
        service.complete(
            db,
            started.id,
            OnboardingCompleteRequest(confirmation="activate"),
            actor_user_id=data.actor_user_id,
            expected_revision=review.revision,
            idempotency_key=data.key("complete-mismatched-phone"),
        )
    _assert_error("completion_requirements_not_met", mismatched_phone.value)

    db.rollback()
    phone = db.get(PhoneIdentity, review.phone.identity_id)
    assert phone is not None
    phone.phone_e164 = original_phone_e164
    job = db.get(AsyncJob, review.initial_sync.job_id)
    assert job is not None
    job.status = "dead_letter"
    db.commit()
    with pytest.raises(OnboardingError) as stale_job:
        service.complete(
            db,
            started.id,
            OnboardingCompleteRequest(confirmation="activate"),
            actor_user_id=data.actor_user_id,
            expected_revision=review.revision,
            idempotency_key=data.key("complete-stale-job"),
        )
    _assert_error("completion_requirements_not_met", stale_job.value)

    db.rollback()
    job = db.get(AsyncJob, review.initial_sync.job_id)
    assert job is not None
    job.status = "succeeded"
    job.payload = {key: value for key, value in job.payload.items() if key != "date_from"}
    db.commit()
    with pytest.raises(OnboardingError) as malformed_job:
        service.complete(
            db,
            started.id,
            OnboardingCompleteRequest(confirmation="activate"),
            actor_user_id=data.actor_user_id,
            expected_revision=review.revision,
            idempotency_key=data.key("complete-malformed-job-window"),
        )
    _assert_error("completion_requirements_not_met", malformed_job.value)

    tenant = db.get(Tenant, started.tenant_id)
    saga = db.get(TenantOnboarding, started.id)
    assert tenant is not None and tenant.status == "provisioning"
    assert saga is not None and saga.current_step == "review"
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.tenant_id == started.tenant_id,
                AuditLog.action == "tenant.onboarding.completed",
            )
        )
        == 0
    )


@pytest.mark.parametrize(
    "failure",
    ["runtime", "runtime_exception", "async_runtime", "providers"],
)
def test_start_preflight_failure_leaves_no_tenant_saga_or_audit_residue(
    db: Session,
    failure: str,
) -> None:
    data = _input(db)
    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "whatsapp_backend": "disabled",
            "bumpa_backend": "disabled",
            "agent_backend": "disabled",
        }
    )
    readiness = ProductionReadiness(
        ready=failure in {"async_runtime", "providers"},
        database="ok",
        async_runtime={"enabled": failure == "providers"},
        providers=ProviderSelectors(
            whatsapp="disabled",
            bumpa="disabled",
            agent="disabled",
        ),
    )

    def check_readiness(_settings: Settings) -> ProductionReadiness:
        if failure == "runtime_exception":
            raise OSError("synthetic readiness failure")
        return readiness

    production_service = OnboardingService(settings, readiness_checker=check_readiness)
    counts_before = (
        db.scalar(select(func.count()).select_from(Tenant)),
        db.scalar(select(func.count()).select_from(TenantOnboarding)),
        db.scalar(select(func.count()).select_from(AuditLog)),
    )

    with pytest.raises(OnboardingError) as captured:
        production_service.start(
            db,
            data.start,
            actor_user_id=data.actor_user_id,
            idempotency_key=data.key(f"start-{failure}"),
        )

    expected = "providers_not_ready" if failure == "providers" else "queue_unavailable"
    _assert_error(expected, captured.value)
    counts_after = (
        db.scalar(select(func.count()).select_from(Tenant)),
        db.scalar(select(func.count()).select_from(TenantOnboarding)),
        db.scalar(select(func.count()).select_from(AuditLog)),
    )
    assert counts_after == counts_before
    assert db.scalar(select(Tenant.id).where(Tenant.slug == data.start.slug)) is None


def test_production_completion_resources_reject_local_provider_records(
    db: Session,
    service: OnboardingService,
) -> None:
    data = _input(db)
    started, _owner, _phone, _bumpa, sync = _start_to_sync(service, db, data)
    _accepted, review = _accept_to_review(service, db, data, sync)
    saga = db.get(TenantOnboarding, started.id)
    assert saga is not None

    production_service = OnboardingService(
        service.settings.model_copy(update={"app_env": "production"})
    )
    assert production_service._completion_resources(db, saga) is None

    connection = db.get(BumpaConnection, saga.bumpa_connection_id)
    profile = db.get(HermesProfile, saga.hermes_profile_id)
    assert connection is not None and profile is not None
    connection.provider = "bumpa"
    profile.provider = "hermes"
    assert production_service._completion_resources(db, saga) is not None
