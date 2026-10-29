from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import AsyncJob, PhoneIdentity, Tenant, TenantMembership, User, WebhookEvent
from app.jobs.runtime import (
    JobFence,
    JobLeaseLostError,
    PermanentJobError,
    bind_job_fence,
    claim_job,
    enqueue_job,
)
from app.services import whatsapp


@pytest.fixture()
def scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scope.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        tenant = Tenant(slug="synthetic-scope", name="Synthetic business")
        other = Tenant(slug="synthetic-other", name="Other synthetic business")
        user = User(primary_phone_e164="+15555550123", status="active")
        db.add_all((tenant, other, user))
        db.flush()
        db.add_all(
            (
                TenantMembership(
                    tenant_id=tenant.id, user_id=user.id, role="owner", status="active"
                ),
                PhoneIdentity(
                    tenant_id=tenant.id, user_id=user.id, phone_e164=user.primary_phone_e164
                ),
            )
        )
        receipt = WebhookEvent(
            provider="whatsapp",
            external_event_id="synthetic-receipt",
            signature_valid=True,
            payload={},
        )
        db.add(receipt)
        db.flush()
        job, _ = enqueue_job(
            db,
            kind="whatsapp.process_webhook",
            payload={"event_id": receipt.id},
            idempotency_key="synthetic-job",
        )
        db.commit()
        assert claim_job(db, job.id, "synthetic-worker")
        fence = JobFence(job.id, "synthetic-worker", job.claim_epoch, 300)
        message = {
            "id": "synthetic-inbound",
            "from": user.primary_phone_e164.lstrip("+"),
            "type": "text",
            "text": {"body": "Synthetic greeting"},
        }
        yield db, tenant, other, user, receipt, job, fence, message
    engine.dispose()


class AtPreprocessing(Exception):
    pass


def test_verified_job_is_bound_before_preprocessing_and_is_idempotent(scope, monkeypatch):
    db, tenant, _other, _user, receipt, job, fence, message = scope

    def checkpoint(*_args, **_kwargs):
        current = db.get(AsyncJob, job.id, populate_existing=True)
        assert current.tenant_id == tenant.id
        assert current.attempts == 1 and current.claim_epoch == fence.claim_epoch
        raise AtPreprocessing

    monkeypatch.setattr(whatsapp, "consume_operation_rate_limit", checkpoint)
    for _ in range(2):
        with bind_job_fence(fence), pytest.raises(AtPreprocessing):
            whatsapp._process_message(db, receipt, message, Settings(app_env="test"))
        db.rollback()
    assert not fence.invalid


@pytest.mark.parametrize("mismatch", ["tenant", "event", "kind"])
def test_existing_binding_or_job_payload_cannot_be_reassigned(scope, mismatch, monkeypatch):
    db, tenant, other, _user, receipt, job, fence, message = scope
    if mismatch == "tenant":
        job.tenant_id = other.id
    elif mismatch == "event":
        job.payload = {"event_id": "another-synthetic-receipt"}
    else:
        job.kind = "system.noop"
    db.commit()
    reached = []
    monkeypatch.setattr(
        whatsapp, "consume_operation_rate_limit", lambda *a, **k: reached.append(True)
    )
    with bind_job_fence(fence), pytest.raises(PermanentJobError):
        whatsapp._process_message(db, receipt, message, Settings(app_env="test"))
    db.rollback()
    db.refresh(job)
    assert job.tenant_id == (other.id if mismatch == "tenant" else None)
    assert not reached


@pytest.mark.parametrize("revoked", ["epoch", "expired"])
def test_binding_never_revives_a_revoked_or_expired_claim(scope, revoked):
    db, _tenant, _other, _user, receipt, job, fence, message = scope
    if revoked == "epoch":
        job.claim_epoch += 1
    else:
        job.locked_at = utcnow() - timedelta(minutes=10)
    db.commit()
    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        whatsapp._process_message(db, receipt, message, Settings(app_env="test"))
    db.rollback()
    db.refresh(job)
    assert job.tenant_id is None
    assert fence.invalid


def test_inline_processing_without_worker_fence_remains_supported(scope, monkeypatch):
    db, _tenant, _other, _user, receipt, job, _fence, message = scope
    monkeypatch.setattr(
        whatsapp,
        "consume_operation_rate_limit",
        lambda *a, **k: (_ for _ in ()).throw(AtPreprocessing()),
    )
    with pytest.raises(AtPreprocessing):
        whatsapp._process_message(db, receipt, message, Settings(app_env="test"))
    assert job.tenant_id is None


def test_disabled_actor_never_binds_a_worker_job(scope):
    db, _tenant, _other, user, receipt, job, fence, message = scope
    user.status = "disabled"
    db.commit()
    with bind_job_fence(fence):
        result = whatsapp._process_message(db, receipt, message, Settings(app_env="test"))
    assert result["status"] == "rejected_unknown_sender"
    assert job.tenant_id is None
