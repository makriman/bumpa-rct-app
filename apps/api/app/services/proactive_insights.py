"""Explicit optional business subscriptions and fenced, exact-period delivery.

Research consent never enrolls a recipient. A saved subscription names one
account-owned approved alias; new aliases do not inherit it. No native/rolling
metric snapshot is presented as a daily or weekly order total.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    BumpaConnection,
    BumpaOrder,
    BumpaSyncRun,
    Conversation,
    HermesProfile,
    PhoneIdentity,
    ProactiveSubscription,
    Tenant,
    TenantMembership,
    User,
    WhatsappMessage,
)
from app.jobs.runtime import PermanentJobError, assert_current_job_lease
from app.providers.meta import MetaProviderError, MetaWhatsAppClient
from app.providers.redaction import redact_text
from app.services.business_tools import ResolvedPeriod, resolve_period, tenant_store_context
from app.services.conversation_scope import receiver_scope

InsightCadence = Literal["daily", "weekly"]
MAX_TEMPLATE_SUMMARY_CHARS = 900


class SubscriptionDenied(RuntimeError):
    """The actor, recipient, or requested settings are not eligible."""


class SubscriptionConflict(SubscriptionDenied):
    """The settings changed since the member reviewed them."""


def _actor(db: Session, tenant_id: str, user_id: str, *, lock: bool = False) -> None:
    def row(model: Any, conditions: Any) -> Any:
        query = select(model).where(*conditions).execution_options(populate_existing=True)
        return db.scalar(query.with_for_update() if lock else query)

    tenant = row(Tenant, [Tenant.id == tenant_id])
    user = row(User, [User.id == user_id])
    membership = row(
        TenantMembership,
        [
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
            TenantMembership.role.in_(("owner", "admin")),
        ],
    )
    if (
        not tenant
        or tenant.status != "active"
        or not user
        or user.status != "active"
        or not membership
    ):
        raise SubscriptionDenied(
            "An active business owner or admin can manage only their own subscription"
        )


def _recipient(
    db: Session, tenant_id: str, user_id: str, identity_id: str, *, lock: bool = False
) -> PhoneIdentity:
    query = (
        select(PhoneIdentity)
        .where(
            PhoneIdentity.id == identity_id,
            PhoneIdentity.tenant_id == tenant_id,
            PhoneIdentity.user_id == user_id,
            PhoneIdentity.status == "approved",
            PhoneIdentity.opt_out.is_(False),
        )
        .execution_options(populate_existing=True)
    )
    identity = db.scalar(query.with_for_update() if lock else query)
    if identity is None:
        raise SubscriptionDenied("Choose your own approved WhatsApp number that has not opted out")
    return identity


def subscription_settings(
    db: Session, *, tenant_id: str, user_id: str, settings: Settings
) -> dict[str, Any]:
    _actor(db, tenant_id, user_id)
    _tenant, store_timezone, _currency = tenant_store_context(db, tenant_id)
    identities = list(
        db.scalars(
            select(PhoneIdentity)
            .where(
                PhoneIdentity.tenant_id == tenant_id,
                PhoneIdentity.user_id == user_id,
                PhoneIdentity.status == "approved",
                PhoneIdentity.opt_out.is_(False),
            )
            .order_by(PhoneIdentity.id)
        )
    )
    subscriptions = list(
        db.scalars(
            select(ProactiveSubscription)
            .where(
                ProactiveSubscription.tenant_id == tenant_id,
                ProactiveSubscription.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
    )
    by_cadence = {row.cadence: row for row in subscriptions}
    choices = [
        {"id": row.id, "phone_e164": row.phone_e164, "label": row.label} for row in identities
    ]
    views = []
    for cadence in ("daily", "weekly"):
        row = by_cadence.get(cadence)
        eligible_recipient = next(
            (item for item in identities if row and item.id == row.phone_identity_id), None
        )
        selected = (
            db.scalar(
                select(PhoneIdentity).where(
                    PhoneIdentity.id == row.phone_identity_id,
                    PhoneIdentity.tenant_id == tenant_id,
                    PhoneIdentity.user_id == user_id,
                )
            )
            if row
            else None
        )
        delivery_available = bool(
            settings.proactive_insights_enabled
            and _cadence_enabled(cadence, settings)
            and settings.whatsapp_backend == "meta"
            and settings.meta_primary_sender_enabled
            and settings.meta_waba_id
            and settings.meta_phone_number_id
        )
        views.append(
            {
                "id": row.id if row else None,
                "cadence": cadence,
                "status": row.status if row else "off",
                "revision": row.revision if row else 0,
                "phone_identity_id": row.phone_identity_id if row else None,
                "recipient_preview": selected.phone_e164 if selected else None,
                "timezone": row.timezone if row else store_timezone,
                "local_hour": row.local_hour if row else 8,
                "weekday": row.weekday if row else 0,
                "effective": bool(
                    row and row.status == "active" and eligible_recipient and delivery_available
                ),
                "delivery_available": delivery_available,
            }
        )
    return {
        "subscriptions": views,
        "recipients": choices,
        "store_timezone": store_timezone,
        "notice": "Optional business messages. Research participation is separate. Pause here or reply STOP on the selected number. START alone does not create a subscription.",
    }


def update_subscription(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    cadence: InsightCadence,
    status: Literal["active", "paused"],
    expected_revision: int,
    phone_identity_id: str | None,
    timezone: str | None,
    local_hour: int,
    weekday: int,
    settings: Settings,
) -> dict[str, Any]:
    if cadence not in {"daily", "weekly"} or status not in {"active", "paused"}:
        raise SubscriptionDenied(
            "Choose a daily or weekly subscription and explicit active or paused status"
        )
    if type(expected_revision) is not int or expected_revision < 0:
        raise SubscriptionDenied("An exact subscription revision is required")
    _actor(db, tenant_id, user_id, lock=True)
    row = db.scalar(
        select(ProactiveSubscription)
        .where(
            ProactiveSubscription.tenant_id == tenant_id,
            ProactiveSubscription.user_id == user_id,
            ProactiveSubscription.cadence == cadence,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (row.revision if row else 0) != expected_revision:
        raise SubscriptionConflict("These settings changed. Reload and review before saving")
    if status == "active":
        if not phone_identity_id or not timezone:
            raise SubscriptionDenied(
                "Choose an exact recipient and schedule timezone before subscribing"
            )
        _recipient(db, tenant_id, user_id, phone_identity_id, lock=True)
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise SubscriptionDenied("Choose a valid IANA timezone") from exc
        if (
            type(local_hour) is not int
            or not 0 <= local_hour <= 23
            or type(weekday) is not int
            or not 0 <= weekday <= 6
        ):
            raise SubscriptionDenied("Choose an hour from 0 to 23 and weekday from 0 to 6")
        if row is None:
            row = ProactiveSubscription(
                tenant_id=tenant_id, user_id=user_id, cadence=cadence, revision=1
            )
            db.add(row)
        else:
            row.revision += 1
        row.phone_identity_id = phone_identity_id
        row.timezone, row.local_hour, row.weekday = timezone, local_hour, weekday
        row.status = "active"
        row.subscribed_at = utcnow()
        row.paused_at = None
    elif row is not None:
        row.status = "paused"
        row.paused_at = utcnow()
        row.revision += 1
    db.flush()
    return subscription_settings(db, tenant_id=tenant_id, user_id=user_id, settings=settings)


def schedule_subscription_slot(subscription: ProactiveSubscription, now: datetime) -> str | None:
    if subscription.status != "active" or subscription.cadence not in {"daily", "weekly"}:
        return None
    try:
        local = _as_utc(now).astimezone(ZoneInfo(subscription.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return None
    if subscription.cadence == "daily":
        return local.date().isoformat() if local.hour >= subscription.local_hour else None
    if local.weekday() < subscription.weekday or (
        local.weekday() == subscription.weekday and local.hour < subscription.local_hour
    ):
        return None
    year, week, _ = local.isocalendar()
    return f"{year:04d}-W{week:02d}"


def insight_period(
    db: Session, tenant_id: str, subscription: ProactiveSubscription, slot: str
) -> ResolvedPeriod:
    if not _valid_slot(subscription.cadence, slot):
        raise PermanentJobError("Proactive insight slot is invalid")
    if subscription.cadence == "daily":
        scheduled_day = date.fromisoformat(slot)
    else:
        year, week = slot.split("-W")
        scheduled_day = date.fromisocalendar(int(year), int(week), subscription.weekday + 1)
    scheduled = datetime.combine(
        scheduled_day, time(subscription.local_hour), ZoneInfo(subscription.timezone)
    )
    return resolve_period(
        db,
        tenant_id=tenant_id,
        period="yesterday" if subscription.cadence == "daily" else "last_week",
        now=scheduled,
    )


@dataclass(frozen=True)
class InsightSnapshot:
    summary: str
    connection_id: str
    boundary_revision: int
    generation: int
    run_id: str
    period: dict[str, str]


def _snapshot(
    db: Session, tenant_id: str, period: ResolvedPeriod, now: datetime, settings: Settings
) -> InsightSnapshot | None:
    connection = db.scalar(
        select(BumpaConnection).where(
            BumpaConnection.tenant_id == tenant_id,
            BumpaConnection.status == "active",
        )
    )
    if connection is None or connection.published_sync_generation <= 0:
        return None
    run = db.scalar(
        select(BumpaSyncRun)
        .where(
            BumpaSyncRun.tenant_id == tenant_id,
            BumpaSyncRun.bumpa_connection_id == connection.id,
            BumpaSyncRun.boundary_revision == connection.boundary_revision,
            BumpaSyncRun.sync_generation == connection.published_sync_generation,
            BumpaSyncRun.status.in_(("success", "partial")),
            BumpaSyncRun.completion_quality.in_(("complete", "accepted_partial")),
            BumpaSyncRun.orders_availability == "available",
            BumpaSyncRun.error.is_(None),
            BumpaSyncRun.requested_from <= period.date_from,
            BumpaSyncRun.requested_to >= period.date_to,
            BumpaSyncRun.finished_at.is_not(None),
        )
        .order_by(BumpaSyncRun.finished_at.desc())
        .limit(1)
    )
    if (
        run is None
        or run.finished_at is None
        or not now - timedelta(hours=settings.insight_max_freshness_hours)
        <= _as_utc(run.finished_at)
        <= now
    ):
        return None
    publication = run.dataset_results.get("_bumpa_order_publication")
    if (
        not isinstance(publication, dict)
        or type(publication.get("version")) is not int
        or publication.get("version") != 1
        or publication.get("state") != "complete"
        or run.orders_count is None
    ):
        return None
    current_orders = list(
        db.scalars(
            select(BumpaOrder).where(
                BumpaOrder.tenant_id == tenant_id,
                BumpaOrder.raw_payload["_bumpa_publication"]["sync_run_id"].as_string() == run.id,
                BumpaOrder.raw_payload["_bumpa_publication"]["state"].as_string() == "active",
            )
        )
    )
    if len(current_orders) != run.orders_count or any(
        row.order_date is None for row in current_orders
    ):
        return None
    orders = [
        row
        for row in current_orders
        if row.order_date is not None
        and period.starts_at_utc <= _as_utc(row.order_date) < period.ends_at_utc_exclusive
    ]
    if any(row.currency_code != connection.store_currency for row in orders):
        return None
    total = sum((row.total_amount for row in orders if row.total_amount is not None), Decimal(0))
    missing = sum(row.total_amount is None for row in orders)
    summary = (
        f"Bumpa order summary {period.date_from.isoformat()} to {period.date_to.isoformat()} "
        f"({period.timezone}). Orders: {len(orders):,}. Available order totals: "
        f"{_format_metric(total, connection.store_currency)}. "
        f"Orders missing amounts: {missing}. Coverage: full requested period, complete order sync; "
        "cancelled/refunded orders included. Profit unavailable. "
        f"Data refreshed {_as_utc(run.finished_at).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Optional business message; reply STOP to opt out."
    )
    if len(summary) > MAX_TEMPLATE_SUMMARY_CHARS:
        raise PermanentJobError("Insight summary exceeds the approved template boundary")
    return InsightSnapshot(
        summary,
        connection.id,
        connection.boundary_revision,
        connection.published_sync_generation,
        run.id,
        period.payload(),
    )


def _eligible(
    db: Session, tenant_id: str, subscription_id: str, subscription_revision: int, cadence: str
) -> tuple[ProactiveSubscription, PhoneIdentity] | None:
    # Consistent lock order with settings writes. Hold through the provider call:
    # a concurrent pause/STOP is serialized against the start of this send.
    candidate = db.scalar(
        select(ProactiveSubscription)
        .where(
            ProactiveSubscription.id == subscription_id,
            ProactiveSubscription.tenant_id == tenant_id,
        )
        .execution_options(populate_existing=True)
    )
    if candidate is None:
        return None
    try:
        _actor(db, tenant_id, candidate.user_id, lock=True)
        subscription = db.scalar(
            select(ProactiveSubscription)
            .where(
                ProactiveSubscription.id == subscription_id,
                ProactiveSubscription.tenant_id == tenant_id,
                ProactiveSubscription.status == "active",
                ProactiveSubscription.cadence == cadence,
                ProactiveSubscription.revision == subscription_revision,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if subscription is None:
            return None
        identity = _recipient(
            db, tenant_id, subscription.user_id, subscription.phone_identity_id, lock=True
        )
    except SubscriptionDenied:
        return None
    return subscription, identity


def deliver_proactive_insight(
    db: Session,
    *,
    tenant_id: str,
    cadence: InsightCadence,
    slot: str,
    settings: Settings,
    subscription_id: str | None = None,
    subscription_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Send one explicit subscription intent; unknown acceptance is never retried.

    Missing legacy subscription claims fail closed rather than re-enrolling any
    pre-existing tenant/alias. Delivery keys survive subscription revisions.
    """
    result: dict[str, object] = {"status": "ineligible", "cadence": cadence, "sent": 0}
    if cadence not in {"daily", "weekly"} or not _valid_slot(cadence, slot):
        raise PermanentJobError("Proactive insight schedule payload is invalid")
    if not settings.proactive_insights_enabled or not _cadence_enabled(cadence, settings):
        return {**result, "status": "disabled"}
    if settings.whatsapp_backend != "meta":
        raise PermanentJobError("Proactive insights require Meta WhatsApp")
    if (
        not settings.meta_primary_sender_enabled
        or not settings.meta_waba_id
        or not settings.meta_phone_number_id
    ):
        return {**result, "status": "no_sender"}
    sender = (settings.meta_waba_id, settings.meta_phone_number_id)
    sender_scope = receiver_scope(settings.app_env, sender)
    if not subscription_id or type(subscription_revision) is not int or subscription_revision < 1:
        return {**result, "status": "no_subscription"}
    eligible = _eligible(db, tenant_id, subscription_id, subscription_revision, cadence)
    if eligible is None:
        return result
    subscription, identity = eligible
    effective_now = _as_utc(now or utcnow())
    if schedule_subscription_slot(subscription, effective_now) != slot:
        return {**result, "status": "stale_slot"}
    period = insight_period(db, tenant_id, subscription, slot)
    snapshot = _snapshot(db, tenant_id, period, effective_now, settings)
    if snapshot is None:
        return {**result, "status": "no_fresh_data"}
    key = _delivery_key(tenant_id, subscription.user_id, cadence, slot, sender_scope)
    outbound = db.scalar(select(WhatsappMessage).where(WhatsappMessage.idempotency_key == key))
    if (
        outbound is not None
        and outbound.status in {"sent", "delivered", "read"}
        and outbound.meta_message_id
    ):
        return {**result, "status": "already_sent", "already_sent": 1}
    if outbound is not None and outbound.status in {"sending", "ambiguous", "rejected"}:
        raise PermanentJobError("Proactive WhatsApp delivery requires reconciliation")
    envelope = {
        "purpose": "proactive_insight",
        "cadence": cadence,
        "slot": slot,
        "subscription_id": subscription_id,
        "subscription_revision": subscription_revision,
        "phone_identity_id": identity.id,
        "period": snapshot.period,
        "summary": snapshot.summary,
        "source_run_id": snapshot.run_id,
        "boundary_revision": snapshot.boundary_revision,
        "template": _template_name(cadence, settings),
        "receiver_scope": sender_scope,
        "sender_waba_id": sender[0],
        "sender_phone_number_id": sender[1],
        "environment_lane": settings.app_env,
    }
    if outbound is None:
        outbound = WhatsappMessage(
            tenant_id=tenant_id,
            user_id=subscription.user_id,
            idempotency_key=key,
            phone_e164=identity.phone_e164,
            receiver_scope=sender_scope,
            direction="outbound",
            message_type="template",
            text_body=snapshot.summary,
            payload=envelope,
            status="prepared",
        )
        db.add(outbound)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise PermanentJobError("Concurrent proactive intent requires reconciliation") from exc
    elif (
        outbound.payload != envelope
        or outbound.phone_e164 != identity.phone_e164
        or outbound.receiver_scope != sender_scope
    ):
        raise PermanentJobError(
            "The immutable proactive intent changed; reconciliation is required"
        )
    claimed = db.execute(
        update(WhatsappMessage)
        .where(
            WhatsappMessage.id == outbound.id,
            WhatsappMessage.status.in_(("prepared", "failed")),
        )
        .values(status="sending")
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise PermanentJobError("Proactive WhatsApp delivery is already claimed")
    db.commit()
    provider = MetaWhatsAppClient.from_settings(settings)
    eligible = _eligible(db, tenant_id, subscription_id, subscription_revision, cadence)
    boundary = db.scalar(
        select(BumpaConnection)
        .where(
            BumpaConnection.id == snapshot.connection_id,
            BumpaConnection.tenant_id == tenant_id,
            BumpaConnection.status == "active",
            BumpaConnection.boundary_revision == snapshot.boundary_revision,
            BumpaConnection.published_sync_generation == snapshot.generation,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        eligible is None
        or boundary is None
        or eligible[1].phone_e164 != outbound.phone_e164
        or eligible[1].id != outbound.payload.get("phone_identity_id")
    ):
        outbound.status = "cancelled"
        db.commit()
        return result
    try:
        assert_current_job_lease(db)
        message_id = provider.send_template(
            eligible[1].phone_e164,
            template_name=_template_name(cadence, settings),
            language_code=settings.meta_template_language_code,
            components=[
                {"type": "body", "parameters": [{"type": "text", "text": snapshot.summary}]}
            ],
        )
    except ValueError as exc:
        outbound.status = "rejected"
        db.commit()
        raise PermanentJobError("Proactive WhatsApp template is invalid") from exc
    except MetaProviderError as exc:
        ambiguous = exc.category in {"timeout", "transport"} or (
            exc.category == "provider" and exc.http_status is not None and exc.http_status >= 500
        )
        outbound.status = "ambiguous" if ambiguous else ("failed" if exc.retryable else "rejected")
        db.commit()
        if outbound.status in {"ambiguous", "rejected"}:
            raise PermanentJobError("Proactive WhatsApp delivery cannot be retried safely") from exc
        raise
    outbound.meta_message_id, outbound.status, outbound.status_rank = message_id, "sent", 3
    _record_scheduled_message(
        db,
        subscription=eligible[0],
        outbound=outbound,
        snapshot=snapshot,
        sender=sender,
        settings=settings,
    )
    db.commit()
    return {**result, "status": "sent", "sent": 1, "period": snapshot.period}


def _record_scheduled_message(
    db: Session,
    *,
    subscription: ProactiveSubscription,
    outbound: WhatsappMessage,
    snapshot: InsightSnapshot,
    sender: tuple[str, str],
    settings: Settings,
) -> AgentMessage:
    """Record a confirmed scheduled assistant event, never a synthetic user turn.

    The caller keeps this in the same transaction as the successful transport
    receipt. Reusing the immutable transport-row ID gives the canonical event
    an independent primary-key fence as well as the scoped Meta-ID constraint.
    """
    if not outbound.meta_message_id or not outbound.idempotency_key:
        raise PermanentJobError("Scheduled output requires its exact transport receipt")
    scope = receiver_scope(settings.app_env, sender)
    existing = db.get(AgentMessage, outbound.id)
    if existing is not None:
        if (
            existing.tenant_id != subscription.tenant_id
            or existing.user_id != subscription.user_id
            or existing.event_scope != scope
            or existing.external_message_id != outbound.meta_message_id
            or existing.media_metadata.get("transport_key") != outbound.idempotency_key
            or existing.content != snapshot.summary
        ):
            raise PermanentJobError("Scheduled canonical receipt requires reconciliation")
        return existing
    conversation = db.scalar(
        select(Conversation)
        .where(
            Conversation.tenant_id == subscription.tenant_id,
            Conversation.user_id == subscription.user_id,
            Conversation.channel == "whatsapp",
            Conversation.context_generation == 1,
            Conversation.status == "open",
            Conversation.environment_lane == settings.app_env,
            Conversation.receiver_waba_id == sender[0],
            Conversation.receiver_phone_id == sender[1],
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .with_for_update()
        .limit(1)
    )
    if conversation is None:
        conversation = Conversation(
            tenant_id=subscription.tenant_id,
            user_id=subscription.user_id,
            channel="whatsapp",
            context_generation=1,
            status="open",
            environment_lane=settings.app_env,
            receiver_waba_id=sender[0],
            receiver_phone_id=sender[1],
            title=f"Optional {subscription.cadence} business summary",
        )
        db.add(conversation)
        db.flush()
    conversation.updated_at = utcnow()
    profile = db.scalar(
        select(HermesProfile)
        .where(
            HermesProfile.tenant_id == subscription.tenant_id,
            HermesProfile.status == "active",
        )
        .order_by(HermesProfile.id)
        .limit(1)
    )
    event = AgentMessage(
        id=outbound.id,
        tenant_id=subscription.tenant_id,
        user_id=subscription.user_id,
        hermes_profile_id=profile.id if profile else None,
        conversation_id=conversation.id,
        channel="whatsapp",
        direction="outbound",
        initiating_message_id=None,
        event_scope=scope,
        external_message_id=outbound.meta_message_id,
        content=snapshot.summary,
        content_parts=[{"type": "text", "text": snapshot.summary}],
        redacted_content=redact_text(snapshot.summary),
        media_metadata={
            "event_kind": "scheduled",
            "delivery_status": "sent",
            "transport_key": outbound.idempotency_key,
            "whatsapp_message_id": outbound.id,
            "subscription_id": subscription.id,
            "subscription_revision": subscription.revision,
            "cadence": subscription.cadence,
            "slot": outbound.payload["slot"],
            "period": snapshot.period,
            "source_kind": "canonical_orders",
            "source_run_id": snapshot.run_id,
            "boundary_revision": snapshot.boundary_revision,
            "sync_generation": snapshot.generation,
            "order_publication_version": 1,
        },
    )
    db.add(event)
    db.flush()
    return event


def _format_metric(value: Decimal, currency: str | None) -> str:
    rendered = f"{value.quantize(Decimal('0.01')):,.2f}"
    return f"{currency} {rendered}" if currency else rendered


def _cadence_enabled(cadence: str, settings: Settings) -> bool:
    return (
        settings.daily_insights_enabled if cadence == "daily" else settings.weekly_insights_enabled
    )


def _template_name(cadence: str, settings: Settings) -> str:
    return (
        settings.meta_daily_insight_template_name
        if cadence == "daily"
        else settings.meta_weekly_insight_template_name
    )


def _delivery_key(tenant_id: str, user_id: str, cadence: str, slot: str, sender_scope: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}\0{user_id}\0{cadence}\0{slot}\0{sender_scope}".encode()
    ).hexdigest()
    return f"insight:{digest}"


def _valid_slot(cadence: str, slot: str) -> bool:
    try:
        if cadence == "daily":
            return date.fromisoformat(slot).isoformat() == slot
        year, week = slot.split("-W", 1)
        return (
            len(year) == 4
            and len(week) == 2
            and date.fromisocalendar(int(year), int(week), 1).isocalendar()[:2]
            == (int(year), int(week))
        )
    except (ValueError, TypeError, AttributeError):
        return False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
