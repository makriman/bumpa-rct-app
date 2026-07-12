from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_researcher
from app.db.models import Artifact, ResearchEvent, ResearchReport, Tenant
from app.db.session import get_db
from app.providers.local import LocalArtifactStore
from app.providers.redaction import pseudonymize, redact_text
from app.schemas import ReportCreate, ReportView
from app.services.audit import audit
from app.services.reports import generate_report

router = APIRouter(prefix="/research", tags=["research"])


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


@router.post("/reports", response_model=ReportView, status_code=201)
def create_report(
    payload: ReportCreate,
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResearchReport:
    if not settings.is_local:
        raise HTTPException(
            status_code=503,
            detail="No production report queue adapter is configured",
        )
    report = ResearchReport(
        report_type=payload.report_type,
        generated_by=principal.user.id,
        filters=payload.filters,
        title=payload.report_type.replace("_", " ").title(),
    )
    db.add(report)
    db.flush()
    audit(
        db,
        actor_user_id=principal.user.id,
        action="research.report.generated",
        resource_type="research_report",
        resource_id=report.id,
        after={"formats": payload.formats, "filters": payload.filters},
    )
    db.commit()
    return generate_report(
        db,
        LocalArtifactStore(settings.artifact_root),
        report,
        payload.formats,
        pseudonym_secret=settings.field_encryption_key,
    )


@router.get("/reports", response_model=list[ReportView])
def reports(
    _principal: Principal = Depends(require_researcher), db: Session = Depends(get_db)
) -> list[ResearchReport]:
    return list(db.scalars(select(ResearchReport).order_by(ResearchReport.created_at.desc())).all())


@router.get("/reports/{report_id}")
def report(
    report_id: str,
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ResearchReport, report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    artifacts = db.scalars(select(Artifact).where(Artifact.report_id == row.id)).all()
    return {
        "id": row.id,
        "status": row.status,
        "title": row.title,
        "summary": row.summary,
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
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    artifact = db.scalar(
        select(Artifact).where(Artifact.report_id == report_id, Artifact.format == format)
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    content = LocalArtifactStore(settings.artifact_root).get(artifact.storage_key)
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
