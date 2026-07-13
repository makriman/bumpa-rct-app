from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from math import ceil
from pathlib import Path
from typing import Any

from reportlab.graphics.charts.barcharts import HorizontalBarChart  # type: ignore[import-untyped]
from reportlab.graphics.shapes import Drawing  # type: ignore[import-untyped]
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_CENTER  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    Artifact,
    ResearchConsent,
    ResearchEvent,
    ResearchReport,
    Tenant,
)
from app.providers.local import LocalArtifactStore
from app.providers.redaction import csv_safe, pseudonymize, redact_text
from app.services.audit import audit
from app.services.research_events import record_report_generated_events

REPORT_RETENTION = timedelta(hours=24)
ALLOWED_FILTERS = frozenset(
    {
        "tenant_id",
        "tenant_pseudonym",
        "channel",
        "primary_intent",
        "business_function",
        "ai_help_type",
        "complexity",
        "date_from",
        "date_to",
    }
)
RAW_REPORT_TYPE = "raw_export_package"
REPORT_TYPES = frozenset(
    {
        "sme_usage",
        "cohort_behavior",
        "question_taxonomy",
        "business_outcome_correlation",
        "weekly_memo",
        "monthly_memo",
        RAW_REPORT_TYPE,
        "anonymized_export_package",
    }
)
REPORT_FORMATS = frozenset({"csv", "jsonl", "pdf"})
MAX_REPORT_ROWS = 10_000
SENSITIVE_OUTCOME_KEYS = frozenset(
    {
        "address",
        "conversation_id",
        "email",
        "event_id",
        "message_id",
        "name",
        "phone",
        "tenant_id",
        "user_id",
    }
)


@dataclass(frozen=True)
class ReportSection:
    title: str
    body: str
    counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ReportDocument:
    title: str
    report_type: str
    generated_at: datetime
    filters: dict[str, str]
    rows: list[dict[str, Any]]
    sections: tuple[ReportSection, ...]
    raw: bool


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
    if "tenant_id" in validated and "tenant_pseudonym" in validated:
        raise ValueError("Use either tenant_id or tenant_pseudonym, not both")
    parsed_dates: dict[str, date] = {}
    for key in ("date_from", "date_to"):
        if value := validated.get(key):
            try:
                parsed_dates[key] = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"Report filter {key} must use YYYY-MM-DD") from exc
    if parsed_dates.get("date_from") and parsed_dates.get("date_to"):
        date_from = parsed_dates["date_from"]
        date_to = parsed_dates["date_to"]
        if date_to < date_from:
            raise ValueError("Report date_to cannot precede date_from")
        if (date_to - date_from).days > 366:
            raise ValueError("Report date range cannot exceed 366 days")
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


def _cleanup_candidate_statement(*, limit: int) -> Select[tuple[ResearchReport]]:
    """Select each report with artifacts once without comparing JSON values.

    PostgreSQL cannot apply ``DISTINCT`` to a full ``ResearchReport`` row because
    the row includes the JSON ``filters`` column. A correlated ``EXISTS`` keeps
    the query bounded while avoiding both that comparison and duplicate reports
    when one report has multiple artifacts.
    """

    has_artifact = (
        select(Artifact.id)
        .where(Artifact.report_id == ResearchReport.id)
        .correlate(ResearchReport)
        .exists()
    )
    return (
        select(ResearchReport)
        .where(has_artifact)
        .order_by(ResearchReport.created_at, ResearchReport.id)
        .limit(limit)
    )


