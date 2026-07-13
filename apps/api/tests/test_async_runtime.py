from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.db.models import AsyncJob, JobOutbox
from app.db.session import SessionLocal
from app.jobs import health, scheduler, worker
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    RedisHealthProbe,
    RedisWakeQueue,
    claim_job,
    complete_job,
    dispatch_due_jobs,
    enqueue_job,
    fail_job,
    recover_stale_jobs,
    recover_stale_wakeups,
    register_handler,
    replay_dead_letter,
)
from app.jobs.worker import process_one


class FakeWakeQueue:
    def __init__(self, queue_name: str, *, fail_publish: bool = False) -> None:
        self.items: list[str] = []
        self.fail_publish = fail_publish
        self.config = SimpleNamespace(
            queue_name=queue_name, retry_base_seconds=1, retry_max_seconds=10
        )

    def publish(self, job_id: str) -> None:
        if self.fail_publish:
            raise ConnectionError("credential-must-not-be-persisted")
        self.items.append(job_id)

    def heartbeat(self, _service: str, _instance_id: str) -> None:
        return None

    def pop(self) -> str | None:
        return self.items.pop(0) if self.items else None


class ObservedWakeQueue(FakeWakeQueue):
    def __init__(self, queue_name: str) -> None:
        super().__init__(queue_name)
        self.heartbeat_times: list[float] = []
        self._heartbeat_condition = threading.Condition()

    def heartbeat(self, _service: str, _instance_id: str) -> None:
        with self._heartbeat_condition:
            self.heartbeat_times.append(time.monotonic())
            self._heartbeat_condition.notify_all()

    def wait_for_heartbeats(self, count: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._heartbeat_condition:
            while len(self.heartbeat_times) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._heartbeat_condition.wait(remaining)
        return True


class FakeRedis:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.values: dict[str, str] = {}

    def rpush(self, _key: str, value: str) -> int:
        self.items.append(value)
        return len(self.items)

    def blpop(self, _keys, timeout: int):  # type: ignore[no-untyped-def]
        del timeout
        return ["queue", self.items.pop(0)] if self.items else None

    def set(self, key: str, value: str, *, ex: int) -> bool:
        assert ex > 0
        self.values[key] = value
        return True

    def exists(self, key: str) -> int:
        return int(key in self.values)

    def pipeline(self, *, transaction: bool):  # type: ignore[no-untyped-def]
        assert transaction is False
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis

    def ping(self):  # type: ignore[no-untyped-def]
        return self

    def exists(self, _key: str):  # type: ignore[no-untyped-def]
        return self

    def llen(self, _key: str):  # type: ignore[no-untyped-def]
        return self

    def execute(self) -> list[object]:
        return [True, 1, 1, len(self.redis.items)]


@pytest.fixture(scope="module", autouse=True)
def ensure_application_schema(client):  # type: ignore[no-untyped-def]
    yield


def config() -> AsyncRuntimeConfig:
    return AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="default",
        queue_key_prefix="test",
        heartbeat_ttl_seconds=45,
        pop_timeout_seconds=1,
        scheduler_interval_seconds=0.01,
        dispatch_batch_size=100,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=10,
        stale_lock_seconds=60,
    )


def test_runtime_config_and_redis_health_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASYNC_RUNTIME_ENABLED", "yes")
    monkeypatch.setenv("ASYNC_QUEUE_NAME", "priority")
    configured = AsyncRuntimeConfig.from_env()
    assert configured.enabled and configured.queue_key.endswith(":priority")
    assert configured.heartbeat_key("worker").endswith(":health:worker")

    fake_redis = FakeRedis()
    queue = RedisWakeQueue(configured, cast(object, fake_redis))  # type: ignore[arg-type]
    queue.publish("job-1")
    assert queue.pop() == "job-1" and queue.pop() is None
    queue.heartbeat("worker", "worker-1")
    queue.heartbeat("scheduler", "scheduler-1")

    health_probe = RedisHealthProbe(configured, cast(object, fake_redis))  # type: ignore[arg-type]
    assert health_probe.is_healthy("worker")
    assert health_probe.health_snapshot() == {
        "redis": "ok",
        "worker": "ok",
        "scheduler": "ok",
        "queued_wakeups": 0,
    }


