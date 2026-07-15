import hashlib
import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_researcher
from app.core.rate_limit import enforce_operation_rate_limit
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    Artifact,
    BumpaConnection,
    BumpaSyncRun,
    HermesProfile,
    ResearchEvent,
    ResearchReport,
    Tenant,
)
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
from app.services.bumpa_freshness import usable_bumpa_sync_run_predicate
from app.services.reports import (
    RAW_REPORT_TYPE,
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
SENSITIVE_REASON_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{8,}|sk-(?:ant-)?[a-z0-9_-]{8,}|"
    r"(?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*\S+)"
)


def _permissioned_access_reason(
    principal: Principal,
    access_reason: str | None,
    *,
    resource: str,
) -> str:
    if not principal.has_platform_role("superadmin"):
        raise HTTPException(status_code=403, detail=f"Superadmin {resource} access required")
    reason = (access_reason or "").strip()
    if len(reason) < 12 or redact_text(reason) != reason or SENSITIVE_REASON_RE.search(reason):
        raise HTTPException(
            status_code=422,
            detail="Access reason must be specific and contain no PII or secrets",
        )
    return reason


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _distribution(values: Iterable[str | None], *, empty: str = "unclassified") -> dict[str, int]:
    return dict(Counter(value or empty for value in values).most_common())


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil((percentile / 100) * len(ordered)) - 1)
    return ordered[index]


