from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher, secret_hash
from app.core.time import utcnow
from app.db.models import (
    AsyncJob,
    BumpaConnection,
    BumpaSyncRun,
    HermesProfile,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    TenantOnboarding,
    User,
)
from app.jobs.runtime import enqueue_job
from app.onboarding_contracts import (
    OnboardingBumpaRequest,
    OnboardingBumpaView,
    OnboardingCompleteRequest,
    OnboardingError,
    OnboardingFailureView,
    OnboardingHermesRequest,
    OnboardingHermesView,
    OnboardingInitialSyncAcceptRequest,
    OnboardingInitialSyncRequest,
    OnboardingInitialSyncView,
    OnboardingMutation,
    OnboardingOwnerRequest,
    OnboardingOwnerView,
    OnboardingPhoneRequest,
    OnboardingPhoneView,
    OnboardingStartRequest,
    OnboardingStatus,
    OnboardingStep,
    OnboardingTenantView,
    OnboardingView,
)
from app.providers.bumpa import BumpaClient, BumpaProviderError
from app.providers.hermes import (
    HermesError,
    activate_reserved_profile,
    reserve_profile,
)
from app.providers.hermes_control import HermesControlClient
from app.providers.local import local_profile_key
from app.services.admin_operations import mask_phone
from app.services.audit import audit
from app.services.bumpa_freshness import usable_bumpa_sync_run_predicate
from app.services.production_readiness import (
    ProductionReadiness,
    check_production_readiness,
)

ReadinessChecker = Callable[[Settings], ProductionReadiness]
BumpaClientFactory = Callable[[str, str, str], BumpaClient]
HermesControlFactory = Callable[[Settings], HermesControlClient]

_TERMINAL_JOB_STATUSES = frozenset({"succeeded", "dead_letter", "cancelled"})


