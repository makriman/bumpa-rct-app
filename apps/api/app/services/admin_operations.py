from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_contracts import (
    AdminExportView,
    BumpaConnectionStatusView,
    HermesCallErrorView,
    HermesProfileStatusView,
    TenantOperationsView,
    TenantPersonView,
    TenantPhoneView,
    WhatsappDeliveryFailureView,
)
from app.db.models import (
    BumpaConnection,
    HermesProfile,
    PhoneIdentity,
    SystemError,
    Tenant,
    TenantMembership,
    User,
    WhatsappDeliveryEvent,
    WhatsappMessage,
)
from app.providers.redaction import csv_safe

FAILED_WHATSAPP_STATUSES = frozenset({"failed", "undeliverable", "rejected"})
HERMES_ERROR_SERVICES = frozenset({"hermes", "hermes_control", "agent_runtime"})
SAFE_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
SAFE_PROVIDER_CODE = re.compile(r"^[0-9]{1,10}$")
MAX_ADMIN_EXPORT_ROWS = 1000
SAFE_DELIVERY_TITLES = frozenset(
    {
        "Message failed to send",
        "Message undeliverable",
        "Rate limit hit",
        "Recipient phone number not in allowed list",
    }
)


def tenant_operations(db: Session, tenant: Tenant) -> TenantOperationsView:
    memberships = db.execute(
        select(TenantMembership, User)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant.id)
        .order_by(TenantMembership.created_at.asc(), TenantMembership.id.asc())
    ).all()
    phones = db.scalars(
        select(PhoneIdentity)
        .where(PhoneIdentity.tenant_id == tenant.id)
        .order_by(PhoneIdentity.created_at.asc(), PhoneIdentity.id.asc())
    ).all()
    connection = db.scalar(select(BumpaConnection).where(BumpaConnection.tenant_id == tenant.id))
    profile = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == tenant.id))
    return TenantOperationsView(
        tenant_id=tenant.id,
        people=[
            TenantPersonView(
                membership_id=membership.id,
                user_id=user.id,
                name=user.name,
                phone_masked=mask_phone(user.primary_phone_e164),
                role=membership.role,
                status=membership.status,
            )
            for membership, user in memberships
        ],
        phones=[
            TenantPhoneView(
                id=phone.id,
                user_id=phone.user_id,
                phone_masked=mask_phone(phone.phone_e164),
                label=phone.label,
                status=phone.status,
                opt_out=phone.opt_out,
            )
            for phone in phones
        ],
        bumpa=_bumpa_status(connection),
        hermes=_hermes_status(profile),
    )


def whatsapp_delivery_failures(
    db: Session, *, tenant_id: str | None, limit: int
) -> list[WhatsappDeliveryFailureView]:
    statement = (
        select(WhatsappDeliveryEvent, WhatsappMessage)
        .outerjoin(
            WhatsappMessage,
            WhatsappMessage.id == WhatsappDeliveryEvent.whatsapp_message_id,
        )
        .where(WhatsappDeliveryEvent.status.in_(FAILED_WHATSAPP_STATUSES))
    )
    if tenant_id is not None:
        statement = statement.where(WhatsappMessage.tenant_id == tenant_id)
    rows = db.execute(
        statement.order_by(WhatsappDeliveryEvent.created_at.desc()).limit(limit)
    ).all()
    return [
        WhatsappDeliveryFailureView(
            id=event.id,
            tenant_id=message.tenant_id if message else None,
            message_reference=_opaque_reference(event.meta_message_id),
            phone_masked=mask_phone(message.phone_e164) if message and message.phone_e164 else None,
            status=event.status,
            provider_error_code=_delivery_error_code(event.payload),
            provider_error_title=_delivery_error_title(event.payload),
            created_at=event.created_at,
        )
        for event, message in rows
    ]


def hermes_call_errors(
    db: Session, *, tenant_id: str | None, limit: int
) -> list[HermesCallErrorView]:
    statement = select(SystemError).where(SystemError.service.in_(HERMES_ERROR_SERVICES))
    if tenant_id is not None:
        statement = statement.where(SystemError.tenant_id == tenant_id)
    rows = db.scalars(statement.order_by(SystemError.created_at.desc()).limit(limit)).all()
    result: list[HermesCallErrorView] = []
    for row in rows:
        metadata = row.error_metadata if isinstance(row.error_metadata, dict) else {}
        raw_category = metadata.get("category")
        category = (
            raw_category
            if isinstance(raw_category, str) and SAFE_CATEGORY.fullmatch(raw_category)
            else "provider_failure"
        )
        raw_profile_id = metadata.get("profile_id")
        result.append(
            HermesCallErrorView(
                id=row.id,
                tenant_id=row.tenant_id,
                category=category,
                retryable=(
                    metadata["retryable"] if isinstance(metadata.get("retryable"), bool) else None
                ),
                profile_reference=(
                    _opaque_reference(raw_profile_id)
                    if isinstance(raw_profile_id, str) and raw_profile_id
                    else None
                ),
                created_at=row.created_at,
            )
        )
    return result