def _top_labels(counts: Counter[str], *, limit: int = 8) -> list[dict[str, str | int]]:
    return [
        {"label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _safe_question(value: str | None) -> str | None:
    if not value:
        return None
    safe = " ".join(redact_text(value).split())
    if not safe:
        return None
    return f"{safe[:157]}..." if len(safe) > 160 else safe


def _top_questions(
    events: Iterable[ResearchEvent],
    *,
    matches: Callable[[ResearchEvent], bool],
    limit: int = 5,
) -> list[dict[str, str | int]]:
    labels: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for event in events:
        if not matches(event):
            continue
        question = _safe_question(event.redacted_text)
        if not question:
            continue
        key = question.casefold()
        labels.setdefault(key, question)
        counts[key] += 1
    return [
        {"label": labels[key], "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], labels[item[0]]))[
            :limit
        ]
    ]


def _retention_cohorts(
    tenant_activity: dict[str, list[datetime]], *, now: datetime
) -> list[dict[str, object]]:
    cohorts: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for timestamps in tenant_activity.values():
        ordered = sorted(_as_utc(item) for item in timestamps)
        first, last = ordered[0], ordered[-1]
        cohorts[first.strftime("%Y-%m")].append((first, last))

    rows: list[dict[str, object]] = []
    for cohort, activity in sorted(cohorts.items()):
        eligible_7d = sum(now >= first + timedelta(days=7) for first, _last in activity)
        retained_7d = sum(
            now >= first + timedelta(days=7) and last >= first + timedelta(days=7)
            for first, last in activity
        )
        eligible_30d = sum(now >= first + timedelta(days=30) for first, _last in activity)
        retained_30d = sum(
            now >= first + timedelta(days=30) and last >= first + timedelta(days=30)
            for first, last in activity
        )
        rows.append(
            {
                "cohort": cohort,
                "smes": len(activity),
                "eligible_7d": eligible_7d,
                "retained_7d": retained_7d,
                "retention_7d_pct": (
                    round((retained_7d / eligible_7d) * 100, 1) if eligible_7d else None
                ),
                "eligible_30d": eligible_30d,
                "retained_30d": retained_30d,
                "retention_30d_pct": (
                    round((retained_30d / eligible_30d) * 100, 1) if eligible_30d else None
                ),
            }
        )
    return rows[-12:]


def _research_event_view(event: ResearchEvent, settings: Settings) -> ResearchConversationEventView:
    """Serialize an event through the research-safe disclosure boundary."""

    return ResearchConversationEventView(
        id=pseudonymize(event.id, settings.research_pseudonym_key, namespace="event"),
        user_pseudonym=(
            pseudonymize(event.user_id, settings.research_pseudonym_key, namespace="user")
            if event.user_id
            else None
        ),
        channel=event.channel,
        event_type=event.event_type,
        raw_text_present=event.raw_text_present,
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
        id=pseudonymize(conversation_id, settings.research_pseudonym_key, namespace="conversation"),
        tenant_pseudonym=(
            pseudonymize(tenant_id, settings.research_pseudonym_key, namespace="tenant")
            if tenant_id
            else None
        ),
        participant_pseudonyms=[
            pseudonymize(user_id, settings.research_pseudonym_key, namespace="user")
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
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    now = utcnow()
    tenant_count = db.scalar(select(func.count()).select_from(Tenant)) or 0
    consent_rows = db.execute(
        select(Tenant.research_consent_status, func.count())
        .group_by(Tenant.research_consent_status)
        .order_by(Tenant.research_consent_status)
    ).all()
    consented_events = list(
        db.scalars(
            select(ResearchEvent)
            .join(Tenant, ResearchEvent.tenant_id == Tenant.id)
            .where(Tenant.research_consent_status == "granted")
            .order_by(ResearchEvent.created_at)
        ).all()
    )
    question_events = [
        event for event in consented_events if event.event_type == "user_message_received"
    ]

    tenant_activity: dict[str, list[datetime]] = defaultdict(list)
    channel_users: dict[str, set[str]] = defaultdict(set)
    for event in question_events:
        if event.tenant_id:
            tenant_activity[event.tenant_id].append(_as_utc(event.created_at))
        if event.user_id:
            channel_users[event.channel].add(event.user_id)

    active_smes = {
        "day": sum(
            any(timestamp >= now - timedelta(days=1) for timestamp in timestamps)
            for timestamps in tenant_activity.values()
        ),
        "week": sum(
            any(timestamp >= now - timedelta(days=7) for timestamp in timestamps)
            for timestamps in tenant_activity.values()
        ),
        "month": sum(
            any(timestamp >= now - timedelta(days=30) for timestamp in timestamps)
            for timestamps in tenant_activity.values()
        ),
    }

    conversation_ids = sorted(
        {event.conversation_id for event in question_events if event.conversation_id}
    )
    latency_values: list[int] = []
    if conversation_ids:
        latency_values = [
            value
            for value in db.scalars(
                select(AgentMessage.latency_ms)
                .join(HermesProfile, AgentMessage.hermes_profile_id == HermesProfile.id)
                .where(
                    AgentMessage.conversation_id.in_(conversation_ids),
                    AgentMessage.direction == "outbound",
                    AgentMessage.latency_ms.is_not(None),
                    HermesProfile.provider == "hermes",
                )
            ).all()
            if value is not None
        ]

    connection_count = (
        db.scalar(
            select(func.count())
            .select_from(BumpaConnection)
            .join(Tenant, BumpaConnection.tenant_id == Tenant.id)
            .where(
                Tenant.research_consent_status == "granted",
                BumpaConnection.status == "active",
            )
        )
        or 0
    )
    sync_times = [
        _as_utc(timestamp)
        for timestamp in db.scalars(
            select(func.max(BumpaSyncRun.finished_at))
            .join(
                BumpaConnection,
                BumpaConnection.id == BumpaSyncRun.bumpa_connection_id,
            )
            .join(Tenant, Tenant.id == BumpaConnection.tenant_id)
            .where(
                Tenant.research_consent_status == "granted",
                BumpaConnection.status == "active",
                BumpaSyncRun.tenant_id == BumpaConnection.tenant_id,
                BumpaSyncRun.boundary_revision == BumpaConnection.boundary_revision,
                usable_bumpa_sync_run_predicate(),
            )
            .group_by(BumpaConnection.id, BumpaConnection.boundary_revision)
        ).all()
        if timestamp is not None
    ]
    fresh_24h = sum(timestamp >= now - timedelta(hours=24) for timestamp in sync_times)
    stale_24_to_72h = sum(
        now - timedelta(hours=72) <= timestamp < now - timedelta(hours=24)
        for timestamp in sync_times
    )
    overdue_72h = sum(timestamp < now - timedelta(hours=72) for timestamp in sync_times)

    reports = list(db.scalars(select(ResearchReport)).all())
    artifacts = list(db.scalars(select(Artifact)).all())

    repeat_rows: list[dict[str, object]] = []
    repeat_smes = 0
    for tenant_id, timestamps in tenant_activity.items():
        ordered = sorted(timestamps)
        active_days = len({timestamp.date() for timestamp in ordered})
        if active_days >= 2:
            repeat_smes += 1
        repeat_rows.append(
            {
                "tenant_pseudonym": pseudonymize(
                    tenant_id,
                    settings.research_pseudonym_key,
                    namespace="tenant",
                ),
                "event_count": len(ordered),
                "active_days": active_days,
                "first_seen_at": ordered[0].isoformat(),
                "last_seen_at": ordered[-1].isoformat(),
            }
        )
    repeat_rows.sort(
        key=lambda row: (
            -cast(int, row["active_days"]),
            -cast(int, row["event_count"]),
            str(row["tenant_pseudonym"]),
        )
    )

    questions_by_category = _distribution(event.primary_intent for event in question_events)
    recurring_problems = Counter(
        event.primary_intent or "unclassified" for event in question_events
    )

    return {
        "generated_at": now.isoformat(),
        "smes_onboarded": tenant_count,
        "research_consent_status": {status: count for status, count in consent_rows},
        "research_events": len(consented_events),
        "active_smes": active_smes,
        "active_users_by_channel": {
            channel: len(users) for channel, users in sorted(channel_users.items())
        },
        "messages_by_channel": _distribution(event.channel for event in question_events),
        "questions_by_category": questions_by_category,
        # Preserve the original response field for existing API consumers.
        "questions_by_intent": questions_by_category,
        "questions_by_business_function": _distribution(
            event.business_function for event in question_events
        ),
        "questions_by_complexity": _distribution(event.complexity for event in question_events),
        "questions_by_ai_help_type": _distribution(event.ai_help_type for event in question_events),
        "bumpa_data_usage": _distribution(
            (event.bumpa_data_used for event in question_events),
            empty="none",
        ),
        "hermes_response_latency": {
            "samples": len(latency_values),
            "average_ms": (
                round(sum(latency_values) / len(latency_values)) if latency_values else None
            ),
            "p50_ms": _percentile(latency_values, 50),
            "p95_ms": _percentile(latency_values, 95),
        },
        "bumpa_sync_freshness": {
            "connected_smes": connection_count,
            "fresh_24h": fresh_24h,
            "stale_24_to_72h": stale_24_to_72h,
            "overdue_72h": overdue_72h,
            "never_synced": connection_count - len(sync_times),
            "latest_sync_at": max(sync_times).isoformat() if sync_times else None,
            "oldest_sync_at": min(sync_times).isoformat() if sync_times else None,
        },
        "report_generation": {
            "total": len(reports),
            "by_status": _distribution(report.status for report in reports),
            "by_type": _distribution(report.report_type for report in reports),
        },
        "exports": {
            "total": len(artifacts),
            "by_format": _distribution(artifact.format for artifact in artifacts),
        },
        "retention_by_cohort": _retention_cohorts(tenant_activity, now=now),
        "repeat_usage": {
            "smes_observed": len(tenant_activity),
            "repeat_smes": repeat_smes,
            "repeat_rate_pct": (
                round((repeat_smes / len(tenant_activity)) * 100, 1) if tenant_activity else None
            ),
            "by_sme": repeat_rows[:10],
        },
        "top_recurring_problems": _top_labels(recurring_problems),
        "most_common_sales_questions": _top_questions(
            question_events,
            matches=lambda event: (
                event.primary_intent == "sales_analysis" or event.business_function == "sales"
            ),
        ),
        "most_common_inventory_questions": _top_questions(
            question_events,
            matches=lambda event: (
                event.primary_intent == "inventory_management" or event.business_function == "stock"
            ),
        ),
        "most_common_customer_questions": _top_questions(
            question_events,
            matches=lambda event: (
                event.primary_intent == "customer_management"
                or event.business_function == "customers"
            ),
        ),
        "most_common_advice_requests": _top_questions(
            question_events,
            matches=lambda event: (
                event.primary_intent == "general_business_advice"
                or event.ai_help_type in {"recommendation", "forecast", "draft_message", "teaching"}
            ),
        ),
    }


@router.get("/events")
def events(
    _principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    tenant_id: str | None = None,
    channel: str | None = None,
    primary_intent: str | None = None,
    event_type: str | None = None,
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
    if event_type:
        statement = statement.where(ResearchEvent.event_type == event_type)
    rows = db.scalars(statement).all()
    return [
        {
            "id": pseudonymize(row.id, settings.research_pseudonym_key, namespace="event"),
            "tenant_pseudonym": pseudonymize(
                row.tenant_id, settings.research_pseudonym_key, namespace="tenant"
            ),
            "user_pseudonym": pseudonymize(
                row.user_id, settings.research_pseudonym_key, namespace="user"
            ),
            "channel": row.channel,
            "event_type": row.event_type,
            "raw_text_present": row.raw_text_present,
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
                pseudonymize(candidate, settings.research_pseudonym_key, namespace="conversation"),
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
    return events(
        principal,
        db,
        settings,
        event_type="user_message_received",
        limit=limit,
    )


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

    reason = _permissioned_access_reason(
        principal,
        access_reason,
        resource="raw event",
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

    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=event.tenant_id,
        action="research.raw_event.accessed",
        resource_type="research_event",
        resource_id=event.id,
        after={"reason": reason, "fields": ["raw_question"], "request_scoped": True},
    )
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
    access_reason: str | None = Header(
        default=None,
        alias="X-Access-Reason",
        max_length=240,
    ),
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResearchReport:
    return _create_artifact(
        payload,
        principal,
        db,
        settings,
        artifact_kind="report",
        access_reason=access_reason,
    )


def _raw_export_reason(
    report_type: str,
    principal: Principal,
    access_reason: str | None,
) -> str | None:
    if report_type != RAW_REPORT_TYPE:
        return None
    return _permissioned_access_reason(
        principal,
        access_reason,
        resource="raw export",
    )


def _create_artifact(
    payload: ReportCreate,
    principal: Principal,
    db: Session,
    settings: Settings,
    *,
    artifact_kind: str,
    access_reason: str | None,
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
    if payload.report_type in {"weekly_memo", "monthly_memo"} and not {
        "date_from",
        "date_to",
    }.intersection(filters):
        today = utcnow().date()
        window_days = 6 if payload.report_type == "weekly_memo" else 29
        filters = {
            **filters,
            "date_from": (today - timedelta(days=window_days)).isoformat(),
            "date_to": today.isoformat(),
        }
    if payload.report_type == RAW_REPORT_TYPE and artifact_kind != "export":
        raise HTTPException(status_code=422, detail="Raw packages must use the export endpoint")
    sensitive_reason = _raw_export_reason(payload.report_type, principal, access_reason)
    report = ResearchReport(
        report_type=payload.report_type,
        artifact_kind=artifact_kind,
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
    audit_filters = dict(filters)
    if tenant_id := audit_filters.pop("tenant_id", None):
        audit_filters["tenant_pseudonym"] = pseudonymize(
            tenant_id,
            settings.research_pseudonym_key,
            namespace="tenant",
        )
    audit(
        db,
        actor_user_id=principal.user.id,
        action="research.report.requested",
        resource_type="research_report",
        resource_id=report.id,
        after={
            "formats": sorted(set(payload.formats)),
            "filters": audit_filters,
            "queued": queued,
            "artifact_kind": artifact_kind,
            "sensitive": sensitive_reason is not None,
            **({"reason": sensitive_reason} if sensitive_reason else {}),
        },
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
        pseudonym_secret=settings.research_pseudonym_key,
    )


@router.get("/reports", response_model=list[ReportView])
def reports(
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    artifact_kind: str | None = Query(default=None, pattern="^(report|export)$"),
) -> list[ResearchReport]:
    cleanup_expired_report_artifacts(db, settings.artifact_root)
    statement = select(ResearchReport).order_by(ResearchReport.created_at.desc())
    if not principal.has_platform_role("superadmin"):
        statement = statement.where(ResearchReport.generated_by == principal.user.id)
    if artifact_kind:
        statement = statement.where(ResearchReport.artifact_kind == artifact_kind)
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
        "report_type": row.report_type,
        "artifact_kind": row.artifact_kind,
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
    access_reason: str | None = Header(
        default=None,
        alias="X-Access-Reason",
        max_length=240,
    ),
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    report_row = db.get(ResearchReport, report_id)
    if not report_row:
        raise HTTPException(status_code=404, detail="Report not found")
    _authorize_report(report_row, principal)
    sensitive_reason = _raw_export_reason(
        report_row.report_type,
        principal,
        access_reason,
    )
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
    store = LocalArtifactStore(settings.artifact_root)
    try:
        content = store.get(artifact.storage_key)
    except (OSError, ValueError):
        content = b""
    checksum = hashlib.sha256(content).hexdigest()
    if len(content) != artifact.byte_size or not secrets.compare_digest(
        checksum, artifact.checksum_sha256
    ):
        deleted = purge_report_artifacts(db, settings.artifact_root, report_row)
        audit(
            db,
            actor_user_id=principal.user.id,
            action="research.report.integrity_failed",
            resource_type="research_report",
            resource_id=report_row.id,
            after={"artifact_count": deleted, "format": format},
        )
        db.commit()
        raise HTTPException(status_code=410, detail="Report artifact failed integrity validation")
    audit(
        db,
        actor_user_id=principal.user.id,
        action="research.report.downloaded",
        resource_type="research_report",
        resource_id=report_row.id,
        after={
            "format": format,
            "sensitive": sensitive_reason is not None,
            **({"reason": sensitive_reason} if sensitive_reason else {}),
        },
    )
    db.commit()
    return Response(
        content=content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="research-report-{report_id}.{format}"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/exports", response_model=ReportView)
def create_export(
    payload: ReportCreate,
    access_reason: str | None = Header(
        default=None,
        alias="X-Access-Reason",
        max_length=240,
    ),
    principal: Principal = Depends(require_researcher),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResearchReport:
    return _create_artifact(
        payload,
        principal,
        db,
        settings,
        artifact_kind="export",
        access_reason=access_reason,
    )


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
