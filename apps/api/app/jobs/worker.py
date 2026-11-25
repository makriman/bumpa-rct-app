from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from contextlib import nullcontext
from types import TracebackType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.models import AsyncJob
from app.db.session import SessionLocal, set_security_context
from app.jobs import handlers as _handlers  # noqa: F401 - import registers worker handlers
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    JobDeferredError,
    JobFence,
    JobLeaseLostError,
    PermanentJobError,
    RedisWakeQueue,
    assert_current_job_lease,
    bind_job_fence,
    claim_job,
    complete_job,
    defer_job,
    fail_job,
    registry,
    renew_job_lease,
)

logger = logging.getLogger("bumpabestie.worker")
running = True


class JobLeaseKeeper:
    """Keep worker liveness and a claimed database lease fresh during a handler."""

    def __init__(
        self,
        *,
        job_id: str,
        worker_id: str,
        config: AsyncRuntimeConfig,
        wake_queue: RedisWakeQueue,
        fence: JobFence | None = None,
    ) -> None:
        self.job_id = job_id
        self.worker_id = worker_id
        self.config = config
        self.wake_queue = wake_queue
        self.fence = fence
        self.interval_seconds = max(
            0.01,
            min(config.heartbeat_ttl_seconds, config.stale_lock_seconds) / 3,
        )
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"job-lease-{job_id[:12]}",
            daemon=True,
        )

    def __enter__(self) -> JobLeaseKeeper:
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._stop_event.set()
        self._thread.join(timeout=max(1.0, min(self.interval_seconds * 2, 5.0)))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                service = (
                    "worker_whatsapp"
                    if getattr(self.wake_queue, "interactive", False)
                    else "worker"
                )
                self.wake_queue.heartbeat(service, self.worker_id)
            except Exception:
                logger.exception(
                    "worker_heartbeat_failed",
                    extra={"worker_id": self.worker_id, "job_id": self.job_id},
                )

            try:
                with SessionLocal() as lease_session:
                    set_security_context(lease_session, privileged=True)
                    renewed = renew_job_lease(
                        lease_session,
                        self.job_id,
                        self.worker_id,
                        claim_epoch=self.fence.claim_epoch if self.fence else None,
                        stale_lock_seconds=self.config.stale_lock_seconds,
                    )
                if not renewed:
                    if self.fence:
                        self.fence.invalidate()
                    logger.warning(
                        "job_lease_lost",
                        extra={"worker_id": self.worker_id, "job_id": self.job_id},
                    )
                    return
            except Exception:
                if self.fence:
                    self.fence.invalidate()
                logger.exception(
                    "job_lease_renewal_failed",
                    extra={"worker_id": self.worker_id, "job_id": self.job_id},
                )
                return

            if self._stop_event.wait(self.interval_seconds):
                return


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def process_one(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    config: AsyncRuntimeConfig,
    wake_queue: RedisWakeQueue | None = None,
) -> str:
    set_security_context(session, privileged=True)
    job = claim_job(session, job_id, worker_id)
    if not job:
        return "ignored"
    fence = JobFence(job.id, worker_id, job.claim_epoch, config.stale_lock_seconds)
    lease_keeper = (
        JobLeaseKeeper(
            job_id=job.id,
            worker_id=worker_id,
            config=config,
            wake_queue=wake_queue,
            fence=fence,
        )
        if wake_queue is not None
        else None
    )
    try:
        with bind_job_fence(fence):
            assert_current_job_lease(session)
            handler = registry.handler_for(job.kind)
            # Complete while the keeper is still active. Joining a renewal
            # blocked on this transaction's job-row lock before committing the
            # result would stall every handler until the join timeout.
            with lease_keeper if lease_keeper is not None else nullcontext():
                result = handler(session, job)
                assert_current_job_lease(session)
                complete_job(
                    session,
                    fence.job_id,
                    result,
                    worker_id=worker_id,
                    claim_epoch=fence.claim_epoch,
                )
        logger.info(
            "job_succeeded",
            extra={"job_id": job.id, "job_kind": job.kind, "attempt": job.attempts},
        )
        return "succeeded"
    except JobLeaseLostError:
        session.rollback()
        status = "lease_lost"
    except JobDeferredError as exc:
        session.rollback()
        status = defer_job(
            session, fence.job_id, exc, worker_id=worker_id, claim_epoch=fence.claim_epoch
        )
    except PermanentJobError as exc:
        session.rollback()
        status = fail_job(
            session,
            fence.job_id,
            exc,
            config,
            worker_id=worker_id,
            claim_epoch=fence.claim_epoch,
            permanent=True,
        )
    except Exception as exc:
        session.rollback()
        status = fail_job(
            session,
            fence.job_id,
            exc,
            config,
            worker_id=worker_id,
            claim_epoch=fence.claim_epoch,
        )
    logger.warning(
        "job_failed",
        extra={"job_id": job.id, "job_kind": job.kind, "status": status},
    )
    return status


