from __future__ import annotations

import json
import math
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from redis import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import AsyncJob, JobOutbox, SystemError

JobResult = dict[str, Any] | None
JobHandler = Callable[[Session, AsyncJob], JobResult]


class PermanentJobError(RuntimeError):
    """A safe, non-retriable failure that should move a job to the dead letter state."""


class JobDeferredError(RuntimeError):
    """The work is eligible later; waiting must not consume its retry budget."""

    def __init__(
        self, reason: str = "Job is waiting for earlier work", *, delay_seconds: float = 1
    ):
        super().__init__(reason)
        if not math.isfinite(delay_seconds) or not 0 < delay_seconds <= 3600:
            raise ValueError("Job deferral must be between zero and one hour")
        self.delay_seconds = delay_seconds


class JobLeaseLostError(RuntimeError):
    """The current execution no longer owns its exact, unexpired claim."""


@dataclass(frozen=True, slots=True)
class JobFence:
    job_id: str
    worker_id: str
    claim_epoch: int
    stale_lock_seconds: int
    _invalid: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    @property
    def invalid(self) -> bool:
        return self._invalid.is_set()

    def invalidate(self) -> None:
        self._invalid.set()


_current_job_fence: ContextVar[JobFence | None] = ContextVar("current_job_fence", default=None)


def current_job_fence() -> JobFence | None:
    return _current_job_fence.get()


@contextmanager
def bind_job_fence(fence: JobFence) -> Iterator[None]:
    token = _current_job_fence.set(fence)
    try:
        yield
    finally:
        _current_job_fence.reset(token)


