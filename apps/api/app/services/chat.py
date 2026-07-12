from __future__ import annotations

from time import monotonic

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaOrder,
    Conversation,
    HermesProfile,
    ResearchEvent,
    Tenant,
    UsageEvent,
    User,
)
from app.providers.local import LocalAgentRuntime, LocalClassifier
from app.providers.redaction import redact_text


def build_business_context(db: Session, tenant_id: str) -> tuple[str, object | None]:
    connection = db.scalar(select(BumpaConnection).where(BumpaConnection.tenant_id == tenant_id))
    metrics = list(
        db.scalars(
            select(BumpaMetricSnapshot)
            .where(BumpaMetricSnapshot.tenant_id == tenant_id)
            .order_by(BumpaMetricSnapshot.created_at.desc())
            .limit(10)
        ).all()
    )
    orders = list(
        db.scalars(select(BumpaOrder).where(BumpaOrder.tenant_id == tenant_id).limit(100)).all()
    )
    if not metrics:
        return "No synced Bumpa metrics are available yet. Data freshness: unavailable.", None
    unique: dict[str, BumpaMetricSnapshot] = {}
    for metric in metrics:
        unique.setdefault(metric.metric_key, metric)
    sales = unique.get("sales.total_sales")
    products = unique.get("products.products_sold")
    context = (
        f"Sales: {sales.value_decimal if sales else 'unavailable'} NGN; "
        f"products sold: {products.value_decimal if products else 'unavailable'}; "
        f"orders in local snapshot: {len(orders)}; "
        f"data freshness: {connection.last_successful_sync_at if connection else 'unavailable'}."
    )
    return context, connection.last_successful_sync_at if connection else None


def handle_chat(
    db: Session,
    *,
    tenant: Tenant,
    user: User,
    message: str,
    channel: str,
    conversation_id: str | None = None,
    external_message_id: str | None = None,
) -> tuple[Conversation, AgentMessage, AgentMessage, object | None]:
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
    context, freshness = build_business_context(db, tenant.id)
    started = monotonic()
    if profile.provider != "local":
        raise HTTPException(status_code=503, detail="Production agent runtime is not configured")
    answer = LocalAgentRuntime().respond(profile.profile_name, message, context)
    latency_ms = int((monotonic() - started) * 1000)
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
    conversation.updated_at = utcnow()
    if tenant.research_consent_status == "granted":
        classification = LocalClassifier().classify(message, "summary_metrics")
        db.add(
            ResearchEvent(
                tenant_id=tenant.id,
                user_id=user.id,
                conversation_id=conversation.id,
                agent_message_id=inbound.id,
                channel=channel,
                event_type="user_message_received",
                redacted_text=redact_text(message),
                **classification,
            )
        )
    db.add(UsageEvent(tenant_id=tenant.id, user_id=user.id, event_name="chat.response"))
    db.commit()
    db.refresh(inbound)
    db.refresh(outbound)
    return conversation, inbound, outbound, freshness
