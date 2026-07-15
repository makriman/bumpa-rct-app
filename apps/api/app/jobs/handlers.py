"""Worker-side job handler registration.

Provider integrations register service-layer callables here. Handlers must call a
provider/service module, never an HTTP route function, so the worker remains usable
without constructing a FastAPI request context.
"""

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import FieldCipher
from app.db.models import AsyncJob, BumpaConnection, ResearchReport
from app.jobs.runtime import JobResult, PermanentJobError, enqueue_job, register_handler
from app.providers.bumpa import BumpaProviderError
from app.providers.local import LocalArtifactStore
from app.services.bumpa import run_sync
from app.services.operational_alerts import (
    check_hermes_profile_health,
    deliver_operational_alert,
)
from app.services.operational_retention import cleanup_operational_history
from app.services.proactive_insights import deliver_proactive_insight
from app.services.reports import (
    cleanup_expired_report_artifacts,
    generate_report,
    report_is_expired,
)
from app.services.whatsapp import process_inbox_event


@register_handler("system.noop")
def noop_handler(_session: Session, job: AsyncJob) -> JobResult:
    return {"accepted": True, "payload_keys": sorted(job.payload)}


@register_handler("whatsapp.process_webhook")
def whatsapp_webhook_handler(session: Session, job: AsyncJob) -> JobResult:
    event_id = _required_string(job.payload, "event_id")
    return process_inbox_event(session, event_id, get_settings())


@register_handler("whatsapp.proactive_insight")
def proactive_insight_handler(session: Session, job: AsyncJob) -> JobResult:
    tenant_id = _required_string(job.payload, "tenant_id")
    if job.tenant_id != tenant_id:
        raise PermanentJobError("Proactive insight tenant boundary is invalid")
    cadence = _required_string(job.payload, "cadence")
    if cadence not in {"daily", "weekly"}:
        raise PermanentJobError("Proactive insight cadence is invalid")
    slot = _required_string(job.payload, "slot")
    return deliver_proactive_insight(
        session,
        tenant_id=tenant_id,
        cadence=cadence,  # type: ignore[arg-type]
        slot=slot,
        settings=get_settings(),
    )


@register_handler("ops.deliver_alert")
def operational_alert_handler(_session: Session, job: AsyncJob) -> JobResult:
    return deliver_operational_alert(job.payload, get_settings())


@register_handler("ops.hermes_health")
def hermes_health_handler(session: Session, job: AsyncJob) -> JobResult:
    profile_id = _required_string(job.payload, "profile_id")
    return check_hermes_profile_health(
        session,
        profile_id=profile_id,
        settings=get_settings(),
    )


@register_handler("bumpa.sync")
def bumpa_sync_handler(session: Session, job: AsyncJob) -> JobResult:
    tenant_id = _required_string(job.payload, "tenant_id")
    connection_id = _required_string(job.payload, "connection_id")
    date_from = _required_date(job.payload, "date_from")
    date_to = _required_date(job.payload, "date_to")
    if date_to < date_from or (date_to - date_from).days > 366:
        raise PermanentJobError("Bumpa sync job contains an invalid date range")
    boundary_revision = _required_positive_int(job.payload, "boundary_revision")
    if job.tenant_id != tenant_id:
        raise PermanentJobError("Bumpa sync job tenant boundary is invalid")

    connection = session.get(BumpaConnection, connection_id)
    if not connection or connection.tenant_id != tenant_id:
        raise PermanentJobError("Bumpa connection is unavailable")
    if connection.status != "active":
        raise PermanentJobError("Bumpa connection is inactive")
    if connection.boundary_revision != boundary_revision:
        raise PermanentJobError("Bumpa connection was replaced after this sync was queued")

    settings = get_settings()
    try:
        result = run_sync(
            session,
            tenant_id=tenant_id,
            connection=connection,
            date_from=date_from,
            date_to=date_to,
            field_cipher=FieldCipher.from_settings(settings),
            runtime_backend=settings.bumpa_backend,
        )
    except HTTPException as exc:
        provider_error = exc.__cause__
        if isinstance(provider_error, BumpaProviderError) and not provider_error.retryable:
            raise PermanentJobError("Bumpa sync request was rejected") from exc
        if exc.status_code < 500:
            raise PermanentJobError("Bumpa sync request was rejected") from exc
        raise RuntimeError("Bumpa sync provider is temporarily unavailable") from exc
    return {
        "sync_run_id": result.id,
        "status": result.status,
        "completion_quality": result.completion_quality,
        "partial_reason": result.partial_reason,
        "orders_availability": result.orders_availability,
        "orders_count": result.orders_count,
        "requested_from": result.requested_from.isoformat(),
        "requested_to": result.requested_to.isoformat(),
    }


