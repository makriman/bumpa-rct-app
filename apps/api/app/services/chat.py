from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    BumpaMetricSnapshot,
    BumpaSyncRun,
    Conversation,
    HermesProfile,
    Tenant,
    UsageEvent,
    User,
)
from app.providers.hermes import (
    HermesClient,
    HermesEndpoint,
    HermesError,
    HermesResult,
)
from app.providers.local import LocalAgentRuntime, LocalClassifier
from app.providers.redaction import redact_text
from app.services.admin_operations import record_hermes_call_error
from app.services.bumpa_freshness import usable_bumpa_sync_run_predicate
from app.services.research_events import record_research_event


def build_business_context(db: Session, tenant_id: str) -> tuple[str, object | None]:
    latest_usable_run = db.scalar(
        select(BumpaSyncRun)
        .where(
            BumpaSyncRun.tenant_id == tenant_id,
            usable_bumpa_sync_run_predicate(),
        )
        .order_by(BumpaSyncRun.finished_at.desc(), BumpaSyncRun.started_at.desc())
        .limit(1)
    )
    if latest_usable_run is None:
        return "No synced Bumpa metrics are available yet. Data freshness: unavailable.", None
    metrics = list(
        db.scalars(
            select(BumpaMetricSnapshot).where(
                BumpaMetricSnapshot.tenant_id == tenant_id,
                BumpaMetricSnapshot.sync_run_id == latest_usable_run.id,
            )
        ).all()
    )
    if not metrics:
        return "No synced Bumpa metrics are available yet. Data freshness: unavailable.", None
    snapshot = {metric.metric_key: metric for metric in metrics}
    sales = snapshot.get("sales.total_sales")
    gross_profit = snapshot.get("sales.gross_profit")
    net_profit = snapshot.get("sales.net_profit")
    products = snapshot.get("products.products_sold")
    freshness = latest_usable_run.finished_at
    context = (
        f"Total sales: {_format_metric(sales, money=True)}; "
        f"gross profit: {_format_metric(gross_profit, money=True)}; "
        f"net profit: {_format_metric(net_profit, money=True)}; "
        f"products sold: {_format_metric(products)}; "
        f"orders in current snapshot: {_format_count(latest_usable_run.orders_count)}; "
        f"data refreshed: {_format_timestamp(freshness)}."
    )
    return context, freshness


def data_freshness_at_message(
    db: Session,
    *,
    tenant_id: str,
    message_created_at: datetime,
) -> datetime | None:
    """Reconstruct the freshness value returned with an idempotent chat response."""

    return db.scalar(
        select(BumpaSyncRun.finished_at)
        .where(
            BumpaSyncRun.tenant_id == tenant_id,
            usable_bumpa_sync_run_predicate(),
            BumpaSyncRun.finished_at <= message_created_at,
        )
        .order_by(BumpaSyncRun.finished_at.desc(), BumpaSyncRun.started_at.desc())
        .limit(1)
    )


def _format_metric(metric: BumpaMetricSnapshot | None, *, money: bool = False) -> str:
    if metric is None or metric.availability != "available":
        return "unavailable"
    return _format_decimal(metric.value_decimal, money=money)


def _format_count(value: int | None) -> str:
    return "unavailable" if value is None else str(value)


def _format_decimal(value: Decimal | None, *, money: bool = False) -> str:
    if value is None:
        return "unavailable"
    if money:
        return f"NGN {value:,.2f}"
    return format(value.normalize(), "f")


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unavailable"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")


