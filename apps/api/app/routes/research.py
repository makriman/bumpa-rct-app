import ipaddress
import secrets
from collections import Counter
from datetime import UTC

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_researcher
from app.core.rate_limit import enforce_operation_rate_limit
from app.core.time import utcnow
from app.db.models import AgentMessage, Artifact, ResearchEvent, ResearchReport, Tenant
from app.db.session import get_db
from app.jobs.runtime import AsyncRuntimeConfig, enqueue_job
from app.providers.local import LocalArtifactStore
from app.providers.redaction import pseudonymize, redact_text
from app.schemas import (
    ReportCreate,
    ReportView,
    ResearchConversationDetailView,
    ResearchConversationEventView,
    ResearchConversationSummaryView,
)
from app.services.audit import audit
from app.services.reports import (
    cleanup_expired_report_artifacts,
    delete_report,
    generate_report,
    purge_report_artifacts,
    report_consent_revoked,
    report_expires_at,
    report_is_expired,
    validated_filters,
)

router = APIRouter(prefix="/research", tags=["research"])


def _research_event_view(event: ResearchEvent, settings: Settings) -> ResearchConversationEventView:
    """Serialize an event through the research-safe disclosure boundary."""

    return ResearchConversationEventView(
        id=pseudonymize(event.id, settings.field_encryption_key, namespace="event"),
        user_pseudonym=(
            pseudonymize(event.user_id, settings.field_encryption_key, namespace="user")
            if event.user_id
            else None
        ),
        channel=event.channel,
        event_type=event.event_type,
        # Re-redact at read time so legacy rows cannot bypass current rules.
        redacted_text=redact_text(event.redacted_text) if event.redacted_text else None,
        primary_intent=event.primary_intent,
        business_function=event.business_function,
        ai_help_type=event.ai_help_type,
        complexity=event.complexity,
        bumpa_data_used=event.bumpa_data_used,
        created_at=event.created_at,
    )


def _conversation_view(
    conversation_id: str, events: list[ResearchEvent], settings: Settings
) -> ResearchConversationSummaryView:
    ordered = sorted(events, key=lambda event: event.created_at)
    latest = ordered[-1]
    tenant_id = next((event.tenant_id for event in ordered if event.tenant_id), None)
    user_ids = sorted({event.user_id for event in ordered if event.user_id})
    intent_counts = Counter(event.primary_intent or "unclassified" for event in ordered)
    return ResearchConversationSummaryView(
        id=pseudonymize(conversation_id, settings.field_encryption_key, namespace="conversation"),
        tenant_pseudonym=(
            pseudonymize(tenant_id, settings.field_encryption_key, namespace="tenant")
            if tenant_id
            else None
        ),
        participant_pseudonyms=[
            pseudonymize(user_id, settings.field_encryption_key, namespace="user")
            for user_id in user_ids
        ],
        channel=latest.channel,
        event_count=len(ordered),
        primary_intents=dict(intent_counts.most_common()),
        latest_redacted_text=(redact_text(latest.redacted_text) if latest.redacted_text else None),
        started_at=ordered[0].created_at,
        last_activity_at=latest.created_at,
    )


@router.get("/overview")
def overview(
    _principal: Principal = Depends(require_researcher), db: Session = Depends(get_db)
) -> dict:
    tenant_count = db.scalar(select(func.count()).select_from(Tenant)) or 0
    consent_rows = db.execute(
        select(Tenant.research_consent_status, func.count())
        .group_by(Tenant.research_consent_status)
        .order_by(Tenant.research_consent_status)
    ).all()
    consented_events = (
        select(ResearchEvent)
        .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
        .where(Tenant.research_consent_status == "granted")
    )
    events = db.scalars(consented_events).all()
    return {
        "smes_onboarded": tenant_count,
        "research_consent_status": {status: count for status, count in consent_rows},
        "research_events": len(events),
        "messages_by_channel": dict(Counter(item.channel for item in events)),
        "questions_by_intent": dict(
            Counter(item.primary_intent or "unclassified" for item in events)
        ),
        "bumpa_data_usage": dict(Counter(item.bumpa_data_used or "none" for item in events)),
    }


