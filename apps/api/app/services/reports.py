from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import Artifact, ResearchConsent, ResearchEvent, ResearchReport, Tenant
from app.providers.local import LocalArtifactStore
from app.providers.redaction import csv_safe, pseudonymize, redact_text
from app.services.audit import audit

REPORT_RETENTION = timedelta(hours=24)
ALLOWED_FILTERS = frozenset({"tenant_id", "channel", "primary_intent"})


def validated_filters(filters: dict[str, Any]) -> dict[str, str]:
    """Return the narrow, research-safe filter contract used by reads and jobs."""

    unknown = set(filters) - ALLOWED_FILTERS
    if unknown:
        raise ValueError(f"Unsupported report filters: {', '.join(sorted(unknown))}")
    validated: dict[str, str] = {}
    for key, value in filters.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError(f"Report filter {key} must be a non-empty string")
        validated[key] = value.strip()
    return validated


def report_expires_at(report: ResearchReport) -> datetime:
    created_at = report.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at + REPORT_RETENTION


def report_is_expired(report: ResearchReport, *, now: datetime | None = None) -> bool:
    return report_expires_at(report) <= (now or utcnow())


def report_consent_revoked(db: Session, report: ResearchReport) -> bool:
    """Fail closed when consent changed after an artifact captured research data.

    A tenant-filtered report is tied to that participant. An unfiltered/cohort
    artifact can contain any participant, so any later withdrawal invalidates it.
    Re-granting consent does not resurrect an artifact created before withdrawal.
    """

    created_at = report.created_at
    statement = select(ResearchConsent.id).where(
        ResearchConsent.status == "withdrawn",
        ResearchConsent.recorded_at > created_at,
    )
    tenant_id = report.filters.get("tenant_id")
    if isinstance(tenant_id, str) and tenant_id:
        tenant = db.get(Tenant, tenant_id)
        if not tenant or tenant.research_consent_status != "granted":
            return True
        statement = statement.where(ResearchConsent.tenant_id == tenant_id)
    return db.scalar(statement.limit(1)) is not None


def purge_report_artifacts(
    db: Session,
    artifact_root: Path,
    report: ResearchReport,
) -> int:
    """Remove artifact bytes and metadata without deleting the audit/report record."""

    root = artifact_root.resolve()
    artifacts = list(db.scalars(select(Artifact).where(Artifact.report_id == report.id)).all())
    for artifact in artifacts:
        candidate = (root / artifact.storage_key).resolve()
        if root != candidate and root not in candidate.parents:
            raise ValueError("Invalid artifact storage key")
        candidate.unlink(missing_ok=True)
        db.delete(artifact)
    return len(artifacts)


def delete_report(
    db: Session,
    artifact_root: Path,
    report: ResearchReport,
    *,
    actor_user_id: str,
) -> int:
    deleted_artifacts = purge_report_artifacts(db, artifact_root, report)
    audit(
        db,
        actor_user_id=actor_user_id,
        action="research.report.deleted",
        resource_type="research_report",
        resource_id=report.id,
        after={"artifact_count": deleted_artifacts},
    )
    db.delete(report)
    db.commit()
    return deleted_artifacts