def handle_chat(
    db: Session,
    *,
    tenant: Tenant,
    user: User,
    message: str,
    channel: str,
    conversation_id: str | None = None,
    external_message_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[Conversation, AgentMessage, AgentMessage, object | None]:
    effective_settings = settings or get_settings()
    profile = db.scalar(
        select(HermesProfile).where(
            HermesProfile.tenant_id == tenant.id,
            HermesProfile.status == "active",
        )
    )
    if not profile:
        raise HTTPException(status_code=409, detail="Agent profile is not provisioned")
    conversation = db.get(Conversation, conversation_id) if conversation_id else None
    if conversation and (conversation.tenant_id != tenant.id or conversation.user_id != user.id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    follow_up_detected = conversation is not None
    persisted_conversation_id = conversation.id if conversation is not None else None
    if not conversation:
        conversation = Conversation(
            tenant_id=tenant.id,
            user_id=user.id,
            channel=channel,
            title=message[:80],
        )
        db.add(conversation)
        db.flush()
    inbound = AgentMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        hermes_profile_id=profile.id,
        conversation_id=conversation.id,
        channel=channel,
        direction="inbound",
        content=message,
        redacted_content=redact_text(message),
        external_message_id=external_message_id,
    )
    db.add(inbound)
    db.flush()
    event_source = external_message_id or inbound.id
    context, freshness = build_business_context(db, tenant.id)
    bumpa_data_used = "summary_metrics" if freshness is not None else "none"
    classification = LocalClassifier().classify(message, bumpa_data_used)
    started = monotonic()
    usage_metadata: dict[str, object] = {"provider": profile.provider}
    if profile.provider == "local":
        answer = LocalAgentRuntime().respond(profile.profile_name, message, context)
        latency_ms = int((monotonic() - started) * 1000)
    elif profile.provider == "hermes" and effective_settings.agent_backend == "hermes":
        try:
            api_key = FieldCipher(effective_settings.field_encryption_key).decrypt(
                profile.encrypted_api_key
            )
            result: HermesResult = HermesClient(effective_settings).respond(
                HermesEndpoint(
                    profile_name=profile.profile_name,
                    api_url=profile.api_internal_url,
                    api_key=api_key,
                ),
                message=redact_text(message),
                business_context=context,
            )
        except HermesError as exc:
            _persist_failed_hermes_call(
                db,
                tenant_id=tenant.id,
                user_id=user.id,
                profile_id=profile.id,
                conversation_id=persisted_conversation_id,
                channel=channel,
                event_source=event_source,
                message=message,
                classification=classification,
                follow_up_detected=follow_up_detected,
                bumpa_context_available=freshness is not None,
                error_code=exc.code,
                retryable=exc.retryable,
                system_error=True,
            )
            raise HTTPException(
                status_code=503,
                detail="Agent service is temporarily unavailable",
            ) from exc
        except ValueError as exc:
            _persist_failed_hermes_call(
                db,
                tenant_id=tenant.id,
                user_id=user.id,
                profile_id=profile.id,
                conversation_id=persisted_conversation_id,
                channel=channel,
                event_source=event_source,
                message=message,
                classification=classification,
                follow_up_detected=follow_up_detected,
                bumpa_context_available=freshness is not None,
                error_code="profile_key_invalid",
                retryable=False,
                system_error=False,
            )
            raise HTTPException(
                status_code=503,
                detail="Agent service is temporarily unavailable",
            ) from exc
        answer = result.content
        latency_ms = result.latency_ms
        usage_metadata.update(
            {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
            }
        )
    else:
        db.rollback()
        raise HTTPException(status_code=503, detail="Production agent runtime is not configured")
    outbound = AgentMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        hermes_profile_id=profile.id,
        conversation_id=conversation.id,
        channel=channel,
        direction="outbound",
        content=answer,
        redacted_content=redact_text(answer),
        latency_ms=latency_ms,
    )
    db.add(outbound)
    # Materialize the outbound identifier before research instrumentation so
    # the response event can prove where permissioned raw text is retained.
    db.flush()
    conversation.updated_at = utcnow()
    _record_successful_chat_events(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        profile=profile,
        conversation_id=conversation.id,
        inbound=inbound,
        outbound=outbound,
        event_source=event_source,
        channel=channel,
        message=message,
        answer=answer,
        classification=classification,
        follow_up_detected=follow_up_detected,
        bumpa_context_available=freshness is not None,
        latency_ms=latency_ms,
    )
    db.add(
        UsageEvent(
            tenant_id=tenant.id,
            user_id=user.id,
            event_name="chat.response",
            units=(
                Decimal(str(usage_metadata["total_tokens"]))
                if "total_tokens" in usage_metadata
                else None
            ),
            event_metadata={**usage_metadata, "latency_ms": latency_ms},
        )
    )
    db.commit()
    db.refresh(inbound)
    db.refresh(outbound)
    return conversation, inbound, outbound, freshness