def assert_job_lease(
    session: Session,
    *,
    job_id: str,
    claim_epoch: int,
    stale_lock_seconds: int,
    worker_id: str | None = None,
) -> None:
    """Lock and verify a persisted claim before an application effect.

    Keep this transaction open through the effect's durable result commit. The
    row lock prevents concurrent scheduler recovery during that boundary.
    Persisted run capabilities may supply the epoch without an in-process fence.
    """
    job = session.scalar(
        select(AsyncJob)
        .where(AsyncJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not job
        or job.status != "running"
        or job.claim_epoch != claim_epoch
        or (worker_id is not None and job.locked_by != worker_id)
        or not job.locked_by
        or job.locked_at is None
        or claim_epoch < 1
        or stale_lock_seconds <= 0
        or _as_utc(job.locked_at) <= utcnow() - timedelta(seconds=stale_lock_seconds)
    ):
        raise JobLeaseLostError("Job lease is no longer owned by this execution")


def assert_current_job_lease(session: Session) -> None:
    fence = current_job_fence()
    if fence is None:
        return
    if fence.invalid:
        raise JobLeaseLostError("Job lease renewal was lost")
    try:
        assert_job_lease(
            session,
            job_id=fence.job_id,
            worker_id=fence.worker_id,
            claim_epoch=fence.claim_epoch,
            stale_lock_seconds=fence.stale_lock_seconds,
        )
    except JobLeaseLostError:
        fence.invalidate()
        raise


@dataclass(frozen=True)
class AsyncRuntimeConfig:
    enabled: bool
    redis_url: str
    queue_name: str
    queue_key_prefix: str
    heartbeat_ttl_seconds: int
    pop_timeout_seconds: int
    scheduler_interval_seconds: float
    dispatch_batch_size: int
    redispatch_seconds: int
    retry_base_seconds: int
    retry_max_seconds: int
    stale_lock_seconds: int

    @classmethod
    def from_env(cls) -> AsyncRuntimeConfig:
        config = cls(
            enabled=_bool_env("ASYNC_RUNTIME_ENABLED", default=False),
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
            queue_name=os.getenv("ASYNC_QUEUE_NAME", "default"),
            queue_key_prefix=os.getenv("ASYNC_QUEUE_KEY_PREFIX", "bumpabestie"),
            heartbeat_ttl_seconds=_int_env("ASYNC_HEARTBEAT_TTL_SECONDS", 45),
            pop_timeout_seconds=_int_env("ASYNC_POP_TIMEOUT_SECONDS", 5),
            scheduler_interval_seconds=_float_env("ASYNC_SCHEDULER_INTERVAL_SECONDS", 2.0),
            dispatch_batch_size=_int_env("ASYNC_DISPATCH_BATCH_SIZE", 100),
            redispatch_seconds=_int_env("ASYNC_REDISPATCH_SECONDS", 60),
            retry_base_seconds=_int_env("ASYNC_RETRY_BASE_SECONDS", 5),
            retry_max_seconds=_int_env("ASYNC_RETRY_MAX_SECONDS", 900),
            stale_lock_seconds=_int_env("ASYNC_STALE_LOCK_SECONDS", 300),
        )
        if not config.queue_name or ":" in config.queue_name:
            raise ValueError("ASYNC_QUEUE_NAME must be non-empty and cannot contain ':'")
        if re.fullmatch(r"[A-Za-z0-9._-]+", config.queue_key_prefix) is None:
            raise ValueError("ASYNC_QUEUE_KEY_PREFIX contains invalid characters")
        if config.heartbeat_ttl_seconds < 15:
            raise ValueError("ASYNC_HEARTBEAT_TTL_SECONDS must be at least 15")
        if not 1 <= config.pop_timeout_seconds < config.heartbeat_ttl_seconds:
            raise ValueError("ASYNC_POP_TIMEOUT_SECONDS must be positive and below heartbeat TTL")
        if config.scheduler_interval_seconds <= 0:
            raise ValueError("ASYNC_SCHEDULER_INTERVAL_SECONDS must be positive")
        if not 1 <= config.dispatch_batch_size <= 1000:
            raise ValueError("ASYNC_DISPATCH_BATCH_SIZE must be between 1 and 1000")
        if config.redispatch_seconds < config.heartbeat_ttl_seconds:
            raise ValueError("ASYNC_REDISPATCH_SECONDS must be at least the heartbeat TTL")
        if config.retry_base_seconds < 1 or config.retry_max_seconds < config.retry_base_seconds:
            raise ValueError("Async retry settings are invalid")
        if config.stale_lock_seconds < config.heartbeat_ttl_seconds:
            raise ValueError("ASYNC_STALE_LOCK_SECONDS must be at least the heartbeat TTL")
        return config

    @property
    def queue_key(self) -> str:
        return f"{self.queue_key_prefix}:jobs:{self.queue_name}"

    def heartbeat_key(self, service: str) -> str:
        return f"{self.queue_key_prefix}:health:{service}"


class RedisWakeQueue:
    def __init__(self, config: AsyncRuntimeConfig, client: Redis | None = None) -> None:
        self.config = config
        self.client = client or Redis.from_url(
            config.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=max(3, config.pop_timeout_seconds + 2),
            health_check_interval=30,
        )

    def publish(self, job_id: str) -> None:
        self.client.rpush(self.config.queue_key, job_id)

    def pop(self) -> str | None:
        item = cast(
            list[str] | None,
            self.client.blpop([self.config.queue_key], timeout=self.config.pop_timeout_seconds),
        )
        return item[1] if item else None

    def heartbeat(self, service: str, instance_id: str) -> None:
        payload = json.dumps(
            {"instance_id": instance_id, "at": utcnow().isoformat()}, separators=(",", ":")
        )
        self.client.set(
            self.config.heartbeat_key(service),
            payload,
            ex=self.config.heartbeat_ttl_seconds,
        )


class RedisHealthProbe:
    """Probe Redis and async heartbeats within the readiness-check budget.

    Queue consumers need a socket timeout longer than their blocking pop. Health
    checks do not: they must fail fast enough for both HTTP callers and container
    health checks to observe the dependency outage. Keeping a dedicated client
    prevents the queue's blocking-pop timeout from leaking into readiness.
    """

    def __init__(self, config: AsyncRuntimeConfig, client: Redis | None = None) -> None:
        self.config = config
        self.client = client or Redis.from_url(
            config.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=0,
        )

    def is_healthy(self, service: str) -> bool:
        return bool(self.client.exists(self.config.heartbeat_key(service)))

    def health_snapshot(self) -> dict[str, Any]:
        pipe = self.client.pipeline(transaction=False)
        pipe.ping()
        pipe.exists(self.config.heartbeat_key("worker"))
        pipe.exists(self.config.heartbeat_key("scheduler"))
        pipe.llen(self.config.queue_key)
        redis_ok, worker, scheduler, queued_ids = pipe.execute()
        return {
            "redis": "ok" if redis_ok else "unavailable",
            "worker": "ok" if worker else "stale",
            "scheduler": "ok" if scheduler else "stale",
            "queued_wakeups": int(queued_ids),
        }


class JobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, kind: str, handler: JobHandler) -> None:
        if not kind or kind in self._handlers:
            raise ValueError(f"Job handler is already registered or invalid: {kind}")
        self._handlers[kind] = handler

    def handler_for(self, kind: str) -> JobHandler:
        try:
            return self._handlers[kind]
        except KeyError as exc:
            raise PermanentJobError(f"No handler is registered for job kind {kind}") from exc


