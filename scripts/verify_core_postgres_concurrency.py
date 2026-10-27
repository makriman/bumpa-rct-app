"""Concurrent core gates for an already-owned disposable PostgreSQL database.

Imported only by verify_core_postgres.py after its pinned, loopback-only tmpfs
container has been migrated. Provider responses and worker wake-ups are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    AsyncJob,
    Conversation,
    HermesProfile,
    IngressStream,
    JobOutbox,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
    WebhookEvent,
)
from app.jobs import worker
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    JobDeferredError,
    JobFence,
    JobLeaseLostError,
    assert_current_job_lease,
    bind_job_fence,
    claim_job,
    complete_job,
    defer_job,
    enqueue_job,
    fail_job,
    recover_stale_jobs,
    renew_job_lease,
)
from app.providers.hermes import SME_SYSTEM_POLICY, HermesResult
from app.services import chat, whatsapp
from app.services.agent_execution import ExecutionDenied, require_active_run
from app.services.conversation_scope import receiver_scope
from app.services.ingress_ordering import allocate_arrival, require_head
from app.services.run_reconciliation import ReconciliationAction, freeze_request
from app.services.run_recovery import reconcile_application_run
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session, sessionmaker


def _wait(event_value: threading.Event, label: str) -> None:
    if not event_value.wait(timeout=15):
        raise AssertionError(f"Timed out waiting for {label}")


def _database_waits_for_lock(engine: Any, pid: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            state = connection.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
        if state == "Lock":
            return
        time.sleep(0.02)
    raise AssertionError(
        "Concurrent PostgreSQL operation did not wait for its durable row lock"
    )


def _join(threads: list[threading.Thread], errors: list[BaseException]) -> None:
    for thread in threads:
        thread.join(timeout=25)
    if any(thread.is_alive() for thread in threads):
        raise AssertionError("Concurrent PostgreSQL worker did not terminate")
    if errors:
        raise AssertionError("Concurrent PostgreSQL worker failed") from errors[0]


def _thread(name: str, target: Any, errors: list[BaseException]) -> threading.Thread:
    def run() -> None:
        try:
            target()
        except Exception as exc:  # noqa: BLE001 - re-raised by the parent after joining every worker.
            errors.append(exc)

    return threading.Thread(name=name, target=run, daemon=True)


def _payload(tag: str, timestamp: str) -> dict[str, Any]:
    return {
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "67890"},
                            "messages": [
                                {
                                    "id": tag,
                                    "from": "15555550100",
                                    "timestamp": timestamp,
                                }
                            ],
                        }
                    }
                ],
            }
        ]
    }


def _ingress_gate(engine: Any, factory: Any, tag: str) -> dict[str, Any]:
    allocated = threading.Event()
    release_allocation = threading.Event()
    contender_started = threading.Event()
    first_head_held = threading.Event()
    release_head = threading.Event()
    second_head_started = threading.Event()
    outcomes: dict[str, Any] = {}
    errors: list[BaseException] = []

    def receive_first() -> None:
        with factory() as db:
            payload = _payload(tag + "-first", "9999999999")
            receipt = WebhookEvent(
                provider="whatsapp",
                external_event_id=f"core-{tag}-first",
                signature_valid=True,
                payload=payload,
            )
            db.add(receipt)
            db.flush()
            allocate_arrival(db, receipt, lane="test", payload=payload)
            outcomes["first"] = receipt.id
            outcomes["scope"] = receipt.ordering_scope
            outcomes["first_sequence"] = receipt.arrival_sequence
            allocated.set()
            _wait(release_allocation, "first receipt commit")
            db.commit()

    def receive_second() -> None:
        _wait(allocated, "first allocation")
        with factory() as db:
            outcomes["allocation_waiter_pid"] = db.scalar(
                text("SELECT pg_backend_pid()")
            )
            payload = _payload(tag + "-second", "1")
            receipt = WebhookEvent(
                provider="whatsapp",
                external_event_id=f"core-{tag}-second",
                signature_valid=True,
                payload=payload,
            )
            db.add(receipt)
            db.flush()
            contender_started.set()
            allocate_arrival(db, receipt, lane="test", payload=payload)
            outcomes["second"] = receipt.id
            outcomes["second_sequence"] = receipt.arrival_sequence
            db.commit()

    threads = [
        _thread("core-ingress-first", receive_first, errors),
        _thread("core-ingress-second", receive_second, errors),
    ]
    try:
        for thread in threads:
            thread.start()
        _wait(contender_started, "concurrent ingress allocation")
        _database_waits_for_lock(engine, outcomes["allocation_waiter_pid"])
    finally:
        release_allocation.set()
        _join(threads, errors)
    if (outcomes["first_sequence"], outcomes["second_sequence"]) != (1, 2):
        raise AssertionError(
            "Concurrent receipt admission did not preserve committed stream order"
        )
    with factory() as db:
        first, second = (
            db.get(WebhookEvent, outcomes["first"]),
            db.get(WebhookEvent, outcomes["second"]),
        )
        allocate_arrival(db, first, lane="test", payload=first.payload)
        if db.get(IngressStream, outcomes["scope"]).next_sequence != 3:
            raise AssertionError("Duplicate ingress receipt consumed another sequence")
        try:
            require_head(db, second)
        except JobDeferredError:
            db.rollback()
        else:
            raise AssertionError("Later receipt bypassed unresolved durable head")
        first.processing_status = "failed"
        db.commit()
        try:
            require_head(db, second)
        except JobDeferredError:
            db.rollback()
        else:
            raise AssertionError(
                "Failed earlier receipt ceased being an ordering barrier"
            )

    def finish_first() -> None:
        with factory() as db:
            first = db.get(WebhookEvent, outcomes["first"])
            require_head(db, first)
            first.processing_status = "processed"
            db.flush()
            first_head_held.set()
            _wait(release_head, "durable head disposition commit")
            db.commit()

    def claim_second() -> None:
        _wait(first_head_held, "first head row lock")
        with factory() as db:
            second = db.get(WebhookEvent, outcomes["second"])
            outcomes["head_waiter_pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            second_head_started.set()
            require_head(db, second)
            second.processing_status = "processed"
            db.commit()
            outcomes["second_admitted"] = True

    threads = [
        _thread("core-head-first", finish_first, errors),
        _thread("core-head-second", claim_second, errors),
    ]
    try:
        for thread in threads:
            thread.start()
        _wait(second_head_started, "second durable head claim")
        _database_waits_for_lock(engine, outcomes["head_waiter_pid"])
    finally:
        release_head.set()
        _join(threads, errors)
    if not outcomes.get("second_admitted"):
        raise AssertionError("Resolved head did not unblock the next receipt")
    return {
        "concurrent_allocation": True,
        "database_lock_wait_observed": True,
        "sequences": [1, 2],
        "failed_head_barrier": True,
        "serialized_head_transition": True,
        "duplicate_position_retained": True,
    }


def _lease_gate(factory: Any, tag: str, tenant_id: str) -> dict[str, Any]:
    configuration = replace(
        AsyncRuntimeConfig.from_env(), stale_lock_seconds=60, dispatch_batch_size=100
    )
    with factory() as db:
        job, _ = enqueue_job(
            db,
            kind="system.noop",
            payload={},
            idempotency_key=f"core-{tag}-epoch",
            tenant_id=tenant_id,
        )
        db.commit()
        job_id = job.id
        first = claim_job(db, job_id, "same-worker")
        old_epoch = first.claim_epoch
        first.locked_at = utcnow() - timedelta(seconds=120)
        db.commit()
        if renew_job_lease(
            db, job_id, "same-worker", claim_epoch=old_epoch, stale_lock_seconds=60
        ):
            raise AssertionError("Expired worker resurrected its lease")
        if recover_stale_jobs(db, configuration) != 1:
            raise AssertionError("Expired durable claim was not recovered")
        replacement = claim_job(db, job_id, "same-worker")
        new_epoch = replacement.claim_epoch
    barrier = threading.Barrier(3)
    outcomes: dict[str, bool] = {}
    errors: list[BaseException] = []

    def renew(label: str, epoch: int) -> None:
        with factory() as db:
            barrier.wait(timeout=15)
            outcomes[label] = renew_job_lease(
                db, job_id, "same-worker", claim_epoch=epoch, stale_lock_seconds=60
            )

    threads = [
        _thread("core-old-renew", lambda: renew("old", old_epoch), errors),
        _thread("core-new-renew", lambda: renew("new", new_epoch), errors),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=15)
    _join(threads, errors)
    if outcomes != {"old": False, "new": True} or new_epoch != old_epoch + 1:
        raise AssertionError("Same-worker identity bypassed the durable epoch fence")
    with factory() as db:
        try:
            complete_job(
                db,
                job_id,
                {"stale": True},
                worker_id="same-worker",
                claim_epoch=old_epoch,
            )
        except JobLeaseLostError:
            pass
        else:
            raise AssertionError("Old epoch overwrote the replacement result")
        if (
            fail_job(
                db,
                job_id,
                RuntimeError("synthetic"),
                configuration,
                worker_id="same-worker",
                claim_epoch=old_epoch,
            )
            != "lease_lost"
        ):
            raise AssertionError("Old epoch failed a replacement job")
        if (
            defer_job(
                db,
                job_id,
                JobDeferredError(),
                worker_id="same-worker",
                claim_epoch=old_epoch,
            )
            != "lease_lost"
        ):
            raise AssertionError("Old epoch deferred a replacement job")
    fence = JobFence(job_id, "same-worker", old_epoch, 60)
    original_factory = worker.SessionLocal

    class Wake:
        def heartbeat(self, _service: str, _worker: str) -> None:
            pass

    try:
        worker.SessionLocal = factory
        keeper = worker.JobLeaseKeeper(
            job_id=job_id,
            worker_id="same-worker",
            config=configuration,
            wake_queue=Wake(),
            fence=fence,
        )
        keeper._run()
    finally:
        worker.SessionLocal = original_factory
    if not fence.invalid:
        raise AssertionError(
            "Lost PostgreSQL renewal did not invalidate the in-process fence"
        )
    with factory() as db:
        with bind_job_fence(fence):
            try:
                assert_current_job_lease(db)
            except JobLeaseLostError:
                pass
            else:
                raise AssertionError(
                    "Lost renewal still authorized a subsequent effect"
                )
        complete_job(
            db,
            job_id,
            {"replacement": True},
            worker_id="same-worker",
            claim_epoch=new_epoch,
        )
    return {
        "same_worker_epochs": [old_epoch, new_epoch],
        "concurrent_old_renewal": False,
        "concurrent_new_renewal": True,
        "stale_completion_failure_deferral_denied": True,
        "expired_renewal_denied": True,
        "lease_keeper_invalidated": True,
    }


def _seed_run(
    db: Session,
    settings: Settings,
    tenant_id: str,
    user_id: str,
    profile_id: str,
    label: str,
) -> tuple[str, str, str]:
    conversation = Conversation(
        tenant_id=tenant_id,
        user_id=user_id,
        channel="web",
        status="open",
        context_generation=1,
        environment_lane="test",
    )
    db.add(conversation)
    db.flush()
    inbound = AgentMessage(
        tenant_id=tenant_id,
        user_id=user_id,
        hermes_profile_id=profile_id,
        conversation_id=conversation.id,
        channel="web",
        direction="inbound",
        content=f"Synthetic {label}",
        event_scope=receiver_scope("test", ("", "")),
    )
    db.add(inbound)
    db.flush()
    run = AgentExecutionRun(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation.id,
        initiating_message_id=inbound.id,
        status="running",
        expires_at=utcnow() - timedelta(seconds=1),
        provider_run_ids=[f"provider_{label}"],
    )
    db.add(run)
    db.flush()
    conversation.active_run_id = run.id
    envelope = {
        "payload": {
            "model": "synthetic-concurrency",
            "input": [{"role": "user", "content": inbound.content}],
            "instructions": SME_SYSTEM_POLICY
            + "\n\nCurrent application context:\nSynthetic context",
            "conversation_history": [],
        },
        "headers": {
            "Idempotency-Key": "bb_" + hashlib.sha256(inbound.id.encode()).hexdigest()
        },
        "endpoint": "http://hermes:8700/v1",
    }
    frozen = freeze_request(envelope)
    run.request_snapshots_encrypted = FieldCipher.from_settings(settings).encrypt(
        json.dumps(
            [
                {
                    "payload_json": frozen.payload_json,
                    "sha256": frozen.sha256,
                    "recoverable": True,
                }
            ]
        )
    )
    db.commit()
    return run.id, conversation.id, inbound.id


def _guard_lock_order_gate(
    engine: Any,
    factory: Any,
    settings: Settings,
    tenant_id: str,
    user_id: str,
    profile_id: str,
) -> dict[str, Any]:
    traces: dict[str, list[str]] = {}
    for mode in ("final_guard", "progress"):
        with factory() as db:
            run_id, conversation_id, input_id = _seed_run(
                db, settings, tenant_id, user_id, profile_id, mode
            )
            job, _ = enqueue_job(
                db,
                kind="system.noop",
                payload={},
                idempotency_key=f"core-{run_id}-guard",
                tenant_id=tenant_id,
            )
            db.commit()
            owned = claim_job(db, job.id, "guard-worker")
            fence = JobFence(owned.id, "guard-worker", owned.claim_epoch, 60)
            run = db.get(AgentExecutionRun, run_id)
            run.expires_at = utcnow() + timedelta(minutes=5)
            run.job_id, run.job_claim_epoch, run.job_stale_lock_seconds = (
                owned.id,
                owned.claim_epoch,
                60,
            )
            db.commit()
            phone = db.get(User, user_id).primary_phone_e164
        held = factory()
        held.scalar(
            select(AsyncJob).where(AsyncJob.id == fence.job_id).with_for_update()
        )
        started = threading.Event()
        errors: list[BaseException] = []
        outcomes: dict[str, Any] = {}
        trace: list[str] = []
        thread_name = "core-job-before-member-" + mode

        def observe(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _many: bool,
        ) -> None:
            sql = " ".join(statement.upper().split())
            if threading.current_thread().name == thread_name and "FOR UPDATE" in sql:
                for table in (
                    "ASYNC_JOBS",
                    "CONVERSATIONS",
                    "TENANT_MEMBERSHIPS",
                    "PHONE_IDENTITIES",
                ):
                    if table in sql:
                        trace.append(table.lower())
                        break

        class Meta:
            def mark_read(self, _message_id: str, *, typing: bool) -> None:
                if not typing:
                    raise AssertionError("Progress fixture did not request typing")
                outcomes["typing"] = True

        def guard() -> None:
            with factory() as db:
                outcomes["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                with bind_job_fence(fence):
                    if mode == "final_guard":
                        require_active_run(
                            db,
                            run_id=run_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            initiating_message_id=input_id,
                            channel="web",
                        )
                    else:
                        with (
                            patch.object(whatsapp, "SessionLocal", lambda: db),
                            patch.object(
                                whatsapp.MetaWhatsAppClient,
                                "for_inbound_reply",
                                return_value=Meta(),
                            ),
                        ):
                            progress = whatsapp._WhatsAppProgress(
                                settings=settings.model_copy(
                                    update={"whatsapp_backend": "meta"}
                                ),
                                event_id="synthetic-progress",
                                inbound_message_id="synthetic-message",
                                phone=phone,
                                tenant_id=tenant_id,
                                user_id=user_id,
                                meta_sender=("12345", "67890"),
                            )
                            progress._refresh_typing()
                    outcomes["passed"] = True
                db.rollback()

        event.listen(engine, "before_cursor_execute", observe)
        thread = _thread(thread_name, guard, errors)
        try:
            thread.start()
            _wait(started, mode + " job-row wait")
            _database_waits_for_lock(engine, outcomes["pid"])
            # NOWAIT proves that a guard blocked on the job does not already
            # own the member/conversation locks from the former inversion.
            with factory() as probe:
                probe.scalar(
                    select(Conversation.id)
                    .where(Conversation.id == conversation_id)
                    .with_for_update(nowait=True)
                )
                probe.scalar(
                    select(TenantMembership.id)
                    .where(
                        TenantMembership.tenant_id == tenant_id,
                        TenantMembership.user_id == user_id,
                    )
                    .with_for_update(nowait=True)
                )
                probe.rollback()
        finally:
            held.rollback()
            held.close()
            _join([thread], errors)
            event.remove(engine, "before_cursor_execute", observe)
        if not outcomes.get("passed") or not trace or trace[0] != "async_jobs":
            raise AssertionError(
                "Guard did not acquire job authority before actor/conversation rows"
            )
        if mode == "progress" and not outcomes.get("typing"):
            raise AssertionError(
                "Verified progress guard did not reach its synthetic typing boundary"
            )
        if "tenant_memberships" not in trace:
            raise AssertionError(
                "Guard fixture did not exercise its actor membership row lock"
            )
        traces[mode] = trace
    return {
        "job_lock_wait_observed": True,
        "member_and_conversation_nowait_probe_passed": True,
        "lock_traces": traces,
        "typing_provider_mocked": True,
    }


def _claim_gate(
    engine: Any, factory: Any, settings: Settings, ids: tuple[str, str, str]
) -> dict[str, Any]:
    run_id, _conversation_id, inbound_id = ids
    claim_written = threading.Event()
    release_claim = threading.Event()
    second_started = threading.Event()
    polling = threading.Event()
    release_poll = threading.Event()
    second_done = threading.Event()
    outcomes: dict[str, Any] = {}
    errors: list[BaseException] = []

    def observe(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        sql = " ".join(statement.upper().split())
        if (
            threading.current_thread().name == "core-recover-first"
            and sql.startswith("UPDATE AGENT_EXECUTION_RUNS SET")
            and "RECONCILE_EPOCH" in sql
            and not claim_written.is_set()
        ):
            claim_written.set()
            _wait(release_claim, "first reconciliation claim commit")

    def poll(_endpoint: Any, provider_id: str) -> dict[str, Any]:
        polling.set()
        _wait(release_poll, "first recovered model result")
        return {
            "run_id": provider_id,
            "status": "completed",
            "output": "Synthetic recovered claim answer",
        }

    def first() -> None:
        with factory() as db:
            outcomes["first"] = reconcile_application_run(
                db, run_id, settings, poll_override=poll
            )

    def second() -> None:
        _wait(claim_written, "uncommitted reconciliation claim")
        with factory() as db:
            outcomes["waiter_pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            second_started.set()
            outcomes["second"] = reconcile_application_run(
                db,
                run_id,
                settings,
                poll_override=lambda *_: (_ for _ in ()).throw(
                    AssertionError("Second reconciler polled an owned run")
                ),
            )
            second_done.set()

    event.listen(engine, "after_cursor_execute", observe)
    threads = [
        _thread("core-recover-first", first, errors),
        _thread("core-recover-second", second, errors),
    ]
    try:
        for thread in threads:
            thread.start()
        _wait(second_started, "competing reconciliation claim")
        _database_waits_for_lock(engine, outcomes["waiter_pid"])
        release_claim.set()
        _wait(polling, "committed reconciliation claim")
        _wait(second_done, "second reconciler's fenced decision")
    finally:
        release_claim.set()
        release_poll.set()
        _join(threads, errors)
        event.remove(engine, "after_cursor_execute", observe)
    if (
        outcomes["second"].action != ReconciliationAction.WAIT
        or outcomes["first"].action != ReconciliationAction.PERSIST_OUTPUT
    ):
        raise AssertionError(
            "Concurrent reconciliation claims did not produce one owner"
        )
    with factory() as db:
        run = db.get(AgentExecutionRun, run_id)
        replies = db.scalars(
            select(AgentMessage).where(
                AgentMessage.initiating_message_id == inbound_id,
                AgentMessage.direction == "outbound",
            )
        ).all()
        if (
            run.reconcile_epoch != 1
            or len(replies) != 1
            or run.output_message_id != replies[0].id
        ):
            raise AssertionError(
                "Concurrent recovery produced duplicate or unbound durable output"
            )
    return {
        "database_claim_lock_wait_observed": True,
        "one_reconcile_epoch": True,
        "competing_claim_waited_without_poll": True,
        "one_exact_parent_reply": True,
    }


def _publication_gate(
    factory: Any,
    settings: Settings,
    tenant_id: str,
    user_id: str,
    identity_id: str,
    label: str,
) -> dict[str, Any]:
    provider_entered = threading.Event()
    release_provider = threading.Event()
    publication_finished = threading.Event()
    recovery_polling = threading.Event()
    errors: list[BaseException] = []
    outcomes: dict[str, Any] = {}
    original_client = chat.HermesClient

    class Provider:
        def __init__(self, _settings: Settings):
            pass

        def respond(
            self,
            endpoint: Any,
            *,
            message: str,
            business_context: str,
            history: Any = None,
            request_id: str | None = None,
            on_prepared: Any = None,
            on_admitted: Any = None,
            **_options: Any,
        ) -> HermesResult:
            on_prepared(
                {
                    "payload": {
                        "model": endpoint.profile_name,
                        "input": [{"role": "user", "content": message}],
                        "instructions": SME_SYSTEM_POLICY
                        + "\n\nCurrent application context:\n"
                        + business_context,
                        "conversation_history": history or [],
                    },
                    "headers": {
                        "Idempotency-Key": "bb_"
                        + hashlib.sha256(str(request_id).encode()).hexdigest()
                    },
                    "endpoint": endpoint.api_url,
                }
            )
            on_admitted("provider_publication")
            provider_entered.set()
            _wait(release_provider, "stale provider answer")
            return HermesResult("Stale original answer must not publish", 1, 1, 2, 1)

    def original() -> None:
        try:
            with factory() as db:
                tenant, user = db.get(Tenant, tenant_id), db.get(User, user_id)
                chat.handle_chat(
                    db,
                    tenant=tenant,
                    user=user,
                    settings=settings,
                    channel="whatsapp",
                    message=f"Synthetic publication {label}",
                    media_metadata={
                        "type": "voice_call",
                        "phone_identity_id": identity_id,
                    },
                )
        except ExecutionDenied:
            outcomes["stale_publication_denied"] = True
        finally:
            publication_finished.set()

    def poll(_endpoint: Any, provider_id: str) -> dict[str, Any]:
        recovery_polling.set()
        _wait(publication_finished, "stale publisher rejection")
        return {
            "run_id": provider_id,
            "status": "completed",
            "output": "Verified recovered publication answer",
        }

    def recover() -> None:
        with factory() as db:
            outcomes["recovery"] = reconcile_application_run(
                db, outcomes["run_id"], settings, poll_override=poll
            )

    chat.HermesClient = Provider
    threads = [_thread("core-stale-publisher", original, errors)]
    try:
        threads[0].start()
        _wait(provider_entered, "original admitted provider request")
        with factory() as db:
            run = db.scalar(
                select(AgentExecutionRun).where(
                    AgentExecutionRun.tenant_id == tenant_id,
                    AgentExecutionRun.status == "running",
                    AgentExecutionRun.initiating_message_id.in_(
                        select(AgentMessage.id).where(
                            AgentMessage.tenant_id == tenant_id,
                            AgentMessage.user_id == user_id,
                            AgentMessage.content == f"Synthetic publication {label}",
                        )
                    ),
                )
            )
            if run is None:
                raise AssertionError(
                    "Original publication run was not durably admitted"
                )
            outcomes["run_id"], outcomes["input_id"] = run.id, run.initiating_message_id
            run.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        threads.append(_thread("core-publication-recovery", recover, errors))
        threads[1].start()
        _wait(recovery_polling, "recovery publication ownership")
    finally:
        release_provider.set()
        _join(threads, errors)
        chat.HermesClient = original_client
    if (
        not outcomes.get("stale_publication_denied")
        or outcomes["recovery"].action != ReconciliationAction.PERSIST_OUTPUT
    ):
        raise AssertionError(
            "Expired original publisher was not fenced by the recovery claim"
        )
    with factory() as db:
        replies = db.scalars(
            select(AgentMessage).where(
                AgentMessage.initiating_message_id == outcomes["input_id"],
                AgentMessage.direction == "outbound",
            )
        ).all()
        run = db.get(AgentExecutionRun, outcomes["run_id"])
        if (
            len(replies) != 1
            or replies[0].content != "Verified recovered publication answer"
            or run.output_message_id != replies[0].id
            or run.status != "completed"
            or replies[0].media_metadata.get("voice_playback_state") != "generated"
        ):
            raise AssertionError(
                "Final publication was not serialized to one exact recovered answer"
            )
    return {
        "actual_handle_chat_publisher_fenced": True,
        "recovery_claim_revoked_original_capability": True,
        "one_exact_recovered_reply": True,
        "voice_output_still_unheard": True,
        "no_external_provider_calls": True,
    }


def verify_core_concurrency(engine: Any, tag: str) -> dict[str, Any]:
    if (
        engine.dialect.name != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.database != "bumpa_core_synthetic"
    ):
        raise AssertionError(
            "Core concurrency gate requires the owned disposable loopback database"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_backend="hermes",
        field_encryption_key="synthetic-core-concurrency-key",
    )
    tenant_id = user_id = None
    try:
        with factory() as db:
            tenant = Tenant(slug=f"core-{tag}", name="Synthetic concurrent business")
            user = User(primary_phone_e164=f"+1555{int(tag, 16) % 10_000_000:07d}")
            db.add_all((tenant, user))
            db.flush()
            tenant_id, user_id = tenant.id, user.id
            profile = HermesProfile(
                tenant_id=tenant_id,
                profile_name="synthetic-concurrency",
                provider="hermes",
                profile_path="/synthetic/concurrency",
                api_port=8700,
                api_internal_url="http://hermes:8700/v1",
                encrypted_api_key=FieldCipher.from_settings(settings).encrypt(
                    "synthetic-private-key"
                ),
            )
            db.add_all(
                (
                    profile,
                    TenantMembership(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        role="owner",
                        status="active",
                    ),
                )
            )
            db.flush()
            profile_id = profile.id
            identity = PhoneIdentity(
                tenant_id=tenant_id,
                user_id=user_id,
                phone_e164=user.primary_phone_e164,
                status="approved",
                opt_out=False,
            )
            db.add(identity)
            db.flush()
            identity_id = identity.id
            db.commit()
        ingress = _ingress_gate(engine, factory, tag)
        leases = _lease_gate(factory, tag, tenant_id)
        with factory() as db:
            ids = _seed_run(db, settings, tenant_id, user_id, profile_id, "claim")
        claims = _claim_gate(engine, factory, settings, ids)
        guard_order = _guard_lock_order_gate(
            engine, factory, settings, tenant_id, user_id, profile_id
        )
        publication = _publication_gate(
            factory, settings, tenant_id, user_id, identity_id, tag
        )
        return {
            "ingress_ordering": ingress,
            "job_epoch_fencing": leases,
            "recovery_claims": claims,
            "job_before_member_lock_order": guard_order,
            "final_publication": publication,
        }
    finally:
        with factory() as db:
            if tenant_id is not None:
                job_ids = select(AsyncJob.id).where(AsyncJob.tenant_id == tenant_id)
                db.execute(delete(JobOutbox).where(JobOutbox.job_id.in_(job_ids)))
                db.execute(delete(AsyncJob).where(AsyncJob.tenant_id == tenant_id))
                db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            if user_id is not None:
                db.execute(delete(User).where(User.id == user_id))
            scoped_events = db.scalars(
                select(WebhookEvent).where(
                    WebhookEvent.external_event_id.like(f"core-{tag}-%")
                )
            ).all()
            scopes = {
                receipt.ordering_scope
                for receipt in scoped_events
                if receipt.ordering_scope
            }
            for receipt in scoped_events:
                db.delete(receipt)
            if scopes:
                db.execute(
                    delete(IngressStream).where(IngressStream.scope_key.in_(scopes))
                )
            db.commit()