def restore_interactive_wakeups(config: AsyncRuntimeConfig, wake_queue: RedisWakeQueue) -> int:
    """Route pre-upgrade queued jobs without changing their durable identity.

    Older Redis lists can retain duplicate IDs: claim epochs make those harmless.
    Remaining/lost wakeups use the existing durable scheduler recovery policy.
    """
    with SessionLocal() as session:
        set_security_context(session, privileged=True)
        rows = session.execute(
            select(AsyncJob.id, AsyncJob.kind)
            .where(
                AsyncJob.queue_name == config.queue_name,
                AsyncJob.status == "queued",
                AsyncJob.kind.startswith("whatsapp."),
            )
            .order_by(AsyncJob.created_at)
            .limit(1000)
        ).all()
    for job_id, kind in rows:
        wake_queue.publish(job_id, job_kind=kind)
    return len(rows)


def work_loop(config: AsyncRuntimeConfig, worker_id: str, wake_queue: RedisWakeQueue) -> None:
    """Each lane has its own consumer, DB session, claim and renewal thread."""
    service = "worker_whatsapp" if wake_queue.interactive else "worker"
    while running:
        try:
            wake_queue.heartbeat(service, worker_id)
            job_id = wake_queue.pop()
            wake_queue.heartbeat(service, worker_id)
            if job_id:
                with SessionLocal() as session:
                    set_security_context(session, privileged=True)
                    kind = session.scalar(select(AsyncJob.kind).where(AsyncJob.id == job_id))
                    session.rollback()
                    if kind is None:
                        continue
                    if kind.startswith("whatsapp.") != wake_queue.interactive:
                        wake_queue.publish(job_id, job_kind=kind)
                        continue
                    process_one(
                        session,
                        job_id=job_id,
                        worker_id=worker_id,
                        config=config,
                        wake_queue=wake_queue,
                    )
        except Exception:
            logger.exception("worker_cycle_failed")
            time.sleep(min(config.scheduler_interval_seconds, 5.0))


def main() -> None:
    global running
    configure_logging()
    config = AsyncRuntimeConfig.from_env()
    if not config.enabled:
        raise RuntimeError("Async runtime is disabled; refusing to start worker")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    wake_queue = RedisWakeQueue(config)
    # One bulk consumer preserves Bumpa's existing provider concurrency budget.
    # Dedicated interactive consumers cannot be occupied by a long import.
    restore_interactive_wakeups(config, wake_queue)
    threads = [
        threading.Thread(
            target=work_loop,
            args=(
                config,
                f"{worker_id}:whatsapp:{index}",
                RedisWakeQueue(config, interactive=True),
            ),
            name=f"whatsapp-worker-{index}",
        )
        for index in range(config.whatsapp_workers)
    ]
    for thread in threads:
        thread.start()
    logger.info("worker_starting", extra={"worker_id": worker_id, "queue": config.queue_name})
    try:
        work_loop(config, worker_id, wake_queue)
    finally:
        running = False
        for thread in threads:
            thread.join()
    logger.info("worker_stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