def test_redis_health_probe_uses_a_bounded_non_blocking_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_redis = FakeRedis()

    def fake_from_url(url: str, **kwargs: object) -> object:
        captured.update({"url": url, **kwargs})
        return fake_redis

    monkeypatch.setattr("app.jobs.runtime.Redis.from_url", fake_from_url)

    probe = RedisHealthProbe(config())

    assert probe.client is fake_redis
    assert captured == {
        "url": "redis://unused",
        "decode_responses": True,
        "socket_connect_timeout": 1,
        "socket_timeout": 1,
        "health_check_interval": 0,
    }


def test_scheduler_enqueues_one_durable_job_per_daily_retention_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="production",
            operational_retention_batch_size=500,
        ),
    )
    with SessionLocal() as session:
        scheduler._ensure_daily_maintenance(session)
        scheduler._ensure_daily_maintenance(session)
        jobs = list(
            session.scalars(
                select(AsyncJob).where(AsyncJob.kind == "research.cleanup_expired_artifacts")
            )
        )
        assert len(jobs) == 1
        assert jobs[0].payload == {"limit": 1000}
        assert session.scalar(select(JobOutbox).where(JobOutbox.job_id == jobs[0].id)) is not None
        operational_jobs = list(
            session.scalars(
                select(AsyncJob).where(AsyncJob.kind == "system.cleanup_operational_history")
            )
        )
        assert len(operational_jobs) == 1
        assert operational_jobs[0].payload == {"limit": 500}
        assert (
            session.scalar(select(JobOutbox).where(JobOutbox.job_id == operational_jobs[0].id))
            is not None
        )
        session.rollback()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ASYNC_RUNTIME_ENABLED", "maybe", "true or false"),
        ("ASYNC_QUEUE_NAME", "bad:name", "QUEUE_NAME"),
        ("ASYNC_QUEUE_KEY_PREFIX", "bad:prefix", "QUEUE_KEY_PREFIX"),
        ("ASYNC_HEARTBEAT_TTL_SECONDS", "14", "at least 15"),
        ("ASYNC_POP_TIMEOUT_SECONDS", "45", "below heartbeat"),
        ("ASYNC_SCHEDULER_INTERVAL_SECONDS", "0", "positive"),
        ("ASYNC_DISPATCH_BATCH_SIZE", "1001", "between 1 and 1000"),
        ("ASYNC_REDISPATCH_SECONDS", "20", "at least the heartbeat"),
        ("ASYNC_RETRY_BASE_SECONDS", "0", "retry settings"),
        ("ASYNC_RETRY_MAX_SECONDS", "1", "retry settings"),
        ("ASYNC_STALE_LOCK_SECONDS", "20", "at least the heartbeat"),
        ("ASYNC_POP_TIMEOUT_SECONDS", "not-an-int", "integer"),
        ("ASYNC_SCHEDULER_INTERVAL_SECONDS", "not-a-number", "numeric"),
    ],
)
def test_runtime_config_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    baseline = {
        "ASYNC_RUNTIME_ENABLED": "true",
        "ASYNC_QUEUE_NAME": "default",
        "ASYNC_HEARTBEAT_TTL_SECONDS": "45",
        "ASYNC_POP_TIMEOUT_SECONDS": "5",
        "ASYNC_SCHEDULER_INTERVAL_SECONDS": "2",
        "ASYNC_DISPATCH_BATCH_SIZE": "100",
        "ASYNC_REDISPATCH_SECONDS": "60",
        "ASYNC_RETRY_BASE_SECONDS": "5",
        "ASYNC_RETRY_MAX_SECONDS": "900",
        "ASYNC_STALE_LOCK_SECONDS": "300",
    }
    for key, setting in baseline.items():
        monkeypatch.setenv(key, setting)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        AsyncRuntimeConfig.from_env()