@router.get("/events")
def events(
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    tenant_id: str | None = None,
    channel: str | None = None,
    primary_intent: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    statement = (
        select(ResearchEvent)
        .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
        .where(Tenant.research_consent_status == "granted")
        .order_by(ResearchEvent.created_at.desc())
        .limit(limit)
    )
    if tenant_id:
        statement = statement.where(ResearchEvent.tenant_id == tenant_id)
    if channel:
        statement = statement.where(ResearchEvent.channel == channel)
    if primary_intent:
        statement = statement.where(ResearchEvent.primary_intent == primary_intent)
    rows = db.scalars(statement).all()
    return [
        {
            "id": pseudonymize(row.id, settings.field_encryption_key, namespace="event"),
            "tenant_pseudonym": pseudonymize(
                row.tenant_id, settings.field_encryption_key, namespace="tenant"
            ),
            "user_pseudonym": pseudonymize(
                row.user_id, settings.field_encryption_key, namespace="user"
            ),
            "channel": row.channel,
            "event_type": row.event_type,
            "redacted_text": redact_text(row.redacted_text) if row.redacted_text else None,
            "primary_intent": row.primary_intent,
            "business_function": row.business_function,
            "ai_help_type": row.ai_help_type,
            "complexity": row.complexity,
            "bumpa_data_used": row.bumpa_data_used,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/conversations", response_model=list[ResearchConversationSummaryView])
def conversations(
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    channel: str | None = None,
    primary_intent: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ResearchConversationSummaryView]:
    """List consented conversations without disclosing platform identifiers or raw text."""

    grouped = (
        select(
            ResearchEvent.conversation_id,
            func.max(ResearchEvent.created_at).label("last_activity_at"),
        )
        .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
        .where(
            Tenant.research_consent_status == "granted",
            ResearchEvent.conversation_id.is_not(None),
        )
        .group_by(ResearchEvent.conversation_id)
        .order_by(func.max(ResearchEvent.created_at).desc())
        .limit(limit)
    )
    if channel:
        grouped = grouped.where(ResearchEvent.channel == channel)
    if primary_intent:
        grouped = grouped.where(ResearchEvent.primary_intent == primary_intent)
    conversation_ids = [row.conversation_id for row in db.execute(grouped) if row.conversation_id]
    if not conversation_ids:
        return []

    rows = list(
        db.scalars(
            select(ResearchEvent)
            .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
            .where(
                Tenant.research_consent_status == "granted",
                ResearchEvent.conversation_id.in_(conversation_ids),
            )
            .order_by(ResearchEvent.created_at)
        ).all()
    )
    by_conversation: dict[str, list[ResearchEvent]] = {
        conversation_id: [] for conversation_id in conversation_ids
    }
    for row in rows:
        if row.conversation_id in by_conversation:
            by_conversation[row.conversation_id].append(row)
    return [
        _conversation_view(conversation_id, by_conversation[conversation_id], settings)
        for conversation_id in conversation_ids
        if by_conversation[conversation_id]
    ]


@router.get(
    "/conversations/{conversation_pseudonym}",
    response_model=ResearchConversationDetailView,
)
def conversation_detail(
    conversation_pseudonym: str,
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResearchConversationDetailView:
    """Return the redacted research-event timeline for one pseudonymous conversation."""

    candidate_ids = db.scalars(
        select(ResearchEvent.conversation_id)
        .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
        .where(
            Tenant.research_consent_status == "granted",
            ResearchEvent.conversation_id.is_not(None),
        )
        .distinct()
    ).all()
    conversation_id = next(
        (
            candidate
            for candidate in candidate_ids
            if candidate
            and secrets.compare_digest(
                pseudonymize(candidate, settings.field_encryption_key, namespace="conversation"),
                conversation_pseudonym,
            )
        ),
        None,
    )
    if not conversation_id:
        raise HTTPException(status_code=404, detail="Research conversation not found")

    rows = list(
        db.scalars(
            select(ResearchEvent)
            .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
            .where(
                Tenant.research_consent_status == "granted",
                ResearchEvent.conversation_id == conversation_id,
            )
            .order_by(ResearchEvent.created_at)
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Research conversation not found")
    summary = _conversation_view(conversation_id, rows, settings)
    return ResearchConversationDetailView(
        **summary.model_dump(),
        events=[_research_event_view(event, settings) for event in rows],
    )


@router.get("/questions")
def questions(
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    return events(principal, db, settings, limit=limit)


@router.get("/taxonomy")
def taxonomy(_principal: Principal = Depends(require_researcher)) -> dict:
    return {
        "primary_intent": [
            "sales_analysis",
            "inventory_management",
            "customer_management",
            "marketing",
            "finance",
            "operations",
            "order_management",
            "product_strategy",
            "platform_support",
            "general_business_advice",
            "other",
        ],
        "business_function": [
            "sales",
            "stock",
            "customers",
            "ads",
            "finance",
            "fulfillment",
            "staff",
            "strategy",
            "admin",
        ],
        "ai_help_type": [
            "data_lookup",
            "explanation",
            "diagnosis",
            "recommendation",
            "forecast",
            "report",
            "draft_message",
            "teaching",
            "troubleshooting",
        ],
        "complexity": [
            "simple_lookup",
            "single_step_reasoning",
            "multi_step_reasoning",
            "strategic_reasoning",
        ],
    }


@router.get("/events/{event_id}/raw")
def raw_event(
    event_id: str,
    request: Request,
    access_reason: str = Header(
        alias="X-Access-Reason",
        min_length=12,
        max_length=240,
    ),
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Disclose one raw question to a superadmin for this request only.

    The response is deliberately non-cacheable. There is no durable raw-access
    grant to forget to revoke: every disclosure requires fresh authorization and
    a recorded reason.
    """

    if not principal.has_platform_role("superadmin"):
        raise HTTPException(status_code=403, detail="Superadmin raw access required")
    reason = access_reason.strip()
    if len(reason) < 12 or redact_text(reason) != reason:
        raise HTTPException(
            status_code=422, detail="Access reason must be specific and contain no PII"
        )
    event = db.get(ResearchEvent, event_id)
    if not event or not event.tenant_id:
        raise HTTPException(status_code=404, detail="Research event not found")
    tenant = db.get(Tenant, event.tenant_id)
    if not tenant or tenant.research_consent_status != "granted":
        raise HTTPException(status_code=404, detail="Research event not found")
    message = db.get(AgentMessage, event.agent_message_id) if event.agent_message_id else None
    if not message or message.tenant_id != event.tenant_id:
        raise HTTPException(status_code=404, detail="Raw question is unavailable")

    record = audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=event.tenant_id,
        action="research.raw_event.accessed",
        resource_type="research_event",
        resource_id=event.id,
        after={"reason": reason, "fields": ["raw_question"], "request_scoped": True},
    )
    if request.client:
        try:
            record.ip_address = ipaddress.ip_address(request.client.host).compressed
        except ValueError:
            record.ip_address = None
    record.user_agent = (request.headers.get("user-agent") or "")[:500] or None
    db.commit()
    return JSONResponse(
        {
            "event_id": event.id,
            "raw_question": message.content,
            "accessed_at": utcnow().astimezone(UTC).isoformat(),
        },
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@router.post("/reports", response_model=ReportView, status_code=201)
def create_report(
    payload: ReportCreate,
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResearchReport:
    enforce_operation_rate_limit(
        settings,
        operation="research-report",
        scopes={"user": principal.user.id},
        limit=settings.research_report_rate_limit,
        window_seconds=settings.research_report_rate_limit_window_seconds,
    )
    if not payload.formats:
        raise HTTPException(status_code=422, detail="At least one report format is required")
    try:
        filters = validated_filters(payload.filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report = ResearchReport(
        report_type=payload.report_type,
        generated_by=principal.user.id,
        filters=filters,
        title=payload.report_type.replace("_", " ").title(),
    )
    db.add(report)
    db.flush()
    queued = not settings.is_local
    if queued:
        if not AsyncRuntimeConfig.from_env().enabled:
            raise HTTPException(status_code=503, detail="Report generation queue is unavailable")
        enqueue_job(
            db,
            kind="research.generate_report",
            payload={"report_id": report.id, "formats": sorted(set(payload.formats))},
            idempotency_key=f"research-report:{report.id}",
            max_attempts=3,
        )
    audit(
        db,
        actor_user_id=principal.user.id,
        action="research.report.requested",
        resource_type="research_report",
        resource_id=report.id,
        after={"formats": sorted(set(payload.formats)), "filters": filters, "queued": queued},
    )
    db.commit()
    if queued:
        db.refresh(report)
        return report
    return generate_report(
        db,
        LocalArtifactStore(settings.artifact_root),
        report,
        payload.formats,
        pseudonym_secret=settings.field_encryption_key,
    )


@router.get("/reports", response_model=list[ReportView])
def reports(
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ResearchReport]:
    cleanup_expired_report_artifacts(db, settings.artifact_root)
    statement = select(ResearchReport).order_by(ResearchReport.created_at.desc())
    if not principal.has_platform_role("superadmin"):
        statement = statement.where(ResearchReport.generated_by == principal.user.id)
    return [row for row in db.scalars(statement).all() if not report_is_expired(row)]


@router.get("/reports/{report_id}")
def report(
    report_id: str,
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = db.get(ResearchReport, report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    _authorize_report(row, principal)
    if report_is_expired(row) or report_consent_revoked(db, row):
        purge_report_artifacts(db, settings.artifact_root, row)
        db.commit()
    artifacts = db.scalars(select(Artifact).where(Artifact.report_id == row.id)).all()
    return {
        "id": row.id,
        "status": row.status,
        "title": row.title,
        "summary": row.summary,
        "expires_at": report_expires_at(row),
        "expired": report_is_expired(row),
        "artifacts": [
            {
                "format": item.format,
                "byte_size": item.byte_size,
                "checksum_sha256": item.checksum_sha256,
            }
            for item in artifacts
        ],
    }


@router.get("/reports/{report_id}/download/{format}")
def download_report(
    report_id: str,
    format: str,
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    report_row = db.get(ResearchReport, report_id)
    if not report_row:
        raise HTTPException(status_code=404, detail="Report not found")
    _authorize_report(report_row, principal)
    if report_is_expired(report_row) or report_consent_revoked(db, report_row):
        deleted = purge_report_artifacts(db, settings.artifact_root, report_row)
        audit(
            db,
            actor_user_id=principal.user.id,
            action="research.report.expired",
            resource_type="research_report",
            resource_id=report_row.id,
            after={"artifact_count": deleted},
        )
        db.commit()
        raise HTTPException(status_code=410, detail="Report artifact has expired")
    artifact = db.scalar(
        select(Artifact).where(Artifact.report_id == report_id, Artifact.format == format)
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    content = LocalArtifactStore(settings.artifact_root).get(artifact.storage_key)
    audit(
        db,
        actor_user_id=principal.user.id,
        action="research.report.downloaded",
        resource_type="research_report",
        resource_id=report_row.id,
        after={"format": format},
    )
    db.commit()
    return Response(
        content=content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="research-report-{report_id}.{format}"'
        },
    )


@router.post("/exports", response_model=ReportView)
def create_export(
    payload: ReportCreate,
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResearchReport:
    return create_report(payload, principal, db, settings)


@router.delete("/reports/{report_id}", status_code=204)
def remove_report(
    report_id: str,
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = db.get(ResearchReport, report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    _authorize_report(row, principal)
    delete_report(db, settings.artifact_root, row, actor_user_id=principal.user.id)
    return Response(status_code=204)


def _authorize_report(report: ResearchReport, principal: Principal) -> None:
    if report.generated_by != principal.user.id and not principal.has_platform_role("superadmin"):
        # Do not disclose another researcher's report identifiers.
        raise HTTPException(status_code=404, detail="Report not found")
