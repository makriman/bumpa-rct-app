from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from types import TracebackType

from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.session import SessionLocal, set_security_context
from app.jobs import handlers as _handlers  # noqa: F401 - import registers worker handlers
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    PermanentJobError,
    RedisWakeQueue,
    claim_job,
    complete_job,
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
    ) -> None:
        self.job_id = job_id
        self.worker_id = worker_id
        self.config = config
        self.wake_queue = wake_queue
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
                self.wake_queue.heartbeat("worker", self.worker_id)
            except Exception:
                logger.exception(
                    "worker_heartbeat_failed",
                    extra={"worker_id": self.worker_id, "job_id": self.job_id},
                )

            try:
                with SessionLocal() as lease_session:
                    set_security_context(lease_session, privileged=True)
                    renewed = renew_job_lease(lease_session, self.job_id, self.worker_id)
                if not renewed:
                    logger.warning(
                        "job_lease_lost",
                        extra={"worker_id": self.worker_id, "job_id": self.job_id},
                    )
                    return
            except Exception:
                logger.exception(
                    "job_lease_renewal_failed",
                    extra={"worker_id": self.worker_id, "job_id": self.job_id},
                )

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
    lease_keeper = (
        JobLeaseKeeper(
            job_id=job.id,
            worker_id=worker_id,
            config=config,
            wake_queue=wake_queue,
        )
        if wake_queue is not None
        else None
    )
    try:
        handler = registry.handler_for(job.kind)
        if lease_keeper is None:
            result = handler(session, job)
        else:
            with lease_keeper:
                result = handler(session, job)
        complete_job(session, job.id, result, worker_id=worker_id)
        logger.info(
            "job_succeeded",
            extra={"job_id": job.id, "job_kind": job.kind, "attempt": job.attempts},
        )
        return "succeeded"
    except PermanentJobError as exc:
        session.rollback()
        status = fail_job(session, job.id, exc, config, worker_id=worker_id, permanent=True)
    except Exception as exc:
        session.rollback()
        status = fail_job(session, job.id, exc, config, worker_id=worker_id)
    logger.warning(
        "job_failed",
        extra={"job_id": job.id, "job_kind": job.kind, "status": status},
    )
    return status


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
    logger.info("worker_starting", extra={"worker_id": worker_id, "queue": config.queue_name})
    while running:
        try:
            wake_queue.heartbeat("worker", worker_id)
            job_id = wake_queue.pop()
            wake_queue.heartbeat("worker", worker_id)
            if job_id:
                with SessionLocal() as session:
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
    logger.info("worker_stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