def test_transactional_outbox_idempotency_and_duplicate_delivery() -> None:
    queue_name = "test-idempotent"
    wake = FakeWakeQueue(queue_name)
    with SessionLocal() as session:
        job, created = enqueue_job(
            session,
            kind="system.noop",
            payload={"safe": True},
            idempotency_key="async-idempotent-1",
            queue_name=queue_name,
        )
        duplicate, duplicate_created = enqueue_job(
            session,
            kind="system.noop",
            payload={"safe": False},
            idempotency_key="async-idempotent-1",
            queue_name=queue_name,
        )
        session.commit()
        assert created is True and duplicate_created is False
        assert duplicate.id == job.id
        assert dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10) == 1
        assert wake.items == [job.id]
        # At-least-once Redis delivery is safe because only one worker can claim
        # a durable non-terminal job record.
        assert (
            process_one(session, job_id=job.id, worker_id="worker-1", config=config())
            == "succeeded"
        )
        assert (
            process_one(session, job_id=job.id, worker_id="worker-2", config=config()) == "ignored"
        )
        stored = session.get(AsyncJob, job.id)
        assert stored and stored.status == "succeeded" and stored.attempts == 1


def test_retry_backoff_then_success() -> None:
    calls = 0

    @register_handler("test.transient")
    def transient(_session, _job):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("provider-token-must-not-be-persisted")
        return {"ok": True}

    queue_name = "test-retry"
    wake = FakeWakeQueue(queue_name)
    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="test.transient",
            payload={},
            idempotency_key="async-retry-1",
            queue_name=queue_name,
            max_attempts=3,
        )
        session.commit()
        dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10)
        assert process_one(session, job_id=job.id, worker_id="worker-1", config=config()) == "retry"
        stored = session.get(AsyncJob, job.id)
        assert stored and stored.status == "retry"
        assert stored.last_error == "TimeoutError: job execution failed"
        assert "token" not in stored.last_error
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox and outbox.status == "pending"
        stored.available_at = utcnow()
        outbox.available_at = stored.available_at
        session.commit()
        dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10)
        assert (
            process_one(session, job_id=job.id, worker_id="worker-1", config=config())
            == "succeeded"
        )
        assert session.get(AsyncJob, job.id).attempts == 2  # type: ignore[union-attr]


def test_unknown_kind_dead_letters_and_explicit_replay() -> None:
    queue_name = "test-dead-letter"
    wake = FakeWakeQueue(queue_name)
    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="unknown.kind",
            payload={},
            idempotency_key="async-dead-letter-1",
            queue_name=queue_name,
        )
        session.commit()
        dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10)
        assert (
            process_one(session, job_id=job.id, worker_id="worker-1", config=config())
            == "dead_letter"
        )
        dead = session.get(AsyncJob, job.id)
        assert dead and dead.status == "dead_letter" and dead.finished_at is not None
        replayed = replay_dead_letter(session, job.id, max_attempts=2)
        assert replayed.status == "retry" and replayed.attempts == 0
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox and outbox.status == "pending"


def test_queue_outage_keeps_outbox_pending_without_leaking_error() -> None:
    queue_name = "test-outage"
    wake = FakeWakeQueue(queue_name, fail_publish=True)
    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-queue-outage-1",
            queue_name=queue_name,
        )
        session.commit()
        assert dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10) == 0
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox and outbox.status == "pending" and outbox.dispatch_attempts == 1
        assert outbox.last_error == "ConnectionError: queue publish failed"


def test_stale_worker_lease_is_recovered_for_redispatch() -> None:
    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-stale-1",
            queue_name="test-stale",
        )
        session.commit()
        claimed = claim_job(session, job.id, "dead-worker")
        assert claimed
        claimed.locked_at = utcnow() - timedelta(seconds=120)
        session.commit()
        assert recover_stale_jobs(session, config()) == 1
        recovered = session.get(AsyncJob, job.id)
        assert recovered and recovered.status == "retry" and recovered.locked_by is None
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox and outbox.status == "pending"