class OnboardingService:
    """Durable administrative provisioning state machine.

    Every public mutation owns its transaction boundary. Provider calls are made
    outside row locks, then the saga is re-locked and revalidated before durable
    state advances. Raw phone numbers, email addresses, API keys and idempotency
    keys never enter the saga row or an audit payload.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        readiness_checker: ReadinessChecker = check_production_readiness,
        bumpa_client_factory: BumpaClientFactory = BumpaClient,
        hermes_control_factory: HermesControlFactory = HermesControlClient,
    ) -> None:
        self.settings = settings
        self._readiness_checker = readiness_checker
        self._bumpa_client_factory = bumpa_client_factory
        self._hermes_control_factory = hermes_control_factory

    def start(
        self,
        db: Session,
        payload: OnboardingStartRequest,
        *,
        actor_user_id: str,
        idempotency_key: str,
    ) -> OnboardingMutation:
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        existing = db.scalar(
            select(TenantOnboarding).where(TenantOnboarding.start_idempotency_key_hash == key_hash)
        )
        if existing is not None:
            if existing.start_fingerprint != fingerprint:
                raise OnboardingError("idempotency_conflict")
            return OnboardingMutation(view=self._view(db, existing), replayed=True)

        # Release the read transaction before dependency probes. A failed
        # preflight must create no tenant, saga, audit or outbox row.
        db.rollback()
        self._require_runtime_ready()

        tenant = Tenant(
            slug=payload.slug,
            name=payload.name,
            status="provisioning",
            business_category=payload.business_category,
            country=payload.country,
            city=payload.city,
            timezone=payload.timezone,
            currency_code=payload.currency_code,
            research_consent_status="pending",
        )
        saga = TenantOnboarding(
            tenant_id=tenant.id,
            status="in_progress",
            current_step="owner",
            revision=0,
            start_idempotency_key_hash=key_hash,
            start_fingerprint=fingerprint,
            sync_attempt=0,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        try:
            db.add(tenant)
            db.flush()
            saga.tenant_id = tenant.id
            db.add(saga)
            db.flush()
            audit(
                db,
                actor_user_id=actor_user_id,
                tenant_id=tenant.id,
                action="tenant.created",
                resource_type="tenant",
                resource_id=tenant.id,
                after={"status": "provisioning"},
            )
            audit(
                db,
                actor_user_id=actor_user_id,
                tenant_id=tenant.id,
                action="tenant.onboarding.started",
                resource_type="tenant_onboarding",
                resource_id=saga.id,
                after={"current_step": "owner", "revision": 0},
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raced = db.scalar(
                select(TenantOnboarding).where(
                    TenantOnboarding.start_idempotency_key_hash == key_hash
                )
            )
            if raced is not None:
                if raced.start_fingerprint != fingerprint:
                    raise OnboardingError("idempotency_conflict") from None
                return OnboardingMutation(view=self._view(db, raced), replayed=True)
            raise OnboardingError("tenant_slug_conflict") from None
        return OnboardingMutation(view=self._view(db, saga), created=True)

    def list(
        self,
        db: Session,
        *,
        status: OnboardingStatus | None,
        limit: int,
    ) -> list[OnboardingView]:
        statement = select(TenantOnboarding)
        if status is not None:
            statement = statement.where(TenantOnboarding.status == status)
        rows = db.scalars(
            statement.order_by(
                TenantOnboarding.updated_at.desc(), TenantOnboarding.id.desc()
            ).limit(limit)
        ).all()
        return [self._view(db, row) for row in rows]

    def get(self, db: Session, onboarding_id: str) -> OnboardingView:
        saga = db.get(TenantOnboarding, onboarding_id)
        if saga is None:
            raise OnboardingError("onboarding_not_found")
        return self._view(db, saga)

    def set_owner(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingOwnerRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        saga = self._locked(db, onboarding_id)
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "owner_idempotency_key_hash",
            "owner_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "owner", expected_revision)

        user = db.scalar(select(User).where(User.primary_phone_e164 == payload.phone_e164))
        requested_email = str(payload.email) if payload.email is not None else None
        if user is not None:
            # The phone is the authoritative identity boundary. An existing
            # active platform operator can also own a tenant; onboarding must
            # not rewrite or reject that global user's profile fields.
            if user.status != "active":
                raise OnboardingError("owner_conflict")
        else:
            if (
                requested_email is not None
                and db.scalar(select(User).where(User.email == requested_email)) is not None
            ):
                raise OnboardingError("owner_conflict")
            candidate = User(
                name=payload.name,
                email=requested_email,
                primary_phone_e164=payload.phone_e164,
                status="active",
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                user = candidate
            except IntegrityError:
                user = db.scalar(select(User).where(User.primary_phone_e164 == payload.phone_e164))
                if user is None or user.status != "active":
                    raise OnboardingError("owner_conflict") from None

        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == saga.tenant_id,
                TenantMembership.user_id == user.id,
            )
        )
        if membership is not None:
            if membership.role != "owner" or membership.status != "active":
                raise OnboardingError("owner_conflict")
        else:
            candidate_membership = TenantMembership(
                tenant_id=saga.tenant_id,
                user_id=user.id,
                role="owner",
                status="active",
            )
            try:
                with db.begin_nested():
                    db.add(candidate_membership)
                    db.flush()
                membership = candidate_membership
            except IntegrityError:
                membership = db.scalar(
                    select(TenantMembership).where(
                        TenantMembership.tenant_id == saga.tenant_id,
                        TenantMembership.user_id == user.id,
                    )
                )
                if (
                    membership is None
                    or membership.role != "owner"
                    or membership.status != "active"
                ):
                    raise OnboardingError("owner_conflict") from None

        saga.owner_user_id = user.id
        saga.owner_membership_id = membership.id
        self._advance(
            saga,
            next_step="phone",
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="owner_idempotency_key_hash",
            fingerprint_field="owner_fingerprint",
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="tenant.onboarding.owner_saved",
            resource_type="tenant_membership",
            resource_id=membership.id,
            after={"user_id": user.id, "role": "owner", "revision": saga.revision},
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga))

    def approve_phone(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingPhoneRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        saga = self._locked(db, onboarding_id)
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "phone_idempotency_key_hash",
            "phone_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "phone", expected_revision)
        if saga.owner_user_id is None or saga.owner_membership_id is None:
            raise OnboardingError("completion_requirements_not_met")
        user = db.get(User, saga.owner_user_id)
        if user is None or user.status != "active":
            raise OnboardingError("owner_conflict")

        identity = db.scalar(
            select(PhoneIdentity).where(PhoneIdentity.phone_e164 == user.primary_phone_e164)
        )
        if identity is not None:
            if (
                identity.tenant_id != saga.tenant_id
                or identity.user_id != user.id
                or identity.status != "approved"
                or identity.opt_out
            ):
                raise OnboardingError("phone_identity_conflict")
            identity.label = payload.label
        else:
            candidate = PhoneIdentity(
                tenant_id=saga.tenant_id,
                user_id=user.id,
                phone_e164=user.primary_phone_e164,
                label=payload.label,
                status="approved",
                opt_out=False,
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                identity = candidate
            except IntegrityError:
                identity = db.scalar(
                    select(PhoneIdentity).where(PhoneIdentity.phone_e164 == user.primary_phone_e164)
                )
                if (
                    identity is None
                    or identity.tenant_id != saga.tenant_id
                    or identity.user_id != user.id
                    or identity.status != "approved"
                    or identity.opt_out
                ):
                    raise OnboardingError("phone_identity_conflict") from None

        saga.phone_identity_id = identity.id
        self._advance(
            saga,
            next_step="bumpa",
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="phone_idempotency_key_hash",
            fingerprint_field="phone_fingerprint",
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="phone.approved",
            resource_type="phone_identity",
            resource_id=identity.id,
            after={"user_id": user.id, "status": "approved", "revision": saga.revision},
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga))

    def connect_bumpa(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingBumpaRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        api_key = payload.api_key.get_secret_value()
        fingerprint = self._fingerprint(
            payload,
            exclude={"api_key"},
            extra={
                "credential_hmac": secret_hash(
                    f"onboarding-bumpa:{api_key}",
                    self.settings.field_encryption_key,
                )
            },
        )

        # Fast replay/out-of-order validation before the provider call. No row
        # lock is held over network I/O.
        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "bumpa_idempotency_key_hash",
            "bumpa_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "bumpa", expected_revision)
        db.rollback()

        provider = (
            "local" if self.settings.is_local and self.settings.bumpa_backend == "mock" else "bumpa"
        )
        if provider == "bumpa":
            if self.settings.bumpa_backend != "bumpa":
                raise OnboardingError("providers_not_ready")
            try:
                with self._bumpa_client_factory(
                    api_key, payload.scope_type, payload.scope_id
                ) as client:
                    client.verify()
            except (BumpaProviderError, ValueError) as error:
                retryable = isinstance(error, BumpaProviderError) and error.retryable
                self._persist_failure(
                    db,
                    onboarding_id,
                    step="bumpa",
                    code="bumpa_verification_failed",
                    retryable=retryable,
                    actor_user_id=actor_user_id,
                )
                raise OnboardingError("bumpa_verification_failed") from None

        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "bumpa_idempotency_key_hash",
            "bumpa_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "bumpa", expected_revision)

        encrypted = FieldCipher(self.settings.field_encryption_key).encrypt(api_key)
        connection = db.scalar(
            select(BumpaConnection).where(BumpaConnection.tenant_id == saga.tenant_id)
        )
        if connection is not None:
            if (
                connection.scope_type != payload.scope_type
                or connection.scope_id != payload.scope_id
                or connection.provider != provider
            ):
                raise OnboardingError("bumpa_connection_conflict")
            connection.encrypted_api_key = encrypted
            connection.status = "active"
            connection.last_error = None
        else:
            candidate = BumpaConnection(
                tenant_id=saga.tenant_id,
                encrypted_api_key=encrypted,
                scope_type=payload.scope_type,
                scope_id=payload.scope_id,
                provider=provider,
                status="active",
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                connection = candidate
            except IntegrityError:
                connection = db.scalar(
                    select(BumpaConnection).where(BumpaConnection.tenant_id == saga.tenant_id)
                )
                if (
                    connection is None
                    or connection.scope_type != payload.scope_type
                    or connection.scope_id != payload.scope_id
                    or connection.provider != provider
                ):
                    raise OnboardingError("bumpa_connection_conflict") from None

        saga.bumpa_connection_id = connection.id
        self._advance(
            saga,
            next_step="initial_sync",
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="bumpa_idempotency_key_hash",
            fingerprint_field="bumpa_fingerprint",
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="tenant.onboarding.bumpa_connected",
            resource_type="bumpa_connection",
            resource_id=connection.id,
            after={
                "provider": provider,
                "scope_type": payload.scope_type,
                "scope_id_last4": payload.scope_id[-4:],
                "revision": saga.revision,
            },
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga))

    def trigger_initial_sync(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingInitialSyncRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "initial_sync_idempotency_key_hash",
            "initial_sync_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "initial_sync", expected_revision)

        previous_job = (
            db.get(AsyncJob, saga.initial_sync_job_id)
            if saga.initial_sync_job_id is not None
            else None
        )
        if previous_job is not None:
            if previous_job.status not in _TERMINAL_JOB_STATUSES:
                raise OnboardingError("initial_sync_in_progress")
            usable = self._usable_run_for_job(db, saga, previous_job)
            if usable is not None:
                raise OnboardingError("initial_sync_retry_not_allowed")

        db.rollback()
        self._require_runtime_ready()
        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "initial_sync_idempotency_key_hash",
            "initial_sync_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "initial_sync", expected_revision)
        if saga.bumpa_connection_id is None:
            raise OnboardingError("completion_requirements_not_met")

        previous_job = (
            db.get(AsyncJob, saga.initial_sync_job_id)
            if saga.initial_sync_job_id is not None
            else None
        )
        if previous_job is not None:
            if previous_job.status not in _TERMINAL_JOB_STATUSES:
                raise OnboardingError("initial_sync_in_progress")
            if self._usable_run_for_job(db, saga, previous_job) is not None:
                raise OnboardingError("initial_sync_retry_not_allowed")

        attempt = saga.sync_attempt + 1
        job_payload = {
            "tenant_id": saga.tenant_id,
            "connection_id": saga.bumpa_connection_id,
            "date_from": payload.date_from.isoformat(),
            "date_to": payload.date_to.isoformat(),
        }
        job, created = enqueue_job(
            db,
            kind="bumpa.sync",
            tenant_id=saga.tenant_id,
            payload=job_payload,
            idempotency_key=f"onboarding:{saga.id}:initial-sync:{attempt}",
            max_attempts=5,
        )
        if not created and job.payload != job_payload:
            raise OnboardingError("idempotency_conflict")
        saga.initial_sync_job_id = job.id
        saga.initial_sync_run_id = None
        saga.sync_attempt = attempt
        self._record_command(
            saga,
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="initial_sync_idempotency_key_hash",
            fingerprint_field="initial_sync_fingerprint",
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="tenant.onboarding.initial_sync_requested",
            resource_type="async_job",
            resource_id=job.id,
            after={
                "attempt": attempt,
                "date_from": payload.date_from.isoformat(),
                "date_to": payload.date_to.isoformat(),
                "revision": saga.revision,
            },
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga), replayed=not created)

    def accept_initial_sync(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingInitialSyncAcceptRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        saga = self._locked(db, onboarding_id)
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "initial_sync_accept_idempotency_key_hash",
            "initial_sync_accept_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "initial_sync", expected_revision)
        if saga.initial_sync_job_id is None:
            raise OnboardingError("initial_sync_not_ready")
        job = db.get(AsyncJob, saga.initial_sync_job_id)
        if job is None or job.tenant_id != saga.tenant_id:
            raise OnboardingError("initial_sync_not_ready")
        if job.status != "succeeded":
            if job.status in _TERMINAL_JOB_STATUSES:
                self._mark_failure(
                    saga,
                    step="initial_sync",
                    code="initial_sync_not_ready",
                    retryable=True,
                    actor_user_id=actor_user_id,
                )
                audit(
                    db,
                    actor_user_id=actor_user_id,
                    tenant_id=saga.tenant_id,
                    action="tenant.onboarding.step_failed",
                    resource_type="tenant_onboarding",
                    resource_id=saga.id,
                    after={
                        "step": "initial_sync",
                        "code": "initial_sync_not_ready",
                    },
                )
                db.commit()
            raise OnboardingError(
                "initial_sync_in_progress"
                if job.status not in _TERMINAL_JOB_STATUSES
                else "initial_sync_not_ready"
            )
        run = self._usable_run_for_job(db, saga, job)
        if run is None:
            self._mark_failure(
                saga,
                step="initial_sync",
                code="initial_sync_not_ready",
                retryable=True,
                actor_user_id=actor_user_id,
            )
            audit(
                db,
                actor_user_id=actor_user_id,
                tenant_id=saga.tenant_id,
                action="tenant.onboarding.step_failed",
                resource_type="tenant_onboarding",
                resource_id=saga.id,
                after={"step": "initial_sync", "code": "initial_sync_not_ready"},
            )
            db.commit()
            raise OnboardingError("initial_sync_not_ready")

        saga.initial_sync_run_id = run.id
        self._advance(
            saga,
            next_step="hermes",
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="initial_sync_accept_idempotency_key_hash",
            fingerprint_field="initial_sync_accept_fingerprint",
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="tenant.onboarding.initial_sync_accepted",
            resource_type="bumpa_sync_run",
            resource_id=run.id,
            after={
                "job_id": job.id,
                "status": run.status,
                "completion_quality": run.completion_quality,
                "revision": saga.revision,
            },
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga))

    def provision_hermes(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingHermesRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "hermes_idempotency_key_hash",
            "hermes_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "hermes", expected_revision)
        tenant = db.get(Tenant, saga.tenant_id)
        if tenant is None:
            raise OnboardingError("tenant_not_found")

        if self.settings.is_local and self.settings.agent_backend == "mock":
            profile = db.scalar(
                select(HermesProfile).where(HermesProfile.tenant_id == saga.tenant_id)
            )
            if profile is None:
                candidate = HermesProfile(
                    tenant_id=saga.tenant_id,
                    profile_name=_local_profile_name(tenant),
                    provider="local",
                    api_internal_url="local://agent",
                    encrypted_api_key=FieldCipher(self.settings.field_encryption_key).encrypt(
                        local_profile_key()
                    ),
                    status="active",
                )
                try:
                    with db.begin_nested():
                        db.add(candidate)
                        db.flush()
                    profile = candidate
                except IntegrityError:
                    profile = db.scalar(
                        select(HermesProfile).where(HermesProfile.tenant_id == saga.tenant_id)
                    )
                    if profile is None or profile.provider != "local":
                        raise OnboardingError("hermes_profile_conflict") from None
            elif profile.provider != "local":
                raise OnboardingError("hermes_profile_conflict")
            profile.status = "active"
            saga.hermes_profile_id = profile.id
            self._advance(
                saga,
                next_step="review",
                actor_user_id=actor_user_id,
                key_hash=key_hash,
                fingerprint=fingerprint,
                key_field="hermes_idempotency_key_hash",
                fingerprint_field="hermes_fingerprint",
            )
            audit(
                db,
                actor_user_id=actor_user_id,
                tenant_id=saga.tenant_id,
                action="hermes.profile.created",
                resource_type="hermes_profile",
                resource_id=profile.id,
                after={"provider": "local", "status": "active"},
            )
            db.commit()
            return OnboardingMutation(view=self._view(db, saga))

        if self.settings.agent_backend != "hermes":
            raise OnboardingError("providers_not_ready")

        profile = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == saga.tenant_id))
        reserved = profile is None
        try:
            if profile is None:
                profile = reserve_profile(db, tenant, self.settings)
            elif profile.provider != "hermes":
                raise OnboardingError("hermes_profile_conflict")
            saga.hermes_profile_id = profile.id
            if reserved:
                audit(
                    db,
                    actor_user_id=actor_user_id,
                    tenant_id=saga.tenant_id,
                    action="hermes.profile.provisioned",
                    resource_type="hermes_profile",
                    resource_id=profile.id,
                    after={
                        "profile_name": profile.profile_name,
                        "provider": "hermes",
                        "api_port": profile.api_port,
                        "status": "provisioning",
                    },
                )
            # The durable reservation and saga FK must commit before any file is
            # created. A failure after this point is fully reconcilable.
            db.commit()
        except HermesError:
            db.rollback()
            self._persist_failure(
                db,
                onboarding_id,
                step="hermes",
                code="hermes_provisioning_failed",
                retryable=False,
                actor_user_id=actor_user_id,
            )
            raise OnboardingError("hermes_provisioning_failed") from None
        except IntegrityError:
            db.rollback()
            profile = db.scalar(
                select(HermesProfile).where(HermesProfile.tenant_id == saga.tenant_id)
            )
            if profile is None or profile.provider != "hermes":
                raise OnboardingError("hermes_profile_conflict") from None

        try:
            activate_reserved_profile(
                profile,
                tenant,
                self.settings,
                control=self._hermes_control_factory(self.settings),
            )
        except (HermesError, OSError, ValueError):
            db.rollback()
            saga = self._locked(db, onboarding_id)
            persisted_profile = db.get(HermesProfile, profile.id)
            if persisted_profile is not None:
                persisted_profile.status = "degraded"
            self._mark_failure(
                saga,
                step="hermes",
                code="hermes_provisioning_failed",
                retryable=True,
                actor_user_id=actor_user_id,
            )
            audit(
                db,
                actor_user_id=actor_user_id,
                tenant_id=saga.tenant_id,
                action="hermes.profile.activation_failed",
                resource_type="hermes_profile",
                resource_id=profile.id,
                after={"status": "degraded", "category": "activation_failed"},
            )
            db.commit()
            raise OnboardingError("hermes_provisioning_failed") from None

        db.rollback()
        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "hermes_idempotency_key_hash",
            "hermes_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "hermes", expected_revision)
        persisted_profile = db.get(HermesProfile, profile.id)
        if persisted_profile is None or persisted_profile.tenant_id != saga.tenant_id:
            raise OnboardingError("hermes_profile_conflict")
        persisted_profile.status = "active"
        saga.hermes_profile_id = persisted_profile.id
        self._advance(
            saga,
            next_step="review",
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="hermes_idempotency_key_hash",
            fingerprint_field="hermes_fingerprint",
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="hermes.profile.activated",
            resource_type="hermes_profile",
            resource_id=persisted_profile.id,
            after={"status": "active", "control_status": "activated"},
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga))

    def complete(
        self,
        db: Session,
        onboarding_id: str,
        payload: OnboardingCompleteRequest,
        *,
        actor_user_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> OnboardingMutation:
        key_hash = self._key_hash(actor_user_id, idempotency_key)
        fingerprint = self._fingerprint(payload)
        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "complete_idempotency_key_hash",
            "complete_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "review", expected_revision)
        tenant = db.get(Tenant, saga.tenant_id)
        profile = (
            db.get(HermesProfile, saga.hermes_profile_id)
            if saga.hermes_profile_id is not None
            else None
        )
        if tenant is None:
            raise OnboardingError("tenant_not_found")
        db.rollback()

        self._require_runtime_ready()
        if not self.settings.is_local:
            if profile is None or profile.provider != "hermes":
                raise OnboardingError("completion_requirements_not_met")
            try:
                activate_reserved_profile(
                    profile,
                    tenant,
                    self.settings,
                    control=self._hermes_control_factory(self.settings),
                )
            except (HermesError, OSError, ValueError):
                self._persist_failure(
                    db,
                    onboarding_id,
                    step="review",
                    code="hermes_provisioning_failed",
                    retryable=True,
                    actor_user_id=actor_user_id,
                )
                raise OnboardingError("hermes_provisioning_failed") from None
            db.rollback()

        saga = self._locked(db, onboarding_id)
        if self._is_replay(
            saga,
            key_hash,
            fingerprint,
            "complete_idempotency_key_hash",
            "complete_fingerprint",
        ):
            return OnboardingMutation(view=self._view(db, saga), replayed=True)
        self._require_step(saga, "review", expected_revision)
        tenant = db.get(Tenant, saga.tenant_id)
        if tenant is None or tenant.status != "provisioning":
            raise OnboardingError("completion_requirements_not_met")
        if saga.hermes_profile_id is not None and not self.settings.is_local:
            reconciled_profile = db.get(HermesProfile, saga.hermes_profile_id)
            if (
                reconciled_profile is None
                or reconciled_profile.tenant_id != saga.tenant_id
                or reconciled_profile.provider != "hermes"
            ):
                raise OnboardingError("completion_requirements_not_met")
            # The authenticated activation immediately before re-locking is the
            # authoritative readiness proof. Persist its recovered state in the
            # same transaction as final invariant validation and activation.
            reconciled_profile.status = "active"
        resources = self._completion_resources(db, saga)
        if resources is None:
            raise OnboardingError("completion_requirements_not_met")
        user, membership, phone, connection, run, profile = resources

        tenant.status = "active"
        saga.status = "completed"
        saga.current_step = "completed"
        saga.completed_at = utcnow()
        self._record_command(
            saga,
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field="complete_idempotency_key_hash",
            fingerprint_field="complete_fingerprint",
            preserve_completed=True,
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="tenant.onboarding.completed",
            resource_type="tenant_onboarding",
            resource_id=saga.id,
            before={"tenant_status": "provisioning", "current_step": "review"},
            after={
                "tenant_id": tenant.id,
                "user_id": user.id,
                "membership_id": membership.id,
                "phone_identity_id": phone.id,
                "bumpa_connection_id": connection.id,
                "initial_sync_job_id": saga.initial_sync_job_id,
                "initial_sync_run_id": run.id,
                "hermes_profile_id": profile.id,
                "tenant_status": "active",
                "revision": saga.revision,
            },
        )
        db.commit()
        return OnboardingMutation(view=self._view(db, saga))

    def _locked(self, db: Session, onboarding_id: str) -> TenantOnboarding:
        saga = db.scalar(
            select(TenantOnboarding).where(TenantOnboarding.id == onboarding_id).with_for_update()
        )
        if saga is None:
            raise OnboardingError("onboarding_not_found")
        return saga

    def _key_hash(self, actor_user_id: str, idempotency_key: str) -> str:
        return secret_hash(
            f"onboarding:v1:{actor_user_id}:{idempotency_key}",
            self.settings.field_encryption_key,
        )

    def _fingerprint(
        self,
        payload: Any,
        *,
        exclude: set[str] | None = None,
        extra: dict[str, str] | None = None,
    ) -> str:
        dumped = payload.model_dump(mode="json", exclude=exclude or set())
        if extra:
            dumped.update(extra)
        encoded = json.dumps(
            dumped,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return secret_hash(
            f"onboarding-fingerprint:v1:{encoded}",
            self.settings.field_encryption_key,
        )

    @staticmethod
    def _is_replay(
        saga: TenantOnboarding,
        key_hash: str,
        fingerprint: str,
        key_field: str,
        fingerprint_field: str,
    ) -> bool:
        stored_key = cast(str | None, getattr(saga, key_field))
        if stored_key is None or not hmac.compare_digest(stored_key, key_hash):
            return False
        stored_fingerprint = cast(str | None, getattr(saga, fingerprint_field))
        if stored_fingerprint is None or not hmac.compare_digest(stored_fingerprint, fingerprint):
            raise OnboardingError("idempotency_conflict")
        return True

    @staticmethod
    def _require_step(
        saga: TenantOnboarding,
        step: OnboardingStep,
        expected_revision: int,
    ) -> None:
        if saga.revision != expected_revision:
            raise OnboardingError("revision_conflict")
        if saga.status == "completed" or saga.current_step != step:
            raise OnboardingError("invalid_step")

    def _advance(
        self,
        saga: TenantOnboarding,
        *,
        next_step: OnboardingStep,
        actor_user_id: str,
        key_hash: str,
        fingerprint: str,
        key_field: str,
        fingerprint_field: str,
    ) -> None:
        saga.current_step = next_step
        self._record_command(
            saga,
            actor_user_id=actor_user_id,
            key_hash=key_hash,
            fingerprint=fingerprint,
            key_field=key_field,
            fingerprint_field=fingerprint_field,
        )

    @staticmethod
    def _record_command(
        saga: TenantOnboarding,
        *,
        actor_user_id: str,
        key_hash: str,
        fingerprint: str,
        key_field: str,
        fingerprint_field: str,
        preserve_completed: bool = False,
    ) -> None:
        setattr(saga, key_field, key_hash)
        setattr(saga, fingerprint_field, fingerprint)
        saga.revision += 1
        saga.updated_by = actor_user_id
        saga.failure_code = None
        saga.failure_step = None
        saga.failure_retryable = None
        saga.failure_at = None
        if not preserve_completed:
            saga.status = "in_progress"

    @staticmethod
    def _mark_failure(
        saga: TenantOnboarding,
        *,
        step: OnboardingStep,
        code: str,
        retryable: bool,
        actor_user_id: str,
    ) -> None:
        saga.status = "attention_required"
        saga.failure_code = code
        saga.failure_step = step
        saga.failure_retryable = retryable
        saga.failure_at = utcnow()
        saga.updated_by = actor_user_id
        saga.revision += 1

    def _persist_failure(
        self,
        db: Session,
        onboarding_id: str,
        *,
        step: OnboardingStep,
        code: str,
        retryable: bool,
        actor_user_id: str,
    ) -> None:
        db.rollback()
        saga = self._locked(db, onboarding_id)
        if saga.status == "completed" or saga.current_step != step:
            db.rollback()
            return
        self._mark_failure(
            saga,
            step=step,
            code=code,
            retryable=retryable,
            actor_user_id=actor_user_id,
        )
        audit(
            db,
            actor_user_id=actor_user_id,
            tenant_id=saga.tenant_id,
            action="tenant.onboarding.step_failed",
            resource_type="tenant_onboarding",
            resource_id=saga.id,
            after={"step": step, "code": code, "retryable": retryable},
        )
        db.commit()

    def _require_runtime_ready(self) -> ProductionReadiness:
        try:
            readiness = self._readiness_checker(self.settings)
        except (OSError, ValueError):
            raise OnboardingError("queue_unavailable") from None
        if not readiness.ready:
            raise OnboardingError("queue_unavailable")
        if not self.settings.is_local:
            if readiness.async_runtime.get("enabled") is not True:
                raise OnboardingError("queue_unavailable")
            if not readiness.providers.ready:
                raise OnboardingError("providers_not_ready")
        return readiness

    @staticmethod
    def _job_run_id(job: AsyncJob) -> str | None:
        result = job.result if isinstance(job.result, dict) else None
        value = result.get("sync_run_id") if result is not None else None
        return value if isinstance(value, str) and value else None

    def _usable_run_for_job(
        self,
        db: Session,
        saga: TenantOnboarding,
        job: AsyncJob,
    ) -> BumpaSyncRun | None:
        run_id = self._job_run_id(job)
        if run_id is None or saga.bumpa_connection_id is None:
            return None
        if (
            job.kind != "bumpa.sync"
            or job.tenant_id != saga.tenant_id
            or job.payload.get("tenant_id") != saga.tenant_id
            or job.payload.get("connection_id") != saga.bumpa_connection_id
        ):
            return None
        try:
            requested_from = date.fromisoformat(str(job.payload["date_from"]))
            requested_to = date.fromisoformat(str(job.payload["date_to"]))
        except (KeyError, TypeError, ValueError):
            return None
        return db.scalar(
            select(BumpaSyncRun).where(
                BumpaSyncRun.id == run_id,
                BumpaSyncRun.tenant_id == saga.tenant_id,
                BumpaSyncRun.bumpa_connection_id == saga.bumpa_connection_id,
                BumpaSyncRun.requested_from == requested_from,
                BumpaSyncRun.requested_to == requested_to,
                usable_bumpa_sync_run_predicate(),
            )
        )

    def _completion_resources(
        self,
        db: Session,
        saga: TenantOnboarding,
    ) -> (
        tuple[
            User,
            TenantMembership,
            PhoneIdentity,
            BumpaConnection,
            BumpaSyncRun,
            HermesProfile,
        ]
        | None
    ):
        if any(
            value is None
            for value in (
                saga.owner_user_id,
                saga.owner_membership_id,
                saga.phone_identity_id,
                saga.bumpa_connection_id,
                saga.initial_sync_job_id,
                saga.initial_sync_run_id,
                saga.hermes_profile_id,
            )
        ):
            return None
        user = db.get(User, saga.owner_user_id)
        membership = db.get(TenantMembership, saga.owner_membership_id)
        phone = db.get(PhoneIdentity, saga.phone_identity_id)
        connection = db.get(BumpaConnection, saga.bumpa_connection_id)
        job = db.get(AsyncJob, saga.initial_sync_job_id)
        try:
            job_requested_from = (
                date.fromisoformat(str(job.payload["date_from"])) if job is not None else None
            )
            job_requested_to = (
                date.fromisoformat(str(job.payload["date_to"])) if job is not None else None
            )
        except (KeyError, TypeError, ValueError):
            return None
        run = db.scalar(
            select(BumpaSyncRun).where(
                BumpaSyncRun.id == saga.initial_sync_run_id,
                BumpaSyncRun.tenant_id == saga.tenant_id,
                BumpaSyncRun.bumpa_connection_id == saga.bumpa_connection_id,
                usable_bumpa_sync_run_predicate(),
            )
        )
        profile = db.get(HermesProfile, saga.hermes_profile_id)
        if (
            user is None
            or user.status != "active"
            or membership is None
            or membership.tenant_id != saga.tenant_id
            or membership.user_id != user.id
            or membership.role != "owner"
            or membership.status != "active"
            or phone is None
            or phone.tenant_id != saga.tenant_id
            or phone.user_id != user.id
            or phone.phone_e164 != user.primary_phone_e164
            or phone.status != "approved"
            or phone.opt_out
            or connection is None
            or connection.tenant_id != saga.tenant_id
            or connection.status != "active"
            or job is None
            or job.tenant_id != saga.tenant_id
            or job.kind != "bumpa.sync"
            or job.payload.get("tenant_id") != saga.tenant_id
            or job.payload.get("connection_id") != saga.bumpa_connection_id
            or job.status != "succeeded"
            or self._job_run_id(job) != saga.initial_sync_run_id
            or run is None
            or run.requested_from != job_requested_from
            or run.requested_to != job_requested_to
            or profile is None
            or profile.tenant_id != saga.tenant_id
            or profile.status != "active"
        ):
            return None
        if not self.settings.is_local and (
            connection.provider != "bumpa" or profile.provider != "hermes"
        ):
            return None
        return user, membership, phone, connection, run, profile

    def _view(self, db: Session, saga: TenantOnboarding) -> OnboardingView:
        tenant = db.get(Tenant, saga.tenant_id)
        if tenant is None:
            raise OnboardingError("tenant_not_found")

        owner: OnboardingOwnerView | None = None
        if saga.owner_user_id is not None and saga.owner_membership_id is not None:
            user = db.get(User, saga.owner_user_id)
            membership = db.get(TenantMembership, saga.owner_membership_id)
            if user is not None and membership is not None:
                owner = OnboardingOwnerView(
                    user_id=user.id,
                    membership_id=membership.id,
                    name=user.name,
                    email_masked=_mask_email(user.email),
                    status=membership.status,
                )

        phone_view: OnboardingPhoneView | None = None
        if saga.phone_identity_id is not None:
            phone = db.get(PhoneIdentity, saga.phone_identity_id)
            if phone is not None:
                phone_view = OnboardingPhoneView(
                    identity_id=phone.id,
                    phone_masked=mask_phone(phone.phone_e164),
                    label=phone.label,
                    status=phone.status,
                    opt_out=phone.opt_out,
                )

        bumpa_view: OnboardingBumpaView | None = None
        if saga.bumpa_connection_id is not None:
            connection = db.get(BumpaConnection, saga.bumpa_connection_id)
            if connection is not None:
                bumpa_view = OnboardingBumpaView(
                    connection_id=connection.id,
                    provider=connection.provider,
                    scope_type=connection.scope_type,
                    scope_id_last4=connection.scope_id[-4:],
                    status=connection.status,
                )

        sync_view = self._sync_view(db, saga)

        hermes_view: OnboardingHermesView | None = None
        if saga.hermes_profile_id is not None:
            profile = db.get(HermesProfile, saga.hermes_profile_id)
            if profile is not None:
                hermes_view = OnboardingHermesView(
                    profile_id=profile.id,
                    profile_name=profile.profile_name,
                    provider=profile.provider,
                    api_port=profile.api_port,
                    status=profile.status,
                )

        failure: OnboardingFailureView | None = None
        if (
            saga.failure_code is not None
            and saga.failure_step is not None
            and saga.failure_retryable is not None
            and saga.failure_at is not None
        ):
            failure = OnboardingFailureView(
                code=saga.failure_code,
                step=cast(OnboardingStep, saga.failure_step),
                retryable=saga.failure_retryable,
                at=saga.failure_at,
            )
        return OnboardingView(
            id=saga.id,
            tenant_id=saga.tenant_id,
            status=cast(OnboardingStatus, saga.status),
            current_step=cast(OnboardingStep, saga.current_step),
            revision=saga.revision,
            tenant=OnboardingTenantView(
                id=tenant.id,
                slug=tenant.slug,
                name=tenant.name,
                status=tenant.status,
            ),
            owner=owner,
            phone=phone_view,
            bumpa=bumpa_view,
            initial_sync=sync_view,
            hermes=hermes_view,
            failure=failure,
            created_at=saga.created_at,
            updated_at=saga.updated_at,
            completed_at=saga.completed_at,
        )

    def _sync_view(
        self,
        db: Session,
        saga: TenantOnboarding,
    ) -> OnboardingInitialSyncView | None:
        if saga.initial_sync_job_id is None:
            return None
        job = db.get(AsyncJob, saga.initial_sync_job_id)
        if job is None or job.tenant_id != saga.tenant_id:
            return None
        try:
            requested_from = date.fromisoformat(str(job.payload["date_from"]))
            requested_to = date.fromisoformat(str(job.payload["date_to"]))
        except (KeyError, TypeError, ValueError):
            return None
        run_id = saga.initial_sync_run_id or self._job_run_id(job)
        run = db.get(BumpaSyncRun, run_id) if run_id is not None else None
        if run is not None and (
            run.tenant_id != saga.tenant_id or run.bumpa_connection_id != saga.bumpa_connection_id
        ):
            run = None
            run_id = None
        return OnboardingInitialSyncView(
            attempt=saga.sync_attempt,
            requested_from=requested_from,
            requested_to=requested_to,
            job_id=job.id,
            job_status=job.status,
            sync_run_id=run_id,
            sync_status=run.status if run is not None else None,
            completion_quality=run.completion_quality if run is not None else None,
            orders_availability=run.orders_availability if run is not None else None,
            orders_count=run.orders_count if run is not None else None,
        )


def _mask_email(value: str | None) -> str | None:
    if value is None or "@" not in value:
        return None
    local, domain = value.rsplit("@", 1)
    return f"{local[:1]}•••@{domain}" if local else f"•••@{domain}"


def _local_profile_name(tenant: Tenant) -> str:
    slug = "".join(
        character if character.isalnum() else "_" for character in tenant.slug.lower()
    ).strip("_")[:80]
    if not slug:
        raise OnboardingError("hermes_profile_conflict")
    return f"tenant_{slug}_{tenant.id[:8]}"