def cleanup_expired_report_artifacts(
    db: Session,
    artifact_root: Path,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Apply expiry and post-withdrawal invalidation in a bounded cleanup batch."""

    if not 1 <= limit <= 1_000:
        raise ValueError("Cleanup limit must be between 1 and 1000")
    candidates = list(db.scalars(_cleanup_candidate_statement(limit=limit)).all())
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


def _filtered_events(
    db: Session,
    filters: dict[str, Any],
    *,
    pseudonym_secret: str,
) -> list[ResearchEvent]:
    safe_filters = validated_filters(filters)
    statement = (
        select(ResearchEvent)
        .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
        .where(Tenant.research_consent_status == "granted")
        .order_by(ResearchEvent.created_at.desc())
        .limit(MAX_REPORT_ROWS)
    )
    if tenant_id := safe_filters.get("tenant_id"):
        statement = statement.where(ResearchEvent.tenant_id == tenant_id)
    if tenant_pseudonym := safe_filters.get("tenant_pseudonym"):
        candidate_ids = list(
            db.scalars(select(Tenant.id).where(Tenant.research_consent_status == "granted")).all()
        )
        resolved_tenant_id = next(
            (
                candidate
                for candidate in candidate_ids
                if secrets.compare_digest(
                    pseudonymize(candidate, pseudonym_secret, namespace="tenant"),
                    tenant_pseudonym,
                )
            ),
            "",
        )
        statement = statement.where(ResearchEvent.tenant_id == resolved_tenant_id)
    if channel := safe_filters.get("channel"):
        statement = statement.where(ResearchEvent.channel == channel)
    if intent := safe_filters.get("primary_intent"):
        statement = statement.where(ResearchEvent.primary_intent == intent)
    if business_function := safe_filters.get("business_function"):
        statement = statement.where(ResearchEvent.business_function == business_function)
    if ai_help_type := safe_filters.get("ai_help_type"):
        statement = statement.where(ResearchEvent.ai_help_type == ai_help_type)
    if complexity := safe_filters.get("complexity"):
        statement = statement.where(ResearchEvent.complexity == complexity)
    if date_from := safe_filters.get("date_from"):
        statement = statement.where(
            ResearchEvent.created_at
            >= datetime.combine(date.fromisoformat(date_from), time.min, tzinfo=UTC)
        )
    if date_to := safe_filters.get("date_to"):
        exclusive_to = date.fromisoformat(date_to) + timedelta(days=1)
        statement = statement.where(
            ResearchEvent.created_at < datetime.combine(exclusive_to, time.min, tzinfo=UTC)
        )
    return list(db.scalars(statement).all())


def _rows(
    db: Session,
    filters: dict[str, Any],
    *,
    pseudonym_secret: str,
    raw: bool,
    events: list[ResearchEvent] | None = None,
) -> list[dict[str, Any]]:
    if events is None:
        events = _filtered_events(db, filters, pseudonym_secret=pseudonym_secret)
    messages: dict[str, AgentMessage] = {}
    if raw:
        message_ids = [event.agent_message_id for event in events if event.agent_message_id]
        if message_ids:
            messages = {
                message.id: message
                for message in db.scalars(
                    select(AgentMessage).where(AgentMessage.id.in_(message_ids))
                ).all()
            }
        rows: list[dict[str, Any]] = []
        for event in events:
            message = messages.get(event.agent_message_id or "")
            if message is not None and message.tenant_id != event.tenant_id:
                message = None
            rows.append(
                {
                    "record_type": "research_event",
                    "timestamp": event.created_at.isoformat(),
                    "event_id": event.id,
                    "tenant_id": event.tenant_id,
                    "user_id": event.user_id,
                    "conversation_id": event.conversation_id,
                    "agent_message_id": event.agent_message_id,
                    "channel": event.channel,
                    "event_type": event.event_type,
                    "raw_text_present": event.raw_text_present,
                    "message_direction": message.direction if message else None,
                    "raw_message": message.content if message else None,
                    "raw_question": (
                        message.content
                        if event.event_type == "user_message_received" and message
                        else None
                    ),
                    "redacted_question": (
                        redact_text(event.redacted_text) if event.redacted_text else None
                    ),
                    "primary_intent": event.primary_intent or "unclassified",
                    "business_function": event.business_function or "unclassified",
                    "ai_help_type": event.ai_help_type or "unclassified",
                    "complexity": event.complexity or "unclassified",
                    "bumpa_data_used": event.bumpa_data_used or "none",
                    "outcome_json": json.dumps(event.outcome, ensure_ascii=False, sort_keys=True),
                    "pii_redacted": event.pii_redacted,
                }
            )
        return rows
    return [
        {
            "record_type": "research_event",
            "timestamp": event.created_at.isoformat(),
            "event_pseudonym": pseudonymize(event.id, pseudonym_secret, namespace="event"),
            "tenant_pseudonym": pseudonymize(event.tenant_id, pseudonym_secret, namespace="tenant"),
            "user_pseudonym": pseudonymize(event.user_id, pseudonym_secret, namespace="user"),
            "channel": event.channel,
            "event_type": event.event_type,
            "raw_text_present": event.raw_text_present,
            "redacted_text": redact_text(event.redacted_text) if event.redacted_text else "",
            "question": (
                redact_text(event.redacted_text)
                if event.event_type == "user_message_received" and event.redacted_text
                else ""
            ),
            "primary_intent": event.primary_intent or "unclassified",
            "business_function": event.business_function or "unclassified",
            "ai_help_type": event.ai_help_type or "unclassified",
            "complexity": event.complexity or "unclassified",
            "bumpa_data_used": event.bumpa_data_used or "none",
            "outcome_json": json.dumps(
                _safe_outcome(event.outcome),
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        for event in events
    ]


def _counts(rows: list[dict[str, Any]], key: str) -> tuple[tuple[str, int], ...]:
    counts = Counter(str(row.get(key) or "unclassified") for row in rows)
    return tuple(counts.most_common(8))


def _safe_outcome(value: Any, *, depth: int = 0) -> Any:
    """Re-redact bounded legacy outcome metadata for anonymous disclosure."""

    if depth >= 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)[:80]
            normalized = key.lower().replace("-", "_")
            if normalized in SENSITIVE_OUTCOME_KEYS or normalized.endswith("_id"):
                continue
            safe[key] = _safe_outcome(item, depth=depth + 1)
        return safe
    if isinstance(value, list):
        return [_safe_outcome(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value)[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))[:500]


def _outcome_counts(rows: list[dict[str, Any]]) -> tuple[tuple[str, int], ...]:
    counts: Counter[str] = Counter()
    for row in rows:
        outcome = row.get("outcome_json")
        if not isinstance(outcome, str):
            continue
        try:
            values = json.loads(outcome)
        except json.JSONDecodeError:
            continue
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if value not in (None, False, "", [], {}):
                counts[str(key)] += 1
    return tuple(counts.most_common(8))


def _document(
    report: ResearchReport,
    rows: list[dict[str, Any]],
    *,
    pseudonym_secret: str,
) -> ReportDocument:
    raw = report.report_type == RAW_REPORT_TYPE
    tenant_key = "tenant_id" if raw else "tenant_pseudonym"
    user_key = "user_id" if raw else "user_pseudonym"
    question_key = "raw_question" if raw else "question"
    question_rows = [row for row in rows if row.get("event_type") == "user_message_received"]
    tenant_count = len({row.get(tenant_key) for row in rows if row.get(tenant_key)})
    user_count = len({row.get(user_key) for row in rows if row.get(user_key)})
    earliest = min((str(row["timestamp"]) for row in rows), default="No events")
    latest = max((str(row["timestamp"]) for row in rows), default="No events")
    examples = [str(row[question_key]) for row in question_rows if row.get(question_key)][:5]
    filters = validated_filters(report.filters)
    if not raw and (tenant_id := filters.pop("tenant_id", None)):
        filters["tenant_pseudonym"] = pseudonymize(
            tenant_id,
            pseudonym_secret,
            namespace="tenant",
        )
    research_questions = {
        "sme_usage": "How, how often, and through which channels are SMEs using Bumpa Bestie?",
        "cohort_behavior": "How does repeated use vary across first-observed SME cohorts?",
        "question_taxonomy": "Which business questions and forms of AI help recur most often?",
        "business_outcome_correlation": "Which observed outcome signals co-occur with AI usage patterns?",
        "weekly_memo": "What changed in consented SME usage and question patterns this week?",
        "monthly_memo": "What monthly patterns are supported by the consented research evidence?",
        RAW_REPORT_TYPE: "What permissioned source records comprise this export package?",
        "anonymized_export_package": "What anonymized source records comprise this export package?",
    }
    sections = (
        ReportSection(
            "Executive summary",
            f"This report contains {len(rows):,} consented research events from "
            f"{tenant_count:,} SMEs and {user_count:,} users. Findings are descriptive, not causal.",
        ),
        ReportSection("Research question", research_questions[report.report_type]),
        ReportSection(
            "Dataset scope",
            f"Observed range: {earliest} to {latest}. Applied filters: "
            f"{json.dumps(filters, sort_keys=True) if filters else 'none'}. "
            f"The export is capped at {MAX_REPORT_ROWS:,} newest matching events.",
        ),
        ReportSection(
            "SME cohort summary",
            f"The dataset includes {tenant_count:,} consented SME workspaces.",
            _counts(rows, tenant_key),
        ),
        ReportSection(
            "AI usage summary",
            "Assistance modes recorded by the research classifier.",
            _counts(question_rows, "ai_help_type"),
        ),
        ReportSection(
            "Question taxonomy",
            "Primary question categories in the selected evidence window.",
            _counts(question_rows, "primary_intent"),
        ),
        ReportSection(
            "Channel behavior",
            "The channels through which consented participants asked questions.",
            _counts(question_rows, "channel"),
        ),
        ReportSection(
            "Bumpa-data usage",
            "Recorded Bumpa context categories used to support answers.",
            _counts(question_rows, "bumpa_data_used"),
        ),
        ReportSection(
            "Examples",
            "\n".join(f"- {example}" for example in examples)
            or "No question examples matched the selected filters.",
        ),
        ReportSection(
            "Observed decision patterns",
            "Business functions represented in the selected question set.",
            _counts(question_rows, "business_function"),
        ),
        ReportSection(
            "Outcome signals",
            "Recorded research outcome fields that co-occur with the selected events. These "
            "frequencies are descriptive and are not estimates of causal impact.",
            _outcome_counts(rows),
        ),
        ReportSection(
            "Operational recommendations",
            "Review repeated high-volume question categories with participating SMEs; investigate "
            "low-context answers; and compare patterns with independently collected outcome evidence "
            "before changing product or business policy.",
        ),
        ReportSection(
            "Caveats",
            "Participation is consent-dependent, classifications are automated, upstream Bumpa data "
            "may be stale or unavailable, and observed associations do not establish causality.",
        ),
        ReportSection(
            "Export metadata",
            f"Report ID: {report.id}. Artifact kind: {report.artifact_kind}. "
            f"Report type: {report.report_type}. "
            f"Disclosure mode: {'permissioned raw' if raw else 'anonymized and re-redacted'}.",
        ),
    )
    return ReportDocument(
        title=report.title or report.report_type.replace("_", " ").title(),
        report_type=report.report_type,
        generated_at=utcnow(),
        filters=filters,
        rows=rows,
        sections=sections,
        raw=raw,
    )


def _csv(document: ReportDocument) -> bytes:
    output = io.StringIO()
    fieldnames = (
        list(document.rows[0])
        if document.rows
        else (
            [
                "record_type",
                "timestamp",
                "event_id",
                "tenant_id",
                "user_id",
                "conversation_id",
                "agent_message_id",
                "channel",
                "event_type",
                "raw_text_present",
                "message_direction",
                "raw_message",
                "raw_question",
                "redacted_question",
                "primary_intent",
                "business_function",
                "ai_help_type",
                "complexity",
                "bumpa_data_used",
                "outcome_json",
                "pii_redacted",
            ]
            if document.raw
            else [
                "record_type",
                "timestamp",
                "event_pseudonym",
                "tenant_pseudonym",
                "user_pseudonym",
                "channel",
                "event_type",
                "raw_text_present",
                "redacted_text",
                "question",
                "primary_intent",
                "business_function",
                "ai_help_type",
                "complexity",
                "bumpa_data_used",
                "outcome_json",
            ]
        )
    )
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows({key: csv_safe(value) for key, value in row.items()} for row in document.rows)
    return output.getvalue().encode("utf-8-sig")


def _jsonl(document: ReportDocument) -> bytes:
    metadata = {
        "record_type": "report_metadata",
        "report_type": document.report_type,
        "generated_at": document.generated_at.isoformat(),
        "filters": document.filters,
        "row_count": len(document.rows),
        "disclosure_mode": "permissioned_raw" if document.raw else "anonymized_redacted",
    }
    records = [metadata, *document.rows]
    return (
        "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in records) + "\n"
    ).encode()


def _chart(counts: tuple[tuple[str, int], ...]) -> Drawing:
    selected = list(counts[:6])
    drawing = Drawing(174 * mm, 48 * mm)
    chart = HorizontalBarChart()
    chart.x = 52 * mm
    chart.y = 7 * mm
    chart.width = 110 * mm
    chart.height = 34 * mm
    chart.data = [[count for _label, count in reversed(selected)]]
    chart.categoryAxis.categoryNames = [label[:28] for label, _count in reversed(selected)]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.categoryAxis.strokeColor = colors.HexColor("#D9E2DD")
    chart.valueAxis.valueMin = 0
    maximum = max((count for _label, count in selected), default=1)
    step = max(1, ceil(maximum / 5))
    chart.valueAxis.valueStep = step
    chart.valueAxis.valueMax = max(1, ceil(maximum / step) * step)
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.strokeColor = colors.HexColor("#D9E2DD")
    chart.bars[0].fillColor = colors.HexColor("#16794B")
    chart.bars[0].strokeColor = colors.HexColor("#16794B")
    chart.barWidth = 4 * mm
    drawing.add(chart)
    return drawing


def _paragraph_text(value: str) -> str:
    return "<br/>".join(escape(line) for line in value.splitlines())


def _pdf(document: ReportDocument) -> bytes:
    output = io.BytesIO()
    pdf = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=document.title,
        author="Bumpa Bestie Research",
        subject=f"{document.report_type} research report",
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BumpaReportTitle",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=32,
        textColor=colors.HexColor("#12372A"),
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    eyebrow_style = ParagraphStyle(
        "BumpaReportEyebrow",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#16794B"),
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    heading_style = ParagraphStyle(
        "BumpaReportHeading",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#12372A"),
        spaceBefore=10,
        spaceAfter=7,
    )
    body_style = ParagraphStyle(
        "BumpaReportBody",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#26352F"),
        spaceAfter=8,
    )
    small_style = ParagraphStyle(
        "BumpaReportSmall",
        parent=body_style,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#56645E"),
    )
    badge_style = ParagraphStyle(
        "BumpaReportBadge",
        parent=body_style,
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=(colors.HexColor("#8A341F") if document.raw else colors.HexColor("#12663F")),
    )

    disclosure = (
        "PERMISSIONED RAW EXPORT — SUPERADMIN ACCESS ONLY"
        if document.raw
        else "ANONYMIZED AND RE-REDACTED RESEARCH OUTPUT"
    )
    story: list[Any] = [
        Spacer(1, 36 * mm),
        Paragraph("Bumpa Bestie Research", eyebrow_style),
        Paragraph(escape(document.title), title_style),
        Spacer(1, 8 * mm),
        Table(
            [[Paragraph(disclosure, badge_style)]],
            colWidths=[150 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF8F2")),
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#A9C9BA")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            ),
            hAlign="CENTER",
        ),
        Spacer(1, 12 * mm),
        Paragraph(
            f"Generated {escape(document.generated_at.astimezone(UTC).strftime('%d %B %Y, %H:%M UTC'))}",
            ParagraphStyle("BumpaReportDate", parent=small_style, alignment=TA_CENTER),
        ),
        Paragraph(
            f"{len(document.rows):,} research records | {escape(document.report_type)}",
            ParagraphStyle("BumpaReportCount", parent=small_style, alignment=TA_CENTER),
        ),
        PageBreak(),
    ]

    for section in document.sections:
        section_flowables: list[Any] = [
            Paragraph(escape(section.title), heading_style),
            Paragraph(_paragraph_text(section.body), body_style),
        ]
        if section.counts:
            section_flowables.append(_chart(section.counts))
        story.append(KeepTogether(section_flowables))
        story.append(Spacer(1, 3 * mm))

    story.extend(
        [
            PageBreak(),
            Paragraph("Record appendix", heading_style),
            Paragraph(
                "The appendix shows at most the first 100 matching records. Use the CSV or JSONL "
                "artifact for the complete machine-readable package.",
                body_style,
            ),
        ]
    )
    key_columns = (
        ("timestamp", "tenant_id", "channel", "raw_message")
        if document.raw
        else ("timestamp", "tenant_pseudonym", "channel", "redacted_text")
    )
    table_data: list[list[Any]] = [
        [Paragraph(escape(column.replace("_", " ").title()), small_style) for column in key_columns]
    ]
    for row in document.rows[:100]:
        table_data.append(
            [
                Paragraph(escape(str(row.get(column) or "—")[:600]), small_style)
                for column in key_columns
            ]
        )
    if len(table_data) == 1:
        table_data.append([Paragraph("No matching records", small_style), "", "", ""])
    appendix = Table(
        table_data,
        colWidths=[33 * mm, 42 * mm, 25 * mm, 74 * mm],
        repeatRows=1,
        hAlign="LEFT",
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEFE6")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C8D4CE")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        ),
    )
    story.append(appendix)

    def page_number(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64736C"))
        canvas.drawString(18 * mm, 10 * mm, "Bumpa Bestie Research")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return output.getvalue()


def _artifacts_are_intact(
    db: Session,
    store: LocalArtifactStore,
    report: ResearchReport,
    formats: set[str],
) -> bool:
    artifacts = list(db.scalars(select(Artifact).where(Artifact.report_id == report.id)).all())
    if {artifact.format for artifact in artifacts} != formats:
        return False
    for artifact in artifacts:
        try:
            content = store.get(artifact.storage_key)
        except (OSError, ValueError):
            return False
        if len(content) != artifact.byte_size:
            return False
        if not secrets.compare_digest(
            hashlib.sha256(content).hexdigest(), artifact.checksum_sha256
        ):
            return False
    return True


def _render_formats(document: ReportDocument, formats: set[str]) -> dict[str, tuple[bytes, str]]:
    rendered: dict[str, tuple[bytes, str]] = {}
    for fmt in sorted(formats):
        if fmt == "csv":
            rendered[fmt] = (_csv(document), "text/csv")
        elif fmt == "jsonl":
            rendered[fmt] = (_jsonl(document), "application/x-ndjson")
        elif fmt == "pdf":
            rendered[fmt] = (_pdf(document), "application/pdf")
    return rendered


def generate_report(
    db: Session,
    store: LocalArtifactStore,
    report: ResearchReport,
    formats: Sequence[str],
    *,
    pseudonym_secret: str,
) -> ResearchReport:
    requested_formats = set(formats)
    unsupported_formats = requested_formats - REPORT_FORMATS
    if not requested_formats or unsupported_formats:
        detail = f": {', '.join(sorted(unsupported_formats))}" if unsupported_formats else ""
        raise ValueError(f"Unsupported or empty report formats{detail}")
    if report.report_type not in REPORT_TYPES:
        raise ValueError("Unsupported report type")
    validated_filters(report.filters)
    if report.status == "success" and _artifacts_are_intact(db, store, report, requested_formats):
        return report
    if report.status == "success":
        deleted = purge_report_artifacts(db, store.root, report)
        audit(
            db,
            actor_user_id=report.generated_by,
            action="research.report.integrity_rebuild",
            resource_type="research_report",
            resource_id=report.id,
            after={"artifacts_purged": deleted, "formats": sorted(requested_formats)},
        )
    report.status = "running"
    report.error = None
    report.finished_at = None
    db.commit()
    stored_keys: list[str] = []
    try:
        raw = report.report_type == RAW_REPORT_TYPE
        events = _filtered_events(db, report.filters, pseudonym_secret=pseudonym_secret)
        rows = _rows(
            db,
            report.filters,
            pseudonym_secret=pseudonym_secret,
            raw=raw,
            events=events,
        )
        document = _document(report, rows, pseudonym_secret=pseudonym_secret)
        # Render the complete package before publishing bytes, so a renderer
        # failure cannot leave a partially successful multi-format report.
        rendered = _render_formats(document, requested_formats)
        purge_report_artifacts(db, store.root, report)
        # Artifact deletion mutates both the database and filesystem. Commit the
        # metadata deletion before writing replacements so a later rollback can
        # never resurrect rows that point at bytes already removed from disk.
        db.commit()
        for fmt, (content, content_type) in rendered.items():
            key, byte_size, checksum = store.put(f"{report.id}/{report.id}.{fmt}", content)
            stored_keys.append(key)
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
        disclosure = "permissioned raw" if raw else "anonymized and re-redacted"
        report.summary = f"Generated a {disclosure} report from {len(rows)} research events."
        report.finished_at = utcnow()
        audit(
            db,
            actor_user_id=report.generated_by,
            action="research.report.completed",
            resource_type="research_report",
            resource_id=report.id,
            after={
                "formats": sorted(requested_formats),
                "event_count": len(rows),
                "artifact_kind": report.artifact_kind,
                "disclosure_mode": "permissioned_raw" if raw else "anonymized_redacted",
            },
        )
        record_report_generated_events(
            db,
            report=report,
            tenant_ids=[event.tenant_id for event in events if event.tenant_id],
            event_count=len(rows),
            formats=sorted(requested_formats),
        )
        db.commit()
    except Exception:
        db.rollback()
        for key in stored_keys:
            candidate = (store.root / key).resolve()
            if store.root == candidate or store.root in candidate.parents:
                candidate.unlink(missing_ok=True)
        failed_report = db.get(ResearchReport, report.id)
        if failed_report is not None:
            purge_report_artifacts(db, store.root, failed_report)
            failed_report.status = "failed"
            failed_report.error = "Report generation failed"
            failed_report.finished_at = utcnow()
            db.commit()
        raise
    db.refresh(report)
    return report
