from __future__ import annotations

import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.time import utcnow
from app.db.models import HermesProfile, Tenant
from app.db.session import SessionLocal, set_security_context
from app.jobs.runtime import (
    AsyncRuntimeConfig,
    RedisWakeQueue,
    dispatch_due_jobs,
    enqueue_job,
    recover_stale_jobs,
    recover_stale_wakeups,
)
from app.services.operational_alerts import discover_operational_alerts

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
    settings = get_settings()
    _ensure_proactive_insight_jobs(session, settings=settings)
    _ensure_operational_alert_jobs(session, settings=settings)
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


def _ensure_proactive_insight_jobs(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Create one cadence job per eligible tenant-local calendar slot."""

    if not settings.proactive_insights_enabled:
        return 0
    current = _as_utc(now or utcnow())
    tenants = session.scalars(
        select(Tenant)
        .where(
            Tenant.status == "active",
            Tenant.research_consent_status == "granted",
        )
        .order_by(Tenant.id.asc())
    ).all()
    created = 0
    for tenant in tenants:
        try:
            local_now = current.astimezone(ZoneInfo(tenant.timezone))
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("insight_schedule_invalid_timezone")
            continue
        if settings.daily_insights_enabled and local_now.hour >= settings.daily_insight_local_hour:
            slot = local_now.date().isoformat()
            _, was_created = enqueue_job(
                session,
                kind="whatsapp.proactive_insight",
                tenant_id=tenant.id,
                idempotency_key=f"proactive-insight:daily:{tenant.id}:{slot}",
                payload={"tenant_id": tenant.id, "cadence": "daily", "slot": slot},
                max_attempts=3,
            )
            created += int(was_created)
        weekly_is_due = local_now.weekday() > settings.weekly_insight_local_weekday or (
            local_now.weekday() == settings.weekly_insight_local_weekday
            and local_now.hour >= settings.weekly_insight_local_hour
        )
        if settings.weekly_insights_enabled and weekly_is_due:
            iso_year, iso_week, _ = local_now.isocalendar()
            slot = f"{iso_year:04d}-W{iso_week:02d}"
            _, was_created = enqueue_job(
                session,
                kind="whatsapp.proactive_insight",
                tenant_id=tenant.id,
                idempotency_key=f"proactive-insight:weekly:{tenant.id}:{slot}",
                payload={"tenant_id": tenant.id, "cadence": "weekly", "slot": slot},
                max_attempts=3,
            )
            created += int(was_created)
    return created


def _ensure_operational_alert_jobs(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    if not settings.ops_alerts_enabled:
        return 0
    current = _as_utc(now or utcnow())
    created = discover_operational_alerts(session, settings, now=current)
    interval_seconds = settings.hermes_health_alert_interval_minutes * 60
    slot = int(current.timestamp()) // interval_seconds
    profiles = session.scalars(
        select(HermesProfile)
        .where(
            HermesProfile.provider == "hermes",
            HermesProfile.status.in_(("active", "degraded", "provisioning")),
        )
        .order_by(HermesProfile.id.asc())
        .limit(settings.ops_alert_scan_limit)
    ).all()
    for profile in profiles:
        _, was_created = enqueue_job(
            session,
            kind="ops.hermes_health",
            tenant_id=profile.tenant_id,
            idempotency_key=f"hermes-health:{profile.id}:{slot}",
            payload={"profile_id": profile.id},
            max_attempts=1,
        )
        created += int(was_created)
    return created


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
