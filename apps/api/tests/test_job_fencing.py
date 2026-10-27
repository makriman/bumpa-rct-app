from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.base import Base
from app.db.models import AsyncJob, JobOutbox
from app.jobs import worker
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    JobDeferredError,
    JobFence,
    JobLeaseLostError,
    assert_current_job_lease,
    assert_job_lease,
    bind_job_fence,
    claim_job,
    complete_job,
    current_job_fence,
    defer_job,
    enqueue_job,
    fail_job,
    recover_stale_jobs,
    renew_job_lease,
)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def config():
    return AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="default",
        queue_key_prefix="test",
        heartbeat_ttl_seconds=30,
        pop_timeout_seconds=5,
        scheduler_interval_seconds=1,
        dispatch_batch_size=100,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=60,
        stale_lock_seconds=60,
    )


def job(db, *, key="fenced", max_attempts=5):
    value, _ = enqueue_job(
        db, kind="system.noop", payload={}, idempotency_key=key, max_attempts=max_attempts
    )
    db.commit()
    return value


def claimed(db, *, worker_id="reused-worker"):
    value = job(db)
    assert claim_job(db, value.id, worker_id) is not None
    return value


def test_claim_epoch_increments_for_each_admission_and_duplicate_wakeup_is_ignored(db):
    value = claimed(db)
    first_epoch = value.claim_epoch
    assert first_epoch == 1
    assert claim_job(db, value.id, "reused-worker") is None
    assert db.get(AsyncJob, value.id).claim_epoch == first_epoch
    assert (
        defer_job(
            db, value.id, JobDeferredError(), worker_id="reused-worker", claim_epoch=first_epoch
        )
        == "deferred"
    )
    value.available_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert claim_job(db, value.id, "reused-worker") is not None
    assert value.claim_epoch == first_epoch + 1


def test_concurrent_duplicate_wakeups_have_only_one_durable_claim(db):
    value = job(db)
    job_id = value.id

    def claim(worker_id):
        with Session(db.get_bind(), expire_on_commit=False) as contender:
            result = claim_job(contender, job_id, worker_id)
            return (worker_id, result.claim_epoch) if result else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-one", "worker-two")))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    db.refresh(value)
    assert (value.locked_by, value.claim_epoch) == winners[0]
    assert value.attempts == 1


def test_stale_epoch_cannot_renew_complete_fail_or_defer_same_worker_replacement(db):
    value = claimed(db)
    old_epoch = value.claim_epoch
    value.locked_at = utcnow() - timedelta(seconds=120)
    db.commit()
    assert recover_stale_jobs(db, config()) == 1
    assert claim_job(db, value.id, "reused-worker")
    replacement_epoch = value.claim_epoch
    assert replacement_epoch > old_epoch
    assert renew_job_lease(db, value.id, "reused-worker", claim_epoch=old_epoch) is False
    with pytest.raises(JobLeaseLostError):
        complete_job(db, value.id, {"old": True}, worker_id="reused-worker", claim_epoch=old_epoch)
    assert (
        fail_job(
            db, value.id, RuntimeError(), config(), worker_id="reused-worker", claim_epoch=old_epoch
        )
        == "lease_lost"
    )
    assert (
        defer_job(
            db, value.id, JobDeferredError(), worker_id="reused-worker", claim_epoch=old_epoch
        )
        == "lease_lost"
    )
    db.refresh(value)
    assert value.status == "running"
    assert value.claim_epoch == replacement_epoch
    assert value.result is None


def test_deferred_waiting_never_exhausts_retry_budget_and_has_durable_outbox(db):
    value = job(db, max_attempts=1)
    for _ in range(4):
        assert claim_job(db, value.id, "worker")
        assert value.attempts == 1
        assert (
            defer_job(
                db,
                value.id,
                JobDeferredError(delay_seconds=10),
                worker_id="worker",
                claim_epoch=value.claim_epoch,
            )
            == "deferred"
        )
        assert value.attempts == 0
        assert value.status == "retry"
        outbox = db.scalar(select(JobOutbox).where(JobOutbox.job_id == value.id))
        assert outbox.status == "pending"
        assert outbox.available_at.replace(tzinfo=UTC) == value.available_at.replace(tzinfo=UTC)
        assert value.locked_at is None and value.locked_by is None
        value.available_at = utcnow() - timedelta(seconds=1)
        db.commit()


