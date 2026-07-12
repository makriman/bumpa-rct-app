from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from redis import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import AsyncJob, JobOutbox

JobResult = dict[str, Any] | None
JobHandler = Callable[[Session, AsyncJob], JobResult]


class PermanentJobError(RuntimeError):
    """A safe, non-retriable failure that should move a job to the dead letter state."""


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
    job = session.scalar(
        select(AsyncJob).where(AsyncJob.id == job_id).with_for_update(skip_locked=True)
    )
    if not job or job.status not in {"pending", "queued", "retry"}:
        session.rollback()
        return None
    if _as_utc(job.available_at) > utcnow():
        session.rollback()
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_at = utcnow()
    job.locked_by = worker_id
    job.last_error = None
    session.commit()
    return job


def renew_job_lease(session: Session, job_id: str, worker_id: str) -> bool:
    """Renew a running job only while this exact worker still owns its lease.

    The conditional update is the worker's fencing check. If a scheduler has
    already recovered the job, or another worker has claimed it, a late renewal
    cannot extend the replacement worker's lease.
    """
    renewed_job_id = session.scalar(
        update(AsyncJob)
        .where(
            AsyncJob.id == job_id,
            AsyncJob.status == "running",
            AsyncJob.locked_by == worker_id,
        )
        .values(locked_at=utcnow())
        .returning(AsyncJob.id)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return renewed_job_id == job_id


def complete_job(session: Session, job_id: str, result: JobResult, *, worker_id: str) -> None:
    job = session.scalar(select(AsyncJob).where(AsyncJob.id == job_id).with_for_update())
    if not job or job.status != "running" or job.locked_by != worker_id:
        session.rollback()
        raise RuntimeError("Job lease is no longer owned by this worker")
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
    permanent: bool = False,
) -> str:
    job = session.scalar(select(AsyncJob).where(AsyncJob.id == job_id).with_for_update())
    if not job:
        session.rollback()
        return "missing"
    if job.status != "running" or job.locked_by != worker_id:
        session.rollback()
        return "lease_lost"
    job.last_error = _safe_error(error, "job execution failed")
    job.locked_at = None
    job.locked_by = None
    if permanent or job.attempts >= job.max_attempts:
        job.status = "dead_letter"
        job.finished_at = utcnow()
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


def recover_stale_jobs(session: Session, config: AsyncRuntimeConfig) -> int:
    cutoff = utcnow() - timedelta(seconds=config.stale_lock_seconds)
    jobs = list(
        session.scalars(
            select(AsyncJob)
            .where(AsyncJob.status == "running", AsyncJob.locked_at < cutoff)
            .order_by(AsyncJob.locked_at)
            .with_for_update(skip_locked=True)
            .limit(config.dispatch_batch_size)
        ).all()
    )
    recovered = 0
    for job in jobs:
        if job.attempts >= job.max_attempts:
            job.status = "dead_letter"
            job.finished_at = utcnow()
            job.last_error = "Stale worker lease exhausted retry budget"
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
    session: Session, job_id: str, *, max_attempts: int | None = None
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
    session.commit()
    return job


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
