from __future__ import annotations

import logging
import os
import signal
import socket
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import utcnow
from app.db.session import SessionLocal, set_security_context
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    RedisWakeQueue,
    dispatch_due_jobs,
    enqueue_job,
    recover_stale_jobs,
    recover_stale_wakeups,
)

logger = logging.getLogger("bumpabestie.scheduler")
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def run_cycle(
    session: Session,
    *,
    config: AsyncRuntimeConfig,
    wake_queue: RedisWakeQueue,
) -> tuple[int, int, int]:
    set_security_context(session, privileged=True)
    _ensure_daily_maintenance(session)
    recovered = recover_stale_jobs(session, config)
    wakeups = recover_stale_wakeups(session, config)
    dispatched = dispatch_due_jobs(session, wake_queue, limit=config.dispatch_batch_size)
    return recovered, wakeups, dispatched


def _ensure_daily_maintenance(session: Session) -> None:
    if get_settings().app_env != "production":
        return
    day = utcnow().date().isoformat()
    enqueue_job(
        session,
        kind="research.cleanup_expired_artifacts",
        payload={"limit": 1000},
        idempotency_key=f"research-retention:{day}",
        max_attempts=3,
    )


def main() -> None:
    global running
    configure_logging()
    config = AsyncRuntimeConfig.from_env()
    if not config.enabled:
        raise RuntimeError("Async runtime is disabled; refusing to start scheduler")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    scheduler_id = f"{socket.gethostname()}:{os.getpid()}"
    wake_queue = RedisWakeQueue(config)
    logger.info(
        "scheduler_starting", extra={"scheduler_id": scheduler_id, "queue": config.queue_name}
    )
    while running:
        try:
            wake_queue.heartbeat("scheduler", scheduler_id)
            with SessionLocal() as session:
                recovered, wakeups, dispatched = run_cycle(
                    session, config=config, wake_queue=wake_queue
                )
            if recovered or wakeups or dispatched:
                logger.info(
                    "scheduler_cycle",
                    extra={
                        "recovered": recovered,
                        "redispatched_wakeups": wakeups,
                        "dispatched": dispatched,
                    },
                )
        except Exception:
            logger.exception("scheduler_cycle_failed")
        if running:
            time.sleep(config.scheduler_interval_seconds)
    logger.info("scheduler_stopped", extra={"scheduler_id": scheduler_id})


if __name__ == "__main__":
    main()