@register_handler("research.generate_report")
def research_report_handler(session: Session, job: AsyncJob) -> JobResult:
    report_id = _required_string(job.payload, "report_id")
    formats = _required_formats(job.payload)
    report = session.get(ResearchReport, report_id)
    if report is None:
        raise PermanentJobError("Research report is unavailable")
    if report_is_expired(report):
        raise PermanentJobError("Research report expired before generation")
    settings = get_settings()
    result = generate_report(
        session,
        LocalArtifactStore(settings.artifact_root),
        report,
        formats,
        pseudonym_secret=settings.research_pseudonym_key,
    )
    return {"report_id": result.id, "status": result.status, "formats": formats}


@register_handler("research.cleanup_expired_artifacts")
def research_retention_handler(session: Session, job: AsyncJob) -> JobResult:
    raw_limit = job.payload.get("limit", 1000)
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or not 1 <= raw_limit <= 1000:
        raise PermanentJobError("Research retention job limit is invalid")
    settings = get_settings()
    return cleanup_expired_report_artifacts(
        session,
        settings.artifact_root,
        limit=raw_limit,
    )


@register_handler("system.cleanup_operational_history")
def operational_retention_handler(session: Session, job: AsyncJob) -> JobResult:
    raw_limit = job.payload.get("limit")
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or not 1 <= raw_limit <= 1000:
        raise PermanentJobError("Operational retention job limit is invalid")
    settings = get_settings()
    result = cleanup_operational_history(
        session,
        audit_log_retention_days=settings.audit_log_retention_days,
        system_error_retention_days=settings.system_error_retention_days,
        limit=raw_limit,
    )
    continuation_enqueued = False
    if raw_limit in result.values():
        # One bounded job never monopolizes the worker. If a table filled the
        # batch, enqueue a durable continuation at the tail of the same queue;
        # other work can interleave while an arbitrary backlog still drains to
        # the configured retention boundary.
        _, continuation_enqueued = enqueue_job(
            session,
            kind="system.cleanup_operational_history",
            payload={"limit": raw_limit},
            idempotency_key=f"operational-retention:continuation:{job.id}",
            max_attempts=3,
        )
    return {
        "audit_logs_deleted": result["audit_logs_deleted"],
        "system_errors_deleted": result["system_errors_deleted"],
        "continuation_enqueued": continuation_enqueued,
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 200:
        raise PermanentJobError(f"Job payload field {key} is invalid")
    return value


def _required_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PermanentJobError(f"Job payload field {key} must be a positive integer")
    return value


def _required_date(payload: dict[str, Any], key: str) -> date:
    value = _required_string(payload, key)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PermanentJobError(f"Job payload field {key} is invalid") from exc


def _required_formats(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("formats")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        raise PermanentJobError("Research report formats are invalid")
    if any(not isinstance(value, str) for value in raw):
        raise PermanentJobError("Research report formats are invalid")
    formats = sorted(set(raw))
    if len(formats) != len(raw) or any(value not in {"csv", "jsonl", "pdf"} for value in formats):
        raise PermanentJobError("Research report formats are invalid")
    return formats
