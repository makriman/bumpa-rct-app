from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
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
    ResearchEvent,
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


def build_business_context(db: Session, tenant_id: str) -> tuple[str, object | None]:
    latest_usable_run = db.scalar(
        select(BumpaSyncRun)
        .where(
            BumpaSyncRun.tenant_id == tenant_id,
            BumpaSyncRun.error.is_(None),
            BumpaSyncRun.finished_at.is_not(None),
            or_(
                and_(
                    BumpaSyncRun.status == "success",
                    BumpaSyncRun.completion_quality == "complete",
                    BumpaSyncRun.partial_reason.is_(None),
                    BumpaSyncRun.orders_availability == "available",
                ),
                and_(
                    BumpaSyncRun.status == "partial",
                    BumpaSyncRun.completion_quality == "accepted_partial",
                    BumpaSyncRun.partial_reason == "profit_not_calculable",
                    BumpaSyncRun.orders_availability == "available",
                    BumpaSyncRun.orders_count.is_not(None),
                ),
            ),
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
        except (HermesError, ValueError) as exc:
            db.rollback()
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