def test_defer_does_not_refund_a_real_previous_failure(db):
    value = claimed(db)
    fail_job(
        db,
        value.id,
        RuntimeError(),
        config(),
        worker_id="reused-worker",
        claim_epoch=value.claim_epoch,
    )
    value.available_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert claim_job(db, value.id, "reused-worker")
    assert value.attempts == 2
    defer_job(
        db, value.id, JobDeferredError(), worker_id="reused-worker", claim_epoch=value.claim_epoch
    )
    assert value.attempts == 1


def test_expired_claim_cannot_be_renewed_or_authorize_effect(db):
    value = claimed(db)
    value.locked_at = utcnow() - timedelta(seconds=120)
    db.commit()
    assert (
        renew_job_lease(
            db, value.id, "reused-worker", claim_epoch=value.claim_epoch, stale_lock_seconds=60
        )
        is False
    )
    with pytest.raises(JobLeaseLostError):
        assert_job_lease(db, job_id=value.id, claim_epoch=value.claim_epoch, stale_lock_seconds=60)


def test_context_fence_is_scoped_and_lost_renewal_stays_invalid(db):
    value = claimed(db)
    fence = JobFence(value.id, "reused-worker", value.claim_epoch, 60)
    assert current_job_fence() is None
    assert_current_job_lease(db)
    with bind_job_fence(fence):
        assert current_job_fence() is fence
        assert_current_job_lease(db)
        fence.invalidate()
        with pytest.raises(JobLeaseLostError):
            assert_current_job_lease(db)
    assert current_job_fence() is None


@pytest.mark.parametrize(
    "field,value", [("status", "retry"), ("locked_by", "another-worker"), ("claim_epoch", 100)]
)
def test_effect_guard_refreshes_database_instead_of_trusting_cached_job(db, field, value):
    current = claimed(db)
    fence = JobFence(current.id, "reused-worker", current.claim_epoch, 60)
    with Session(db.get_bind()) as other:
        other_value = other.get(AsyncJob, current.id)
        setattr(other_value, field, value)
        other.commit()
    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        assert_current_job_lease(db)
    assert fence.invalid


def test_worker_binds_fence_and_defers_without_burning_attempt(db, monkeypatch):
    value = job(db, max_attempts=1)
    observed = []

    def handler(session, current):
        observed.append(current_job_fence())
        assert_current_job_lease(session)
        raise JobDeferredError("Waiting for earlier receipt")

    monkeypatch.setattr(worker.registry, "handler_for", lambda _: handler)
    assert (
        worker.process_one(db, job_id=value.id, worker_id="worker", config=config()) == "deferred"
    )
    assert observed[0].claim_epoch == 1
    assert value.attempts == 0
    assert current_job_fence() is None


def test_worker_invalidated_fence_cannot_mark_handler_success(db, monkeypatch):
    value = job(db)

    def handler(_session, _current):
        current_job_fence().invalidate()
        return {"untrusted_success": True}

    monkeypatch.setattr(worker.registry, "handler_for", lambda _: handler)
    assert (
        worker.process_one(db, job_id=value.id, worker_id="worker", config=config()) == "lease_lost"
    )
    db.refresh(value)
    assert value.status == "running"
    assert value.result is None


def test_lease_keeper_marks_shared_fence_invalid_when_epoch_renewal_is_lost(db, monkeypatch):
    value = claimed(db)
    fence = JobFence(value.id, "reused-worker", value.claim_epoch, 60)
    seen = []

    class Wake:
        def heartbeat(self, _service, _worker):
            return None

    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(db.get_bind()))

    def renew(_db, _job_id, _worker, **options):
        seen.append(options)
        return False

    monkeypatch.setattr(worker, "renew_job_lease", renew)
    keeper = worker.JobLeaseKeeper(
        job_id=value.id, worker_id="reused-worker", config=config(), wake_queue=Wake(), fence=fence
    )
    keeper._run()
    assert fence.invalid
    assert seen == [{"claim_epoch": value.claim_epoch, "stale_lock_seconds": 60}]


def test_lease_keeper_database_error_revokes_in_process_effect_capability(db, monkeypatch):
    value = claimed(db)
    fence = JobFence(value.id, "reused-worker", value.claim_epoch, 60)

    class Wake:
        def heartbeat(self, _service, _worker):
            return None

    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(db.get_bind()))

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(worker, "renew_job_lease", unavailable)
    keeper = worker.JobLeaseKeeper(
        job_id=value.id, worker_id="reused-worker", config=config(), wake_queue=Wake(), fence=fence
    )
    keeper._run()
    assert fence.invalid