def test_long_handler_renews_worker_heartbeat_and_job_lease() -> None:
    """A handler can exceed both liveness thresholds without being redispatched."""
    entered = threading.Event()
    release = threading.Event()

    @register_handler("test.long-running-lease")
    def long_running(_session, _job):  # type: ignore[no-untyped-def]
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release long-running handler")
        return {"lease": "kept"}

    long_config = replace(config(), heartbeat_ttl_seconds=1, stale_lock_seconds=1)
    wake = ObservedWakeQueue(long_config.queue_name)
    result: list[str] = []

    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="test.long-running-lease",
            payload={},
            idempotency_key="async-long-running-lease-1",
        )
        session.commit()
        job_id = job.id

    def run_job() -> None:
        with SessionLocal() as worker_session:
            result.append(
                process_one(
                    worker_session,
                    job_id=job_id,
                    worker_id="long-worker",
                    config=long_config,
                    wake_queue=cast(RedisWakeQueue, wake),
                )
            )

    worker_thread = threading.Thread(target=run_job)
    worker_thread.start()
    try:
        assert entered.wait(timeout=2)
        # The first pulse is immediate. Five observed pulses span four renewal
        # intervals (4/3 seconds), deterministically exceeding both 1s limits.
        assert wake.wait_for_heartbeats(5, timeout=3)
        assert wake.heartbeat_times[-1] - wake.heartbeat_times[0] >= 1.0
        assert all(
            later - earlier < long_config.heartbeat_ttl_seconds
            for earlier, later in zip(wake.heartbeat_times, wake.heartbeat_times[1:], strict=False)
        )
        with SessionLocal() as scheduler_session:
            assert recover_stale_jobs(scheduler_session, long_config) == 0
            running_job = scheduler_session.get(AsyncJob, job_id)
            assert running_job and running_job.status == "running"
            assert running_job.locked_by == "long-worker"
    finally:
        release.set()
        worker_thread.join(timeout=3)

    assert not worker_thread.is_alive()
    assert result == ["succeeded"]
    with SessionLocal() as session:
        completed = session.get(AsyncJob, job_id)
        assert completed and completed.status == "succeeded"


def test_lease_renewal_and_completion_are_fenced_to_owner() -> None:
    from app.jobs.runtime import renew_job_lease

    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-owner-fence-1",
        )
        session.commit()
        assert claim_job(session, job.id, "worker-a")
        assert renew_job_lease(session, job.id, "worker-b") is False
        with pytest.raises(RuntimeError, match="no longer owned"):
            complete_job(session, job.id, None, worker_id="worker-b")
        assert (
            fail_job(
                session,
                job.id,
                RuntimeError("must not mutate"),
                config(),
                worker_id="worker-b",
            )
            == "lease_lost"
        )
        assert renew_job_lease(session, job.id, "worker-a") is True
        complete_job(session, job.id, {"owner": "worker-a"}, worker_id="worker-a")


def test_stale_redis_wakeup_is_safely_republished() -> None:
    queue_name = "test-lost-wakeup"
    wake = FakeWakeQueue(queue_name)
    configured = replace(config(), queue_name=queue_name)
    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-lost-wakeup-1",
            queue_name=queue_name,
        )
        session.commit()
        assert dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10) == 1
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox
        outbox.dispatched_at = utcnow() - timedelta(seconds=120)
        session.commit()
        assert recover_stale_wakeups(session, configured) == 1
        assert outbox.status == "pending" and outbox.dispatched_at is None


def test_terminal_dispatch_claim_and_completion_guards() -> None:
    queue_name = "test-terminal"
    wake = FakeWakeQueue(queue_name)
    with SessionLocal() as session:
        job, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-terminal-1",
            queue_name=queue_name,
        )
        job.status = "cancelled"
        session.commit()
        assert dispatch_due_jobs(session, cast(RedisWakeQueue, wake), limit=10) == 0
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox and outbox.status == "dispatched"
        assert claim_job(session, "missing-job", "worker") is None
        assert claim_job(session, job.id, "worker") is None
        with pytest.raises(RuntimeError, match="no longer owned"):
            complete_job(session, job.id, None, worker_id="worker")