registry = JobRegistry()


def register_handler(kind: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(handler: JobHandler) -> JobHandler:
        registry.register(kind, handler)
        return handler

    return decorator


def enqueue_job(
    session: Session,
    *,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    tenant_id: str | None = None,
    queue_name: str = "default",
    max_attempts: int = 5,
    available_at: datetime | None = None,
) -> tuple[AsyncJob, bool]:
    """Create a job and its outbox handoff in the caller's database transaction."""
    if not kind or not idempotency_key:
        raise ValueError("kind and idempotency_key are required")
    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    existing = session.scalar(
        select(AsyncJob).where(
            AsyncJob.queue_name == queue_name,
            AsyncJob.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing, False

    due = available_at or utcnow()
    job = AsyncJob(
        tenant_id=tenant_id,
        queue_name=queue_name,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        available_at=due,
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
            session.add(JobOutbox(tenant_id=tenant_id, job_id=job.id, available_at=due))
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(AsyncJob).where(
                AsyncJob.queue_name == queue_name,
                AsyncJob.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing, False
        raise
    return job, True


def dispatch_due_jobs(session: Session, wake_queue: RedisWakeQueue, *, limit: int) -> int:
    dispatched = 0
    while dispatched < limit:
        outbox = session.scalar(
            select(JobOutbox)
            .join(AsyncJob, AsyncJob.id == JobOutbox.job_id)
            .where(
                JobOutbox.status == "pending",
                JobOutbox.available_at <= utcnow(),
                AsyncJob.queue_name == wake_queue.config.queue_name,
            )
            .order_by(JobOutbox.available_at, JobOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not outbox:
            break
        # Lock the durable job before publishing its ID. Without this lock a
        # fast worker could complete the job before the dispatcher commits and
        # the dispatcher's stale `queued` update could overwrite `succeeded`.
        job = session.scalar(select(AsyncJob).where(AsyncJob.id == outbox.job_id).with_for_update())
        if not job or job.status in {"succeeded", "dead_letter", "cancelled"}:
            outbox.status = "dispatched"
            outbox.dispatched_at = utcnow()
            outbox.last_error = None
            session.commit()
            continue
        try:
            wake_queue.publish(job.id)
        except Exception as exc:
            outbox.dispatch_attempts += 1
            outbox.last_error = _safe_error(exc, "queue publish failed")
            delay = min(
                wake_queue.config.retry_base_seconds * (2 ** max(outbox.dispatch_attempts - 1, 0)),
                wake_queue.config.retry_max_seconds,
            )
            outbox.available_at = utcnow() + timedelta(seconds=delay)
            session.commit()
            break
        outbox.status = "dispatched"
        outbox.dispatch_attempts += 1
        outbox.dispatched_at = utcnow()
        outbox.last_error = None
        if job.status in {"pending", "retry"}:
            job.status = "queued"
        session.commit()
        dispatched += 1
    return dispatched


def claim_job(session: Session, job_id: str, worker_id: str) -> AsyncJob | None:
    now = utcnow()
    candidate = session.scalar(
        select(AsyncJob.id)
        .where(
            AsyncJob.id == job_id,
            AsyncJob.status.in_(("pending", "queued", "retry")),
            AsyncJob.available_at <= now,
        )
        .with_for_update(skip_locked=True)
    )
    if candidate is None:
        session.rollback()
        return None
    # A single conditional update fences duplicate wake-ups even on SQLite,
    # where SELECT FOR UPDATE is ignored. Every admission gets a new epoch,
    # including a restarted worker reusing the same worker identifier.
    job = session.scalar(
        update(AsyncJob)
        .where(
            AsyncJob.id == job_id,
            AsyncJob.status.in_(("pending", "queued", "retry")),
            AsyncJob.available_at <= now,
        )
        .values(
            status="running",
            attempts=AsyncJob.attempts + 1,
            claim_epoch=AsyncJob.claim_epoch + 1,
            locked_at=now,
            locked_by=worker_id,
            last_error=None,
        )
        .returning(AsyncJob)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    if not job:
        session.rollback()
        return None
    session.commit()
    return job


def renew_job_lease(
    session: Session,
    job_id: str,
    worker_id: str,
    *,
    claim_epoch: int | None = None,
    stale_lock_seconds: int | None = None,
) -> bool:
    """Renew a running job only while this exact worker still owns its lease.

    The conditional update is the worker's fencing check. If a scheduler has
    already recovered the job, or another worker has claimed it, a late renewal
    cannot extend the replacement worker's lease.
    """
    now = utcnow()
    statement = update(AsyncJob).where(
        AsyncJob.id == job_id,
        AsyncJob.status == "running",
        AsyncJob.locked_by == worker_id,
    )
    if claim_epoch is not None:
        statement = statement.where(AsyncJob.claim_epoch == claim_epoch)
    if stale_lock_seconds is not None:
        statement = statement.where(
            AsyncJob.locked_at > now - timedelta(seconds=stale_lock_seconds)
        )
    renewed_job_id = session.scalar(
        statement.values(locked_at=now)
        .returning(AsyncJob.id)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return renewed_job_id == job_id


def complete_job(
    session: Session,
    job_id: str,
    result: JobResult,
    *,
    worker_id: str,
    claim_epoch: int | None = None,
) -> None:
    job = _locked_job(session, job_id)
    if not _owns_job(job, worker_id=worker_id, claim_epoch=claim_epoch):
        session.rollback()
        raise JobLeaseLostError("Job lease is no longer owned by this worker")
    assert job is not None
    job.status = "succeeded"
    job.result = result
    job.finished_at = utcnow()
    job.locked_at = None
    job.locked_by = None
    session.commit()


def fail_job(
    session: Session,
    job_id: str,
    error: BaseException,
    config: AsyncRuntimeConfig,
    *,
    worker_id: str,
    claim_epoch: int | None = None,
    permanent: bool = False,
) -> str:
    job = _locked_job(session, job_id)
    if not job:
        session.rollback()
        return "missing"
    if not _owns_job(job, worker_id=worker_id, claim_epoch=claim_epoch):
        session.rollback()
        return "lease_lost"
    job.last_error = _safe_error(error, "job execution failed")
    job.locked_at = None
    job.locked_by = None
    if permanent or job.attempts >= job.max_attempts:
        job.status = "dead_letter"
        job.finished_at = utcnow()
        _record_terminal_failure(
            session,
            job,
            reason="permanent_failure" if permanent else "attempts_exhausted",
            service="async_worker",
        )
        session.commit()
        return job.status

    delay = min(
        config.retry_base_seconds * (2 ** max(job.attempts - 1, 0)),
        config.retry_max_seconds,
    )
    due = utcnow() + timedelta(seconds=delay)
    job.status = "retry"
    job.available_at = due
    outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
    if not outbox:
        outbox = JobOutbox(tenant_id=job.tenant_id, job_id=job.id)
        session.add(outbox)
    outbox.status = "pending"
    outbox.available_at = due
    outbox.dispatched_at = None
    outbox.last_error = None
    session.commit()
    return job.status


def defer_job(
    session: Session,
    job_id: str,
    error: JobDeferredError,
    *,
    worker_id: str,
    claim_epoch: int | None = None,
) -> str:
    """Return an owned claim to the durable outbox without spending an attempt."""
    job = _locked_job(session, job_id)
    if not job:
        session.rollback()
        return "missing"
    if not _owns_job(job, worker_id=worker_id, claim_epoch=claim_epoch):
        session.rollback()
        return "lease_lost"
    due = utcnow() + timedelta(seconds=error.delay_seconds)
    job.status = "retry"
    job.attempts = max(0, job.attempts - 1)
    job.available_at = due
    job.locked_at = None
    job.locked_by = None
    job.finished_at = None
    job.last_error = "JobDeferredError: execution deferred"
    outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
    if not outbox:
        outbox = JobOutbox(tenant_id=job.tenant_id, job_id=job.id)
        session.add(outbox)
    outbox.status = "pending"
    outbox.available_at = due
    outbox.dispatched_at = None
    outbox.last_error = None
    session.commit()
    return "deferred"


def _locked_job(session: Session, job_id: str) -> AsyncJob | None:
    return session.scalar(
        select(AsyncJob)
        .where(AsyncJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _owns_job(job: AsyncJob | None, *, worker_id: str, claim_epoch: int | None) -> bool:
    return bool(
        job
        and job.status == "running"
        and job.locked_by == worker_id
        and (claim_epoch is None or job.claim_epoch == claim_epoch)
    )


def recover_stale_jobs(session: Session, config: AsyncRuntimeConfig) -> int:
    cutoff = utcnow() - timedelta(seconds=config.stale_lock_seconds)
    jobs = list(
        session.scalars(
            select(AsyncJob)
            .where(AsyncJob.status == "running", AsyncJob.locked_at < cutoff)
            .order_by(AsyncJob.locked_at)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
            .limit(config.dispatch_batch_size)
        ).all()
    )
    recovered = 0
    for job in jobs:
        if job.attempts >= job.max_attempts:
            job.status = "dead_letter"
            job.finished_at = utcnow()
            job.last_error = "Stale worker lease exhausted retry budget"
            _record_terminal_failure(
                session,
                job,
                reason="stale_lease_exhausted",
                service="async_scheduler",
            )
        else:
            job.status = "retry"
            job.available_at = utcnow()
            job.last_error = "Stale worker lease recovered"
            outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
            if not outbox:
                outbox = JobOutbox(tenant_id=job.tenant_id, job_id=job.id)
                session.add(outbox)
            outbox.status = "pending"
            outbox.available_at = job.available_at
            outbox.dispatched_at = None
            outbox.last_error = None
        job.locked_at = None
        job.locked_by = None
        recovered += 1
    session.commit()
    return recovered


def recover_stale_wakeups(session: Session, config: AsyncRuntimeConfig) -> int:
    """Republish queued jobs after a Redis notification may have been lost.

    Redis AOF is intentionally not the source of truth. A bounded periodic duplicate
    is preferable to leaving a durable queued job stranded after Redis data loss.
    Worker claim semantics make the duplicate safe.
    """
    cutoff = utcnow() - timedelta(seconds=config.redispatch_seconds)
    outboxes = list(
        session.scalars(
            select(JobOutbox)
            .join(AsyncJob, AsyncJob.id == JobOutbox.job_id)
            .where(
                AsyncJob.queue_name == config.queue_name,
                AsyncJob.status == "queued",
                JobOutbox.status == "dispatched",
                JobOutbox.dispatched_at < cutoff,
            )
            .order_by(JobOutbox.dispatched_at)
            .with_for_update(skip_locked=True)
            .limit(config.dispatch_batch_size)
        ).all()
    )
    for outbox in outboxes:
        outbox.status = "pending"
        outbox.available_at = utcnow()
        outbox.dispatched_at = None
        outbox.last_error = "Wake-up lease expired; redispatching durable job"
    session.commit()
    return len(outboxes)


def replay_dead_letter(
    session: Session,
    job_id: str,
    *,
    max_attempts: int | None = None,
    commit: bool = True,
) -> AsyncJob:
    job = session.scalar(select(AsyncJob).where(AsyncJob.id == job_id).with_for_update())
    if not job or job.status != "dead_letter":
        raise ValueError("Only dead-letter jobs can be replayed")
    if max_attempts is not None and not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    job.status = "retry"
    job.attempts = 0
    job.max_attempts = max_attempts or job.max_attempts
    job.available_at = utcnow()
    job.finished_at = None
    job.last_error = None
    outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
    if not outbox:
        outbox = JobOutbox(tenant_id=job.tenant_id, job_id=job.id)
        session.add(outbox)
    outbox.status = "pending"
    outbox.available_at = job.available_at
    outbox.dispatched_at = None
    outbox.last_error = None
    if commit:
        session.commit()
    else:
        session.flush()
    return job


def _record_terminal_failure(
    session: Session,
    job: AsyncJob,
    *,
    reason: str,
    service: str,
) -> None:
    """Persist an actionable terminal signal without copying job-controlled data.

    Payloads, results, idempotency keys, worker identifiers, and exception text
    are deliberately excluded. The remaining metadata is bounded runtime state
    needed to locate and safely triage the durable job.
    """

    session.add(
        SystemError(
            tenant_id=job.tenant_id,
            service=service,
            severity="high",
            message="Asynchronous job reached terminal failure",
            stack=None,
            error_metadata={
                "job_id": job.id,
                "job_kind": job.kind,
                "terminal_reason": reason,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
            },
        )
    )


def _safe_error(error: BaseException, fallback: str) -> str:
    name = type(error).__name__
    return f"{name}: {fallback}"[:240]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
