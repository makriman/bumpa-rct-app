"""Bounded background reconciliation never creates new model or send work."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    AsyncJob,
    Conversation,
    Tenant,
    TenantMembership,
    User,
    WhatsappMessage,
)
from app.jobs import handlers, scheduler
from app.jobs.runtime import JobFence, JobLeaseLostError, PermanentJobError, bind_job_fence
from app.providers.hermes import HermesClient
from app.providers.meta import MetaWhatsAppClient
from app.services import run_recovery
from app.services.conversation_scope import receiver_scope
from app.services.run_reconciliation import (
    DeliveryOutcome,
    ModelOutcome,
    ReconciliationAction,
    ReconciliationDecision,
)
from tests.test_run_recovery import NOW, state  # noqa: F401 -- share verified recovery fixture


@pytest.fixture()
def recovery_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, app_env="test", agent_backend="hermes")
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="sweep-a", name="Synthetic")
        user = User(primary_phone_e164="+2348000019001")
        db.add_all((tenant, user))
        db.flush()
        db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner"))
        db.commit()
        yield db, settings, tenant, user
    engine.dispose()


def add_run(context, *, status="running", expires_at=NOW - timedelta(minutes=1)):
    db, _, tenant, user = context
    conversation = Conversation(
        tenant_id=tenant.id,
        user_id=user.id,
        channel="web",
        environment_lane="test",
        context_generation=1,
    )
    db.add(conversation)
    db.flush()
    inbound = AgentMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        direction="inbound",
        channel="web",
        event_scope=receiver_scope("test", ("", "")),
        content="Synthetic private merchant input",
    )
    db.add(inbound)
    db.flush()
    run = AgentExecutionRun(
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        initiating_message_id=inbound.id,
        status=status,
        expires_at=expires_at,
        provider_run_ids=["synthetic-private-admission"],
    )
    db.add(run)
    db.flush()
    conversation.active_run_id = run.id
    db.commit()
    return run


def sweep_job(settings, *, limit=25):
    return AsyncJob(
        kind="system.reconcile_expired_runs",
        tenant_id=None,
        idempotency_key="synthetic-sweep",
        payload={"limit": limit, "environment_lane": settings.app_env},
    )


def forbid_new_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Recovery attempted a model submission or transport send")

    monkeypatch.setattr(HermesClient, "respond", forbidden)
    monkeypatch.setattr(HermesClient, "respond_stream", forbidden)
    monkeypatch.setattr(MetaWhatsAppClient, "send_text", forbidden)
    monkeypatch.setattr(MetaWhatsAppClient, "send_template", forbidden)


def test_scheduler_does_not_enqueue_empty_or_unexpired_work(recovery_db):
    db, settings, *_ = recovery_db
    assert scheduler._ensure_run_recovery_jobs(db, settings=settings, now=NOW) == 0
    for status in ("completed", "failed", "unknown"):
        add_run(recovery_db, status=status)
    add_run(recovery_db, expires_at=NOW + timedelta(minutes=1))
    assert scheduler._ensure_run_recovery_jobs(db, settings=settings, now=NOW) == 0
    assert list(db.scalars(select(AsyncJob))) == []


@pytest.mark.parametrize("status", ["running", "expired", "reconciling"])
def test_scheduler_enqueues_one_bounded_global_job_per_minute_slot(recovery_db, status):
    db, settings, *_ = recovery_db
    add_run(recovery_db, status=status)
    assert scheduler._ensure_run_recovery_jobs(db, settings=settings, now=NOW) == 1
    assert (
        scheduler._ensure_run_recovery_jobs(db, settings=settings, now=NOW + timedelta(seconds=59))
        == 0
    )
    first = db.scalar(select(AsyncJob))
    assert first.kind == "system.reconcile_expired_runs" and first.tenant_id is None
    assert first.payload == {"limit": 25, "environment_lane": "test"}
    assert first.max_attempts == 1
    assert (
        scheduler._ensure_run_recovery_jobs(db, settings=settings, now=NOW + timedelta(minutes=1))
        == 1
    )
    assert len(list(db.scalars(select(AsyncJob)))) == 2


def test_scheduled_job_namespace_includes_environment_lane(recovery_db):
    db, settings, *_ = recovery_db
    add_run(recovery_db)
    other_lane = settings.model_copy(update={"app_env": "development"})
    assert scheduler._ensure_run_recovery_jobs(db, settings=settings, now=NOW) == 1
    assert scheduler._ensure_run_recovery_jobs(db, settings=other_lane, now=NOW) == 1
    jobs = list(db.scalars(select(AsyncJob)))
    assert len({job.idempotency_key for job in jobs}) == 2
    assert {job.payload["environment_lane"] for job in jobs} == {"test", "development"}


@pytest.mark.parametrize("limit", [None, 0, 101, True, "25", 1.5])
def test_handler_rejects_unbounded_or_untyped_batch_before_reconciliation(
    recovery_db, monkeypatch, limit
):
    db, settings, *_ = recovery_db
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(
        handlers,
        "reconcile_expired_runs",
        lambda *args, **kwargs: pytest.fail("Invalid payload reached recovery"),
    )
    with pytest.raises(PermanentJobError, match="payload"):
        handlers.run_recovery_handler(db, sweep_job(settings, limit=limit))


@pytest.mark.parametrize("boundary", ["tenant", "lane", "extra_target"])
def test_handler_requires_exact_global_lane_payload(recovery_db, monkeypatch, boundary):
    db, settings, tenant, _ = recovery_db
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    job = sweep_job(settings)
    if boundary == "tenant":
        job.tenant_id = tenant.id
    elif boundary == "lane":
        job.payload = {**job.payload, "environment_lane": "production"}
    else:
        job.payload = {**job.payload, "run_id": "a-model-supplied-run"}
    with pytest.raises(PermanentJobError):
        handlers.run_recovery_handler(db, job)


def test_handler_uses_actual_expired_selector_bounded_batch_and_safe_counts(
    recovery_db, monkeypatch
):
    db, settings, *_ = recovery_db
    due = [
        add_run(recovery_db, status=status, expires_at=NOW - timedelta(minutes=minute))
        for minute, status in enumerate(("running", "expired", "reconciling", "running"), start=1)
    ]
    untouched = [
        add_run(recovery_db, status="unknown"),
        add_run(recovery_db, expires_at=NOW + timedelta(minutes=1)),
    ]
    forbid_new_external_work(monkeypatch)
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(run_recovery, "utcnow", lambda: NOW)
    result = handlers.run_recovery_handler(db, sweep_job(settings, limit=2))
    assert result["processed"] == 2
    assert result["actions"]["review"] == 2
    assert set(result) == {"status", "processed", "actions"}
    assert set(result["actions"]) == {action.value for action in ReconciliationAction}
    assert "private" not in json.dumps(result)
    assert db.info["is_privileged"] is True
    assert sum(run.status == "unknown" for run in due) == 2
    assert untouched[0].status == "unknown" and untouched[1].status == "running"
    assert list(db.scalars(select(AsyncJob))) == []
    assert list(db.scalars(select(WhatsappMessage))) == []


def test_recorded_provider_get_can_recover_output_without_new_model_post_or_delivery(
    state,  # noqa: F811 -- pytest supplies the imported verified fixture
    monkeypatch,
):
    forbid_new_external_work(monkeypatch)
    monkeypatch.setattr(handlers, "get_settings", lambda: state.settings)
    monkeypatch.setattr(run_recovery, "utcnow", lambda: NOW)
    polls = []

    def recorded_get(self, endpoint, provider_id):
        polls.append(provider_id)
        return {
            "run_id": provider_id,
            "status": "completed",
            "output": "Recovered accepted answer",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }

    monkeypatch.setattr(HermesClient, "poll_run", recorded_get)
    result = handlers.run_recovery_handler(state.db, sweep_job(state.settings, limit=1))
    assert polls == ["provider_one"]
    assert result["processed"] == 1 and result["actions"]["persist_output"] == 1
    assert "Recovered accepted answer" not in json.dumps(result)
    outputs = list(
        state.db.scalars(select(AgentMessage).where(AgentMessage.direction == "outbound"))
    )
    assert len(outputs) == 1 and outputs[0].content == "Recovered accepted answer"
    assert list(state.db.scalars(select(AsyncJob))) == []
    assert list(state.db.scalars(select(WhatsappMessage))) == []


def test_resume_delivery_decision_is_only_a_count_never_a_new_send_job(recovery_db, monkeypatch):
    db, settings, *_ = recovery_db
    forbid_new_external_work(monkeypatch)
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(
        handlers,
        "enqueue_job",
        lambda *args, **kwargs: pytest.fail("Recovery must not enqueue delivery"),
    )
    decision = ReconciliationDecision(
        ReconciliationAction.RESUME_DELIVERY,
        ModelOutcome.COMPLETED,
        DeliveryOutcome.PENDING,
        "synthetic-private-reason",
    )
    monkeypatch.setattr(handlers, "reconcile_expired_runs", lambda *args, **kwargs: (decision,))
    result = handlers.run_recovery_handler(db, sweep_job(settings))
    assert result["processed"] == 1 and result["actions"]["resume_delivery"] == 1
    assert "synthetic-private-reason" not in json.dumps(result)
    assert list(db.scalars(select(AsyncJob))) == []


def test_stale_worker_claim_cannot_enter_sweeper(recovery_db, monkeypatch):
    db, settings, *_ = recovery_db
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(
        handlers,
        "reconcile_expired_runs",
        lambda *args, **kwargs: pytest.fail("Stale worker entered recovery"),
    )
    job = sweep_job(settings)
    job.status, job.locked_by, job.claim_epoch = "running", "new-worker", 2
    job.locked_at = datetime.now(UTC)
    db.add(job)
    db.commit()
    with bind_job_fence(JobFence(job.id, "old-worker", 1, 60)), pytest.raises(JobLeaseLostError):
        handlers.run_recovery_handler(db, job)


def test_regular_scheduler_cycle_wires_sweep_without_external_work(recovery_db, monkeypatch):
    db, settings, *_ = recovery_db
    calls = []
    monkeypatch.setattr(scheduler, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler, "_ensure_daily_maintenance", lambda db: None)
    monkeypatch.setattr(
        scheduler, "_ensure_run_recovery_jobs", lambda db, **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(scheduler, "_ensure_bumpa_sync_jobs", lambda *args, **kwargs: 0)
    monkeypatch.setattr(scheduler, "_ensure_proactive_insight_jobs", lambda *args, **kwargs: 0)
    monkeypatch.setattr(scheduler, "_ensure_operational_alert_jobs", lambda *args, **kwargs: 0)
    monkeypatch.setattr(scheduler, "recover_stale_jobs", lambda *args: 0)
    monkeypatch.setattr(scheduler, "recover_stale_wakeups", lambda *args: 0)
    monkeypatch.setattr(scheduler, "dispatch_due_jobs", lambda *args, **kwargs: 0)
    assert scheduler.run_cycle(
        db, config=SimpleNamespace(dispatch_batch_size=10), wake_queue=object()
    ) == (0, 0, 0, 0)
    assert calls == [{"settings": settings}]