def build_admin_export(db: Session, *, export_id: str, generated_at: str) -> AdminExportView:
    tenants = db.scalars(
        select(Tenant)
        .order_by(Tenant.created_at.asc(), Tenant.id.asc())
        .limit(MAX_ADMIN_EXPORT_ROWS)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "tenant_id",
            "slug",
            "name",
            "status",
            "research_consent_status",
            "member_count",
            "approved_phone_count",
            "bumpa_status",
            "bumpa_provider",
            "bumpa_scope_type",
            "bumpa_scope_last4",
            "bumpa_last_successful_sync_at",
            "hermes_status",
            "hermes_provider",
        )
    )
    for tenant in tenants:
        snapshot = tenant_operations(db, tenant)
        writer.writerow(
            _csv_row(
                (
                    tenant.id,
                    tenant.slug,
                    tenant.name,
                    tenant.status,
                    tenant.research_consent_status,
                    len(snapshot.people),
                    sum(phone.status == "approved" for phone in snapshot.phones),
                    snapshot.bumpa.status,
                    snapshot.bumpa.provider or "",
                    snapshot.bumpa.scope_type or "",
                    snapshot.bumpa.scope_id_last4 or "",
                    snapshot.bumpa.last_successful_sync_at.isoformat()
                    if snapshot.bumpa.last_successful_sync_at
                    else "",
                    snapshot.hermes.status,
                    snapshot.hermes.provider or "",
                )
            )
        )
    content = output.getvalue()
    return AdminExportView(
        export_id=export_id,
        filename=f"bumpa-bestie-admin-operations-{generated_at[:10]}.csv",
        content_type="text/csv",
        content=content,
        row_count=len(tenants),
        checksum_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def record_hermes_call_error(
    db: Session,
    *,
    tenant_id: str,
    profile_id: str,
    category: str,
) -> SystemError:
    """Persist an allowlisted Hermes diagnostic after the caller rolls back work.

    Exception text, prompt content, upstream bodies, URLs and credentials are
    deliberately excluded. The caller owns the surrounding transaction.
    """

    safe_category = category if SAFE_CATEGORY.fullmatch(category) else "provider_failure"
    row = SystemError(
        tenant_id=tenant_id,
        service="hermes",
        severity="error",
        message="Hermes call failed",
        error_metadata={
            "category": safe_category,
            "profile_id": profile_id,
        },
    )
    db.add(row)
    return row


def mask_phone(value: str) -> str:
    if len(value) <= 6:
        return "•" * len(value)
    return f"{value[:3]}{'•' * max(3, len(value) - 7)}{value[-4:]}"


def _bumpa_status(connection: BumpaConnection | None) -> BumpaConnectionStatusView:
    if connection is None:
        return BumpaConnectionStatusView(connected=False, status="not_connected")
    return BumpaConnectionStatusView(
        connected=True,
        status=connection.status,
        scope_type=connection.scope_type,
        scope_id_last4=connection.scope_id[-4:] if connection.scope_id else None,
        provider=connection.provider,
        last_successful_sync_at=connection.last_successful_sync_at,
        last_failed_sync_at=connection.last_failed_sync_at,
        last_error="sync_failed" if connection.last_error else None,
    )


def _hermes_status(profile: HermesProfile | None) -> HermesProfileStatusView:
    if profile is None:
        return HermesProfileStatusView(provisioned=False, status="not_provisioned")
    return HermesProfileStatusView(
        provisioned=True,
        profile_name=profile.profile_name,
        provider=profile.provider,
        status=profile.status,
        api_port=profile.api_port,
    )


def _opaque_reference(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _delivery_error(payload: dict[str, Any]) -> dict[str, Any]:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return {}
    return errors[0]


def _delivery_error_code(payload: dict[str, Any]) -> str | None:
    raw = _delivery_error(payload).get("code")
    candidate = str(raw) if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
    return candidate if SAFE_PROVIDER_CODE.fullmatch(candidate) else None


def _delivery_error_title(payload: dict[str, Any]) -> str | None:
    raw = _delivery_error(payload).get("title")
    if not isinstance(raw, str):
        return None
    normalized = " ".join(raw.split())
    return normalized if normalized in SAFE_DELIVERY_TITLES else None


def _csv_row(values: Iterable[object]) -> list[str]:
    return [csv_safe(str(value)) for value in values]