def test_future_claim_failure_budget_and_stale_dead_letter() -> None:
    with SessionLocal() as session:
        future, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-future-1",
            queue_name="test-future",
            available_at=utcnow() + timedelta(minutes=5),
        )
        session.commit()
        assert claim_job(session, future.id, "worker") is None
        assert (
            fail_job(
                session,
                "missing-job",
                RuntimeError(),
                config(),
                worker_id="worker",
            )
            == "missing"
        )

        exhausted, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-exhausted-1",
            queue_name="test-exhausted",
            max_attempts=1,
        )
        session.commit()
        assert claim_job(session, exhausted.id, "worker")
        assert (
            fail_job(
                session,
                exhausted.id,
                RuntimeError("hidden"),
                config(),
                worker_id="worker",
            )
            == "dead_letter"
        )

        stale, _ = enqueue_job(
            session,
            kind="system.noop",
            payload={},
            idempotency_key="async-stale-dead-1",
            queue_name="test-stale-dead",
            max_attempts=1,
        )
        session.commit()
        claimed = claim_job(session, stale.id, "dead-worker")
        assert claimed
        claimed.locked_at = utcnow() - timedelta(seconds=120)
        session.commit()
        assert recover_stale_jobs(session, config()) == 1
        dead = session.get(AsyncJob, stale.id)
        assert dead and dead.status == "dead_letter"


def test_enqueue_and_replay_validation() -> None:
    with SessionLocal() as session:
        with pytest.raises(ValueError, match="required"):
            enqueue_job(session, kind="", payload={}, idempotency_key="x")
        with pytest.raises(ValueError, match="between 1 and 100"):
            enqueue_job(
                session, kind="system.noop", payload={}, idempotency_key="x", max_attempts=0
            )
        with pytest.raises(ValueError, match="Only dead-letter"):
            replay_dead_letter(session, "missing")

        job, _ = enqueue_job(
            session,
            kind="unknown.replay",
            payload={},
            idempotency_key="async-replay-invalid-1",
            queue_name="test-replay-invalid",
        )
        job.status = "dead_letter"
        session.commit()
        with pytest.raises(ValueError, match="between 1 and 100"):
            replay_dead_letter(session, job.id, max_attempts=0)


def test_health_command_and_enabled_entrypoint_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = config()
    monkeypatch.setattr(health.sys, "argv", ["health", "worker"])
    monkeypatch.setattr(health, "_heartbeat_exists", lambda _service: True)
    health.main()
    monkeypatch.setattr(health.sys, "argv", ["health"])
    with pytest.raises(SystemExit, match="Usage"):
        health.main()
    monkeypatch.setattr(health.sys, "argv", ["health", "scheduler"])
    monkeypatch.setattr(health, "_heartbeat_exists", lambda _service: False)
    with pytest.raises(SystemExit) as unavailable:
        health.main()
    assert unavailable.value.code == 1

    worker_queue = FakeWakeQueue(configured.queue_name)
    monkeypatch.setattr(worker.AsyncRuntimeConfig, "from_env", lambda: configured)
    monkeypatch.setattr(worker, "RedisWakeQueue", lambda _config: worker_queue)
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    worker.running = True

    def stop_worker() -> None:
        worker.running = False
        return None

    worker_queue.pop = stop_worker  # type: ignore[method-assign]
    worker.main()

    scheduler_queue = FakeWakeQueue(configured.queue_name)
    monkeypatch.setattr(scheduler.AsyncRuntimeConfig, "from_env", lambda: configured)
    monkeypatch.setattr(scheduler, "RedisWakeQueue", lambda _config: scheduler_queue)
    monkeypatch.setattr(scheduler.signal, "signal", lambda *_args: None)
    scheduler.running = True
    monkeypatch.setattr(scheduler.time, "sleep", lambda _seconds: scheduler._stop(15, object()))
    scheduler.main()
