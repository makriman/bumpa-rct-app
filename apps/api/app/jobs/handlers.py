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
from app.db.models import AsyncJob, BumpaConnection, ResearchReport
from app.jobs.runtime import JobResult, PermanentJobError, register_handler
from app.providers.bumpa import BumpaProviderError
from app.providers.local import LocalArtifactStore
from app.services.bumpa import run_sync
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


@register_handler("bumpa.sync")
def bumpa_sync_handler(session: Session, job: AsyncJob) -> JobResult:
    tenant_id = _required_string(job.payload, "tenant_id")
    connection_id = _required_string(job.payload, "connection_id")
    date_from = _required_date(job.payload, "date_from")
    date_to = _required_date(job.payload, "date_to")
    if date_to < date_from or (date_to - date_from).days > 366:
        raise PermanentJobError("Bumpa sync job contains an invalid date range")

    connection = session.get(BumpaConnection, connection_id)
    if not connection or connection.tenant_id != tenant_id:
        raise PermanentJobError("Bumpa connection is unavailable")
    if connection.status != "active":
        raise PermanentJobError("Bumpa connection is inactive")

    settings = get_settings()
    try:
        result = run_sync(
            session,
            tenant_id=tenant_id,
            connection=connection,
            date_from=date_from,
            date_to=date_to,
            field_encryption_key=settings.field_encryption_key,
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
        pseudonym_secret=settings.field_encryption_key,
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


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 200:
        raise PermanentJobError(f"Job payload field {key} is invalid")
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
