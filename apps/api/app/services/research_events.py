"""Consent-gated, exactly-once research evidence writers.

This module is the sole write boundary for structured research events. Callers
own their transaction: these helpers never commit or roll back, so product state
and the evidence describing it succeed or fail atomically.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ResearchEvent, ResearchReport, Tenant
from app.providers.redaction import redact_text

ResearchEventType = Literal[
    "user_message_received",
    "assistant_response_sent",
    "bumpa_context_built",
    "bumpa_sync_completed",
    "bumpa_sync_failed",
    "bumpa_sync_degraded",
    "hermes_profile_created",
    "hermes_call_started",
    "hermes_call_completed",
    "hermes_call_failed",
    "research_classification_completed",
    "report_generated",
    "export_generated",
    "admin_action",
    "user_opted_out",
    "user_opted_in",
]
AgentConfidence = Literal["low", "medium", "high"]

EVENT_TYPES = frozenset(
    {
        "user_message_received",
        "assistant_response_sent",
        "bumpa_context_built",
        "bumpa_sync_completed",
        "bumpa_sync_failed",
        "bumpa_sync_degraded",
        "hermes_profile_created",
        "hermes_call_started",
        "hermes_call_completed",
        "hermes_call_failed",
        "research_classification_completed",
        "report_generated",
        "export_generated",
        "admin_action",
        "user_opted_out",
        "user_opted_in",
    }
)
CHANNELS = frozenset({"web", "whatsapp", "system", "admin", "worker"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
SAFE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
LANGUAGE_RE = re.compile(r"^(?:und|[a-z]{2,3}(?:-[A-Z]{2})?)$")
METADATA_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{8,}|"
    r"sk-(?:ant-)?[a-z0-9_-]{8,}|"
    r"eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}|"
    r"EAA[a-z0-9]{12,})"
)
SENSITIVE_METADATA_PARTS = frozenset(
    {
        "address",
        "authorization",
        "content",
        "credential",
        "email",
        "key",
        "message",
        "name",
        "password",
        "payload",
        "phone",
        "raw",
        "secret",
        "token",
        "url",
    }
)
MAX_REDACTED_TEXT_CHARS = 12_000
MAX_METADATA_ITEMS = 32
MAX_METADATA_DEPTH = 3
MAX_METADATA_STRING_CHARS = 240
MAX_QUALITY_FLAGS = 20


def research_event_key(event_type: ResearchEventType, *source_parts: object) -> str:
    """Return an opaque deterministic key without retaining provider identifiers."""

    _validate_event_type(event_type)
    if not source_parts:
        raise ValueError("At least one research event source part is required")
    normalized: list[str] = []
    for part in source_parts:
        value = str(part).strip()
        if not value or len(value) > 500:
            raise ValueError("Research event source parts must be bounded non-empty values")
        normalized.append(value)
    digest = hashlib.sha256("\0".join(normalized).encode()).hexdigest()
    return f"{event_type}:{digest}"


def record_research_event(
    db: Session,
    *,
    tenant_id: str,
    event_type: ResearchEventType,
    source_parts: Sequence[object],
    channel: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    agent_message_id: str | None = None,
    redacted_text: str | None = None,
    primary_intent: str | None = None,
    business_function: str | None = None,
    ai_help_type: str | None = None,
    complexity: str | None = None,
    bumpa_data_used: str | None = None,
    classification_version: str | None = None,
    language: str | None = None,
    agent_confidence: AgentConfidence | None = None,
    response_length_chars: int | None = None,
    response_latency_ms: int | None = None,
    follow_up_detected: bool | None = None,
    business_outcome: Mapping[str, Any] | None = None,
    quality_flags: Sequence[str] = (),
) -> ResearchEvent | None:
    """Add one redacted event when the tenant currently grants research consent.

    Repeated calls with the same event type and source parts return the existing
    row. No source part is stored directly; only its SHA-256-derived event key is
    retained. The caller must commit the surrounding business transaction.
    """

    _validate_event_type(event_type)
    if channel not in CHANNELS:
        raise ValueError("Unsupported research event channel")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.research_consent_status != "granted":
        return None

    # Provider and client message identifiers are not universally guaranteed to
    # be globally unique. Scope the opaque digest to the tenant so independent
    # stores can safely reuse the same source identifier.
    idempotency_key = research_event_key(event_type, tenant_id, *source_parts)
    existing = db.scalar(
        select(ResearchEvent).where(ResearchEvent.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.tenant_id != tenant_id or existing.event_type != event_type:
            raise RuntimeError("Research event idempotency boundary mismatch")
        return existing

    safe_outcome = _sanitize_metadata(business_outcome or {})
    safe_flags = _quality_flags(quality_flags)
    event = ResearchEvent(
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_message_id=agent_message_id,
        channel=channel,
        event_type=event_type,
        raw_text_present=agent_message_id is not None,
        redacted_text=_redacted_text(redacted_text),
        primary_intent=_label(primary_intent, "primary_intent"),
        business_function=_label(business_function, "business_function"),
        ai_help_type=_label(ai_help_type, "ai_help_type"),
        complexity=_label(complexity, "complexity"),
        bumpa_data_used=_label(bumpa_data_used, "bumpa_data_used"),
        classification_version=_label(
            classification_version,
            "classification_version",
            max_length=40,
        ),
        language=_language(language),
        agent_confidence=_confidence(agent_confidence),
        response_length_chars=_nonnegative(response_length_chars, "response_length_chars"),
        response_latency_ms=_nonnegative(response_latency_ms, "response_latency_ms"),
        follow_up_detected=follow_up_detected,
        # `outcome` remains the compatibility field consumed by existing exports.
        # The named field makes the §16.3 business-outcome contract explicit.
        outcome=dict(safe_outcome),
        business_outcome=dict(safe_outcome),
        quality_flags=safe_flags,
        pii_redacted=True,
    )
    db.add(event)
    return event


def record_report_generated_events(
    db: Session,
    *,
    report: ResearchReport,
    tenant_ids: Sequence[str],
    event_count: int,
    formats: Sequence[str],
) -> list[ResearchEvent]:
    """Record report/export completion once per consented participating tenant."""

    if report.artifact_kind not in {"report", "export"}:
        raise ValueError("Unsupported research artifact kind")
    if event_count < 0:
        raise ValueError("Research report event count cannot be negative")
    safe_formats = sorted(set(formats))
    if not safe_formats or any(value not in {"csv", "jsonl", "pdf"} for value in safe_formats):
        raise ValueError("Unsupported research artifact formats")
    event_type: ResearchEventType = (
        "report_generated" if report.artifact_kind == "report" else "export_generated"
    )
    rows: list[ResearchEvent] = []
    for tenant_id in sorted(set(tenant_ids)):
        row = record_research_event(
            db,
            tenant_id=tenant_id,
            event_type=event_type,
            source_parts=(report.id, tenant_id),
            channel="system",
            user_id=report.generated_by,
            business_outcome={
                "status": "success",
                "artifact_kind": report.artifact_kind,
                "report_type": report.report_type,
                "event_count": event_count,
                "artifact_count": len(safe_formats),
                "formats": safe_formats,
            },
        )
        if row is not None:
            rows.append(row)
    return rows


def record_admin_action_event(
    db: Session,
    *,
    tenant_id: str,
    actor_user_id: str | None,
    audit_id: str,
    action: str,
    resource_type: str | None,
) -> ResearchEvent | None:
    """Write a bounded admin mutation projection without audit before/after data."""

    return record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        event_type="admin_action",
        source_parts=(audit_id,),
        channel="admin",
        business_outcome={
            "status": "completed",
            "action": _label(action, "action", max_length=80) or "admin_action",
            "resource_type": (
                _label(resource_type, "resource_type", max_length=80)
                if resource_type
                else "unspecified"
            ),
        },
    )


def record_hermes_profile_created_event(
    db: Session,
    *,
    tenant_id: str,
    actor_user_id: str | None,
    audit_id: str,
    provider: str,
) -> ResearchEvent | None:
    return record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        event_type="hermes_profile_created",
        source_parts=(audit_id,),
        channel="admin",
        business_outcome={"status": "created", "provider": provider},
    )


def _validate_event_type(event_type: str) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported research event type")


def _label(value: str | None, field: str, *, max_length: int = 80) -> str | None:
    if value is None:
        return None
    if len(value) > max_length or not SAFE_LABEL_RE.fullmatch(value):
        raise ValueError(f"Invalid research event {field}")
    return value


def _language(value: str | None) -> str | None:
    if value is None:
        return None
    if not LANGUAGE_RE.fullmatch(value):
        raise ValueError("Invalid research event language")
    return value


def _confidence(value: str | None) -> str | None:
    if value is not None and value not in CONFIDENCE_LEVELS:
        raise ValueError("Invalid research event confidence")
    return value


def _nonnegative(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or value < 0 or value > 2_147_483_647:
        raise ValueError(f"Invalid research event {field}")
    return value


def _redacted_text(value: str | None) -> str | None:
    if value is None:
        return None
    safe = _redact_secret_values(redact_text(value))
    return safe[:MAX_REDACTED_TEXT_CHARS]


def _quality_flags(values: Sequence[str]) -> list[str]:
    unique = sorted(set(values))
    if len(unique) > MAX_QUALITY_FLAGS:
        raise ValueError("Too many research event quality flags")
    for value in unique:
        if not SAFE_LABEL_RE.fullmatch(value):
            raise ValueError("Invalid research event quality flag")
    return unique


def _sanitize_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if len(value) > MAX_METADATA_ITEMS:
        raise ValueError("Research event metadata contains too many fields")
    return {
        key: _sanitize_metadata_value(key, item, depth=0)
        for key, item in sorted(value.items())
        if _metadata_key(key)
    }


def _metadata_key(value: object) -> str:
    if not isinstance(value, str) or not METADATA_KEY_RE.fullmatch(value):
        raise ValueError("Invalid research event metadata key")
    return value


def _sanitize_metadata_value(key: str, value: Any, *, depth: int) -> Any:
    if depth > MAX_METADATA_DEPTH:
        raise ValueError("Research event metadata is too deeply nested")
    normalized_key = key.lower()
    if any(part in normalized_key for part in SENSITIVE_METADATA_PARTS):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return _redact_secret_values(redact_text(value))[:MAX_METADATA_STRING_CHARS]
    if isinstance(value, Mapping):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("Research event metadata contains too many fields")
        return {
            child_key: _sanitize_metadata_value(child_key, child, depth=depth + 1)
            for child_key, child in sorted(value.items())
            if _metadata_key(child_key)
        }
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("Research event metadata contains too many items")
        return [_sanitize_metadata_value(key, child, depth=depth + 1) for child in value]
    raise ValueError("Unsupported research event metadata value")


def _redact_secret_values(value: str) -> str:
    return SECRET_VALUE_RE.sub("[REDACTED]", value)