def cleanup_expired_report_artifacts(
    db: Session,
    artifact_root: Path,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Apply expiry and post-withdrawal invalidation in a bounded cleanup batch."""

    if not 1 <= limit <= 1_000:
        raise ValueError("Cleanup limit must be between 1 and 1000")
    candidates = list(
        db.scalars(
            select(ResearchReport)
            .join(Artifact, Artifact.report_id == ResearchReport.id)
            .distinct()
            .order_by(ResearchReport.created_at)
            .limit(limit)
        ).all()
    )
    reports_cleaned = 0
    artifacts_deleted = 0
    for report in candidates:
        expired = report_is_expired(report)
        consent_invalidated = report_consent_revoked(db, report)
        if not expired and not consent_invalidated:
            continue
        artifacts_deleted += purge_report_artifacts(db, artifact_root, report)
        reports_cleaned += 1
        audit(
            db,
            actor_user_id=None,
            action="research.report.retention_applied",
            resource_type="research_report",
            resource_id=report.id,
            after={"expired": expired, "consent_invalidated": consent_invalidated},
        )
    db.commit()
    return {"reports_cleaned": reports_cleaned, "artifacts_deleted": artifacts_deleted}


def _rows(db: Session, filters: dict[str, Any], *, pseudonym_secret: str) -> list[dict[str, Any]]:
    safe_filters = validated_filters(filters)
    statement = (
        select(ResearchEvent)
        .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
        .where(Tenant.research_consent_status == "granted")
        .order_by(ResearchEvent.created_at.desc())
        .limit(10_000)
    )
    if tenant_id := safe_filters.get("tenant_id"):
        statement = statement.where(ResearchEvent.tenant_id == tenant_id)
    if channel := safe_filters.get("channel"):
        statement = statement.where(ResearchEvent.channel == channel)
    if intent := safe_filters.get("primary_intent"):
        statement = statement.where(ResearchEvent.primary_intent == intent)
    events = db.scalars(statement).all()
    return [
        {
            "timestamp": event.created_at.isoformat(),
            "event_pseudonym": pseudonymize(event.id, pseudonym_secret, namespace="event"),
            "tenant_pseudonym": pseudonymize(event.tenant_id, pseudonym_secret, namespace="tenant"),
            "user_pseudonym": pseudonymize(event.user_id, pseudonym_secret, namespace="user"),
            "channel": event.channel,
            "question": redact_text(event.redacted_text) if event.redacted_text else "",
            "primary_intent": event.primary_intent or "unclassified",
            "business_function": event.business_function or "unclassified",
            "ai_help_type": event.ai_help_type or "unclassified",
            "complexity": event.complexity or "unclassified",
            "bumpa_data_used": event.bumpa_data_used or "none",
        }
        for event in events
    ]


def _csv(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows({key: csv_safe(value) for key, value in row.items()} for row in rows)
    return output.getvalue().encode()


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return (
        "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows) + "\n"
    ).encode()


def _pdf(rows: list[dict[str, Any]], title: str) -> bytes:
    # Minimal standards-compliant PDF; production renderer can replace this through the artifact port.
    summary = f"{title} - {len(rows)} anonymized research events"
    safe = summary.replace("(", "[").replace(")", "]")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def generate_report(
    db: Session,
    store: LocalArtifactStore,
    report: ResearchReport,
    formats: Sequence[str],
    *,
    pseudonym_secret: str,
) -> ResearchReport:
    if report.status == "success":
        return report
    report.status = "running"
    report.error = None
    db.commit()
    try:
        rows = _rows(db, report.filters, pseudonym_secret=pseudonym_secret)
        title = report.title or report.report_type.replace("_", " ").title()
        for fmt in sorted(set(formats)):
            if fmt == "csv":
                content, content_type = _csv(rows), "text/csv"
            elif fmt == "jsonl":
                content, content_type = _jsonl(rows), "application/x-ndjson"
            elif fmt == "pdf":
                content, content_type = _pdf(rows, title), "application/pdf"
            else:
                raise ValueError("Unsupported report format")
            key, byte_size, checksum = store.put(f"{report.id}/{report.id}.{fmt}", content)
            db.add(
                Artifact(
                    report_id=report.id,
                    format=fmt,
                    storage_key=key,
                    content_type=content_type,
                    byte_size=byte_size,
                    checksum_sha256=checksum,
                )
            )
        report.status = "success"
        report.summary = f"Generated an anonymized report from {len(rows)} research events."
        report.finished_at = utcnow()
        audit(
            db,
            actor_user_id=report.generated_by,
            action="research.report.completed",
            resource_type="research_report",
            resource_id=report.id,
            after={"formats": sorted(set(formats)), "event_count": len(rows)},
        )
        db.commit()
    except Exception:
        db.rollback()
        failed_report = db.get(ResearchReport, report.id)
        if failed_report is not None:
            failed_report.status = "failed"
            failed_report.error = "Report generation failed"
            failed_report.finished_at = utcnow()
            db.commit()
        raise
    db.refresh(report)
    return report