def _record_successful_chat_events(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    profile: HermesProfile,
    conversation_id: str,
    inbound: AgentMessage,
    outbound: AgentMessage,
    event_source: str,
    channel: str,
    message: str,
    answer: str,
    classification: dict[str, str],
    follow_up_detected: bool,
    bumpa_context_available: bool,
    latency_ms: int,
) -> None:
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_message_id=inbound.id,
        event_type="user_message_received",
        source_parts=(event_source,),
        channel=channel,
        redacted_text=message,
        language="und",
        agent_confidence="medium",
        follow_up_detected=follow_up_detected,
        primary_intent=classification["primary_intent"],
        business_function=classification["business_function"],
        ai_help_type=classification["ai_help_type"],
        complexity=classification["complexity"],
        bumpa_data_used=classification["bumpa_data_used"],
        classification_version=classification["classification_version"],
    )
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event_type="bumpa_context_built",
        source_parts=(event_source,),
        channel=channel,
        bumpa_data_used=classification["bumpa_data_used"],
        business_outcome={
            "status": "available" if bumpa_context_available else "unavailable",
        },
        quality_flags=() if bumpa_context_available else ("bumpa_data_unavailable",),
    )
    if profile.provider == "hermes":
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_started",
            source_parts=(event_source,),
            channel=channel,
            business_outcome={"status": "started", "provider": "hermes"},
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_completed",
            source_parts=(event_source,),
            channel=channel,
            response_latency_ms=latency_ms,
            business_outcome={"status": "completed", "provider": "hermes"},
        )
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event_type="research_classification_completed",
        source_parts=(event_source,),
        channel=channel,
        agent_confidence="medium",
        follow_up_detected=follow_up_detected,
        business_outcome={"status": "completed"},
        primary_intent=classification["primary_intent"],
        business_function=classification["business_function"],
        ai_help_type=classification["ai_help_type"],
        complexity=classification["complexity"],
        bumpa_data_used=classification["bumpa_data_used"],
        classification_version=classification["classification_version"],
    )
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_message_id=outbound.id,
        event_type="assistant_response_sent",
        source_parts=(event_source,),
        channel=channel,
        redacted_text=answer,
        language="und",
        response_length_chars=len(answer),
        response_latency_ms=latency_ms,
        follow_up_detected=follow_up_detected,
        business_outcome={"status": "sent", "provider": profile.provider},
        primary_intent=classification["primary_intent"],
        business_function=classification["business_function"],
        ai_help_type=classification["ai_help_type"],
        complexity=classification["complexity"],
        bumpa_data_used=classification["bumpa_data_used"],
        classification_version=classification["classification_version"],
    )


def _persist_failed_hermes_call(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str,
    conversation_id: str | None,
    channel: str,
    event_source: str,
    message: str,
    classification: dict[str, str],
    follow_up_detected: bool,
    bumpa_context_available: bool,
    error_code: str,
    retryable: bool,
    system_error: bool,
) -> None:
    """Persist bounded failure evidence after abandoning partial chat state."""

    db.rollback()
    try:
        if system_error:
            record_hermes_call_error(
                db,
                tenant_id=tenant_id,
                profile_id=profile_id,
                category=error_code,
            )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="user_message_received",
            source_parts=(event_source,),
            channel=channel,
            redacted_text=message,
            language="und",
            agent_confidence="medium",
            follow_up_detected=follow_up_detected,
            quality_flags=("assistant_response_failed",),
            primary_intent=classification["primary_intent"],
            business_function=classification["business_function"],
            ai_help_type=classification["ai_help_type"],
            complexity=classification["complexity"],
            bumpa_data_used=classification["bumpa_data_used"],
            classification_version=classification["classification_version"],
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="bumpa_context_built",
            source_parts=(event_source,),
            channel=channel,
            bumpa_data_used=classification["bumpa_data_used"],
            business_outcome={
                "status": "available" if bumpa_context_available else "unavailable",
            },
            quality_flags=() if bumpa_context_available else ("bumpa_data_unavailable",),
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_started",
            source_parts=(event_source,),
            channel=channel,
            business_outcome={"status": "started", "provider": "hermes"},
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_failed",
            source_parts=(event_source,),
            channel=channel,
            business_outcome={
                "status": "failed",
                "provider": "hermes",
                "error_code": error_code,
                "retryable": retryable,
            },
            quality_flags=(error_code,),
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="research_classification_completed",
            source_parts=(event_source,),
            channel=channel,
            agent_confidence="medium",
            follow_up_detected=follow_up_detected,
            business_outcome={"status": "completed"},
            primary_intent=classification["primary_intent"],
            business_function=classification["business_function"],
            ai_help_type=classification["ai_help_type"],
            complexity=classification["complexity"],
            bumpa_data_used=classification["bumpa_data_used"],
            classification_version=classification["classification_version"],
        )
        db.commit()
    except Exception:
        # Instrumentation must not replace the sanitized provider response with a
        # secondary database error. The original Hermes failure remains primary.
        db.rollback()
