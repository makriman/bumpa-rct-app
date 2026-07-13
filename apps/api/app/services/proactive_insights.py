"""Consent-safe, idempotent proactive SME insight delivery.

The scheduler only creates intent. This worker-side service re-checks every
recipient and the authoritative Bumpa freshness evidence immediately before a
Meta template call, so a withdrawal, opt-out, suspension, or stale sync cannot
race a queued send.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import (
    BumpaMetricSnapshot,
    BumpaSyncRun,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
    WhatsappMessage,
)
from app.jobs.runtime import PermanentJobError
from app.providers.meta import MetaProviderError, MetaWhatsAppClient
from app.services.bumpa_freshness import usable_bumpa_sync_run_predicate

InsightCadence = Literal["daily", "weekly"]
MAX_TEMPLATE_SUMMARY_CHARS = 900


def deliver_proactive_insight(
    db: Session,
    *,
    tenant_id: str,
    cadence: InsightCadence,
    slot: str,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, object]:
    """Deliver one scheduled slot to currently eligible tenant owners.

    A successful message row is the durable send fence. Ambiguous provider
    outcomes are never retried automatically because doing so could duplicate a
    proactive message outside Meta's reconciliation surface.
    """

    if cadence not in {"daily", "weekly"} or not _valid_slot(cadence, slot):
        raise PermanentJobError("Proactive insight schedule payload is invalid")
    if not settings.proactive_insights_enabled or not _cadence_enabled(cadence, settings):
        return {"status": "disabled", "cadence": cadence, "sent": 0}
    if settings.whatsapp_backend != "meta":
        raise PermanentJobError("Proactive insights require Meta WhatsApp")

    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active" or tenant.research_consent_status != "granted":
        return {"status": "ineligible", "cadence": cadence, "sent": 0}

    effective_now = _as_utc(now or utcnow())
    run = db.scalar(
        select(BumpaSyncRun)
        .where(
            BumpaSyncRun.tenant_id == tenant.id,
            usable_bumpa_sync_run_predicate(),
        )
        .order_by(BumpaSyncRun.finished_at.desc(), BumpaSyncRun.id.desc())
        .limit(1)
    )
    if run is None or run.finished_at is None:
        return {"status": "no_fresh_data", "cadence": cadence, "sent": 0}
    freshness = _as_utc(run.finished_at)
    if freshness < effective_now - timedelta(hours=settings.insight_max_freshness_hours):
        return {"status": "no_fresh_data", "cadence": cadence, "sent": 0}

    recipients = db.execute(
        select(PhoneIdentity, User)
        .join(User, User.id == PhoneIdentity.user_id)
        .join(
            TenantMembership,
            (TenantMembership.tenant_id == PhoneIdentity.tenant_id)
            & (TenantMembership.user_id == PhoneIdentity.user_id),
        )
        .where(
            PhoneIdentity.tenant_id == tenant.id,
            PhoneIdentity.status == "approved",
            PhoneIdentity.opt_out.is_(False),
            User.status == "active",
            TenantMembership.status == "active",
            TenantMembership.role == "owner",
        )
        .order_by(PhoneIdentity.id.asc())
    ).all()
    if not recipients:
        return {"status": "no_recipients", "cadence": cadence, "sent": 0}

    summary = _build_summary(db, tenant, run, cadence, freshness)
    provider = MetaWhatsAppClient.from_settings(settings)
    sent = 0
    already_sent = 0
    for identity, user in recipients:
        # Re-fetch so an opt-out committed by another worker after the initial
        # query is observed before each external side effect.
        db.expire(identity)
        db.refresh(identity)
        db.expire(user)
        db.refresh(user)
        db.expire(tenant)
        db.refresh(tenant)
        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
                TenantMembership.status == "active",
                TenantMembership.role == "owner",
            )
        )
        if (
            identity.opt_out
            or identity.status != "approved"
            or user.status != "active"
            or tenant.status != "active"
            or tenant.research_consent_status != "granted"
            or membership is None
        ):
            continue
        key = _delivery_key(tenant.id, user.id, cadence, slot)
        outbound = db.scalar(select(WhatsappMessage).where(WhatsappMessage.idempotency_key == key))
        if outbound and outbound.status == "sent" and outbound.meta_message_id:
            already_sent += 1
            continue
        if outbound and outbound.status in {"sending", "ambiguous", "rejected"}:
            raise PermanentJobError("Proactive WhatsApp delivery requires reconciliation")
        if outbound is None:
            outbound = WhatsappMessage(
                tenant_id=tenant.id,
                user_id=user.id,
                idempotency_key=key,
                phone_e164=identity.phone_e164,
                direction="outbound",
                message_type="template",
                payload={
                    "cadence": cadence,
                    "purpose": "proactive_insight",
                    "slot": slot,
                    "template": _template_name(cadence, settings),
                },
                status="prepared",
            )
            db.add(outbound)
            db.commit()
        outbound.status = "sending"
        db.commit()
        try:
            message_id = provider.send_template(
                identity.phone_e164,
                template_name=_template_name(cadence, settings),
                language_code=settings.meta_template_language_code,
                components=[
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": summary}],
                    }
                ],
            )
        except ValueError as exc:
            outbound.status = "rejected"
            db.commit()
            raise PermanentJobError("Proactive WhatsApp template is invalid") from exc
        except MetaProviderError as exc:
            ambiguous = exc.category in {"timeout", "transport"} or (
                exc.category == "provider"
                and exc.http_status is not None
                and exc.http_status >= 500
            )
            outbound.status = (
                "ambiguous" if ambiguous else ("failed" if exc.retryable else "rejected")
            )
            db.commit()
            if outbound.status in {"ambiguous", "rejected"}:
                raise PermanentJobError(
                    "Proactive WhatsApp delivery cannot be retried safely"
                ) from exc
            raise
        outbound.meta_message_id = message_id
        outbound.status = "sent"
        db.commit()
        sent += 1

    return {
        "status": "sent" if sent else "already_sent" if already_sent else "no_recipients",
        "cadence": cadence,
        "sent": sent,
        "already_sent": already_sent,
        "freshness": freshness.isoformat(),
    }


def _build_summary(
    db: Session,
    tenant: Tenant,
    run: BumpaSyncRun,
    cadence: InsightCadence,
    freshness: datetime,
) -> str:
    snapshots = db.scalars(
        select(BumpaMetricSnapshot)
        .where(
            BumpaMetricSnapshot.tenant_id == tenant.id,
            BumpaMetricSnapshot.sync_run_id == run.id,
            BumpaMetricSnapshot.availability == "available",
        )
        .order_by(BumpaMetricSnapshot.metric_key.asc())
    ).all()
    values = {snapshot.metric_key: snapshot for snapshot in snapshots}
    parts = [f"Your {cadence} Bumpa Bestie insight"]
    for key, label in (
        ("sales.total_sales", "sales"),
        ("sales.gross_profit", "gross profit"),
        ("products.products_sold", "products sold"),
    ):
        snapshot = values.get(key)
        if snapshot is None or snapshot.value_decimal is None:
            continue
        parts.append(f"{label}: {_format_metric(snapshot.value_decimal, snapshot.currency_code)}")
    if run.orders_count is not None:
        parts.append(f"orders: {run.orders_count:,}")
    parts.append(f"data refreshed {freshness.strftime('%Y-%m-%d %H:%M UTC')}")
    return ". ".join(parts)[:MAX_TEMPLATE_SUMMARY_CHARS]


def _format_metric(value: Decimal, currency: str | None) -> str:
    normalized = value.quantize(Decimal("0.01"))
    rendered = f"{normalized:,.2f}".rstrip("0").rstrip(".")
    return f"{currency} {rendered}" if currency else rendered


def _cadence_enabled(cadence: InsightCadence, settings: Settings) -> bool:
    return (
        settings.daily_insights_enabled if cadence == "daily" else settings.weekly_insights_enabled
    )


def _template_name(cadence: InsightCadence, settings: Settings) -> str:
    return (
        settings.meta_daily_insight_template_name
        if cadence == "daily"
        else settings.meta_weekly_insight_template_name
    )


def _delivery_key(tenant_id: str, user_id: str, cadence: InsightCadence, slot: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{user_id}\0{cadence}\0{slot}".encode()).hexdigest()
    return f"insight:{digest}"


def _valid_slot(cadence: InsightCadence, slot: str) -> bool:
    try:
        if cadence == "daily":
            return datetime.strptime(slot, "%Y-%m-%d").strftime("%Y-%m-%d") == slot
        year, week = slot.split("-W", 1)
        return len(year) == 4 and year.isdigit() and 1 <= int(week) <= 53 and len(week) == 2
    except (ValueError, TypeError):
        return False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
