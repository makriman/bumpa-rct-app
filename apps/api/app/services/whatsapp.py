from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.rate_limit import (
    RateLimitExceeded,
    RateLimitUnavailable,
    consume_operation_rate_limit,
)
from app.db.models import (
    AgentMessage,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
    WebhookEvent,
    WhatsappDeliveryEvent,
    WhatsappMessage,
)
from app.jobs.runtime import PermanentJobError
from app.providers.contracts import MessagingProvider
from app.providers.local import LocalMessagingProvider
from app.providers.meta import MAX_TEXT_LENGTH, MetaProviderError, MetaWhatsAppClient
from app.services.chat import handle_chat
from app.services.research_events import record_research_event


def process_inbox_event(db: Session, event_id: str, settings: Settings) -> dict[str, Any]:
    event = db.get(WebhookEvent, event_id)
    if not event:
        raise PermanentJobError("Webhook inbox event does not exist")
    if event.processing_status in {"processed", "ignored"}:
        return {"status": "duplicate"}
    event.attempts += 1
    message = extract_message(event.payload)
    delivery = extract_delivery_status(event.payload)
    try:
        if delivery:
            return _process_delivery(db, event, delivery)
        if message:
            return _process_message(db, event, message, settings)
        event.processing_status = "ignored"
        db.commit()
        return {"status": "accepted"}
    except HTTPException as exc:
        if exc.status_code < 500:
            raise PermanentJobError("Webhook event cannot be processed") from exc
        raise


def extract_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        messages = payload["entry"][0]["changes"][0]["value"].get("messages", [])
        return messages[0] if messages else None
    except (KeyError, IndexError, TypeError):
        return None


def extract_delivery_status(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        statuses = payload["entry"][0]["changes"][0]["value"].get("statuses", [])
        return statuses[0] if statuses else None
    except (KeyError, IndexError, TypeError):
        return None


def extract_inbound_sender(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Extract the WABA and receiving phone-number IDs from a Meta message envelope."""

    try:
        entry = payload["entry"][0]
        value = entry["changes"][0]["value"]
        waba_id = entry["id"]
        phone_number_id = value["metadata"]["phone_number_id"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(waba_id, str) or not isinstance(phone_number_id, str):
        return None
    if not waba_id.isdigit() or not phone_number_id.isdigit():
        return None
    return waba_id, phone_number_id


def split_inbox_events(payload: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Split a Meta envelope into one canonical inbox payload per message or status.

    Meta may batch multiple entries, changes, messages, and delivery statuses in one
    signed request. Positional fallback keys keep malformed items distinct while
    provider-issued identifiers remain the primary deduplication keys at ingress.
    """

    items: list[tuple[dict[str, Any], str]] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return [(payload, "root")]
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change_index, change in enumerate(changes):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for kind in ("messages", "statuses"):
                candidates = value.get(kind)
                if not isinstance(candidates, list):
                    continue
                for item_index, item in enumerate(candidates):
                    if not isinstance(item, dict):
                        continue
                    item_value = dict(value)
                    item_value.pop("messages", None)
                    item_value.pop("statuses", None)
                    item_value[kind] = [item]
                    item_change = {**change, "value": item_value}
                    item_entry = {**entry, "changes": [item_change]}
                    item_payload = {**payload, "entry": [item_entry]}
                    fallback_key = f"{entry_index}:{change_index}:{kind}:{item_index}"
                    items.append((item_payload, fallback_key))
    return items or [(payload, "root")]


def _process_delivery(db: Session, event: WebhookEvent, delivery: dict[str, Any]) -> dict[str, Any]:
    message_id = str(delivery.get("id", ""))
    status = str(delivery.get("status", "unknown"))
    timestamp = str(delivery.get("timestamp", "unknown"))
    message = db.scalar(
        select(WhatsappMessage).where(WhatsappMessage.meta_message_id == message_id)
    )
    existing = db.scalar(
        select(WhatsappDeliveryEvent).where(
            WhatsappDeliveryEvent.meta_message_id == message_id,
            WhatsappDeliveryEvent.status == status,
            WhatsappDeliveryEvent.event_timestamp == timestamp,
        )
    )
    if not existing:
        db.add(
            WhatsappDeliveryEvent(
                whatsapp_message_id=message.id if message else None,
                meta_message_id=message_id,
                status=status,
                event_timestamp=timestamp,
                payload=delivery,
            )
        )
    if message:
        message.status = status
    event.processing_status = "processed"
    db.commit()
    return {"status": "accepted", "event_type": "delivery_status"}


def _process_message(
    db: Session,
    event: WebhookEvent,
    message: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    external_id = str(message.get("id", ""))
    if not external_id:
        raise PermanentJobError("WhatsApp message is missing an external identifier")
    reply_sender: tuple[str, str] | None = None
    if settings.whatsapp_backend == "meta":
        inbound_sender = extract_inbound_sender(event.payload)
        if inbound_sender not in settings.allowed_meta_inbound_reply_senders:
            event.processing_status = "ignored"
            db.commit()
            return {"status": "rejected_unconfigured_sender"}
        reply_sender = inbound_sender
    phone = "+" + str(message.get("from", "")).lstrip("+")
    text = str(message.get("text", {}).get("body", "")).strip()
    identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
    tenant = db.get(Tenant, identity.tenant_id) if identity else None
    user = db.get(User, identity.user_id) if identity else None
    membership = (
        db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == identity.tenant_id,
                TenantMembership.user_id == identity.user_id,
                TenantMembership.status == "active",
            )
        )
        if identity
        else None
    )
    inbound = db.scalar(
        select(WhatsappMessage).where(WhatsappMessage.meta_message_id == external_id)
    )
    if not inbound:
        inbound = WhatsappMessage(
            tenant_id=identity.tenant_id if identity else None,
            user_id=identity.user_id if identity else None,
            idempotency_key=_idempotency_key(event.id, "inbound"),
            meta_message_id=external_id,
            wa_id=str(message.get("from", "")),
            phone_e164=phone,
            direction="inbound",
            message_type=str(message.get("type", "unknown")),
            text_body=text,
            payload=message,
        )
        db.add(inbound)
        db.commit()

    # Always honor an opt-out from a previously approved identity, even when
    # its account or membership has since been disabled. It produces no reply
    # and cannot access tenant data.
    if identity and identity.status == "approved" and text.upper() == "STOP":
        identity.opt_out = True
        inbound.status = "processed"
        event.processing_status = "processed"
        record_research_event(
            db,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            event_type="user_opted_out",
            source_parts=(external_id,),
            channel="whatsapp",
            business_outcome={"status": "completed", "opt_out": True},
        )
        db.commit()
        return {"status": "opted_out"}

    authorized = bool(
        identity
        and identity.status == "approved"
        and membership
        and user
        and user.status == "active"
        and tenant
        and tenant.status == "active"
    )
    if not authorized:
        # Only a genuinely unknown phone receives onboarding guidance. Known
        # disabled accounts are processed silently so suspended tenants cannot
        # trigger outbound sends and account state is not disclosed.
        if not identity:
            _deliver_once(
                db,
                event=event,
                purpose="unknown-sender",
                phone=phone,
                body="This number is not approved for Bumpa Bestie. Ask your store owner to add it.",
                settings=settings,
                meta_sender=reply_sender,
            )
        inbound.status = "rejected"
        event.processing_status = "processed"
        db.commit()
        return {"status": "rejected_unknown_sender"}
    assert identity is not None
    assert tenant is not None
    assert user is not None
    if text.upper() == "START":
        identity.opt_out = False
        record_research_event(
            db,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            event_type="user_opted_in",
            source_parts=(external_id,),
            channel="whatsapp",
            business_outcome={"status": "completed", "opt_out": False},
        )
        db.commit()
        _deliver_once(
            db,
            event=event,
            purpose="opt-in",
            phone=phone,
            body="You are opted back in to Bumpa Bestie.",
            settings=settings,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            meta_sender=reply_sender,
        )
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        return {"status": "opted_in"}
    if identity.opt_out:
        inbound.status = "rejected"
        event.processing_status = "processed"
        db.commit()
        return {"status": "opted_out"}
    try:
        consume_operation_rate_limit(
            settings,
            operation="whatsapp-chat",
            scopes={"phone": phone, "tenant": tenant.id},
            limit=settings.whatsapp_rate_limit,
            window_seconds=settings.whatsapp_rate_limit_window_seconds,
        )
    except RateLimitUnavailable as exc:
        raise RuntimeError("Messaging budget is temporarily unavailable") from exc
    except RateLimitExceeded:
        _deliver_once(
            db,
            event=event,
            purpose="rate-limit",
            phone=phone,
            body="You have reached the current message limit. Please try again shortly.",
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
        )
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        return {"status": "rate_limited"}
    existing_agent_message = db.scalar(
        select(AgentMessage).where(
            AgentMessage.tenant_id == tenant.id,
            AgentMessage.channel == "whatsapp",
            AgentMessage.external_message_id == external_id,
        )
    )
    if existing_agent_message:
        outgoing = db.scalar(
            select(AgentMessage)
            .where(
                AgentMessage.tenant_id == tenant.id,
                AgentMessage.conversation_id == existing_agent_message.conversation_id,
                AgentMessage.direction == "outbound",
                AgentMessage.created_at >= existing_agent_message.created_at,
            )
            .order_by(AgentMessage.created_at)
        )
        if not outgoing:
            raise RuntimeError("Inbound agent message exists without its response")
    else:
        _conversation, _incoming, outgoing, _freshness = handle_chat(
            db,
            tenant=tenant,
            user=user,
            message=text,
            channel="whatsapp",
            external_message_id=external_id,
            settings=settings,
        )
    _deliver_text_chunks(
        db,
        event=event,
        purpose="agent-reply",
        phone=phone,
        body=outgoing.content,
        settings=settings,
        tenant_id=tenant.id,
        user_id=user.id,
        meta_sender=reply_sender,
    )
    inbound.status = "processed"
    event.processing_status = "processed"
    db.commit()
    return {"status": "accepted"}


def _deliver_text_chunks(
    db: Session,
    *,
    event: WebhookEvent,
    purpose: str,
    phone: str,
    body: str,
    settings: Settings,
    tenant_id: str | None = None,
    user_id: str | None = None,
    meta_sender: tuple[str, str] | None = None,
) -> list[str]:
    """Deliver a logical reply as deterministic, independently idempotent parts."""

    chunks = _split_text(body)
    multipart = len(chunks) > 1
    message_ids: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_purpose = f"{purpose}:chunk:{index:04d}" if multipart else purpose
        message_ids.append(
            _deliver_once(
                db,
                event=event,
                purpose=chunk_purpose,
                phone=phone,
                body=chunk,
                settings=settings,
                tenant_id=tenant_id,
                user_id=user_id,
                meta_sender=meta_sender,
            )
        )
    return message_ids


def _split_text(body: str, max_length: int = MAX_TEXT_LENGTH) -> list[str]:
    """Partition text without data loss, preferring a nearby whitespace boundary."""

    if max_length < 1:
        raise ValueError("WhatsApp chunk length must be positive")
    if len(body) <= max_length:
        return [body]
    chunks: list[str] = []
    remaining = body
    while len(remaining) > max_length:
        boundary = max(
            remaining.rfind("\n", 0, max_length),
            remaining.rfind(" ", 0, max_length),
        )
        # Avoid tiny chunks caused by whitespace near the start of the window.
        split_at = boundary + 1 if boundary >= max_length // 2 else max_length
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    chunks.append(remaining)
    return chunks


def _deliver_once(
    db: Session,
    *,
    event: WebhookEvent,
    purpose: str,
    phone: str,
    body: str,
    settings: Settings,
    tenant_id: str | None = None,
    user_id: str | None = None,
    meta_sender: tuple[str, str] | None = None,
) -> str:
    key = _idempotency_key(event.id, purpose)
    outbound = db.scalar(select(WhatsappMessage).where(WhatsappMessage.idempotency_key == key))
    if outbound and outbound.status == "sent" and outbound.meta_message_id:
        return outbound.meta_message_id
    if outbound and outbound.status in {"sending", "ambiguous", "rejected"}:
        raise PermanentJobError("WhatsApp delivery outcome requires reconciliation")
    if not outbound:
        outbound = WhatsappMessage(
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=key,
            phone_e164=phone,
            direction="outbound",
            message_type="text",
            text_body=body,
            payload={
                "provider": settings.whatsapp_backend,
                "purpose": purpose,
                **(
                    {
                        "sender_waba_id": meta_sender[0],
                        "sender_phone_number_id": meta_sender[1],
                    }
                    if meta_sender
                    else {}
                ),
            },
            status="prepared",
        )
        db.add(outbound)
        db.commit()
    outbound.status = "sending"
    db.commit()
    try:
        provider = _messaging_provider(
            settings,
            meta_sender=meta_sender,
        )
        message_id = provider.send_text(phone, body)
    except ValueError as exc:
        # Validation failures are deterministic. Persist the rejected terminal
        # state before converting them to a permanent job failure.
        outbound.status = "rejected"
        db.commit()
        raise PermanentJobError("WhatsApp delivery request is invalid") from exc
    except MetaProviderError as exc:
        outcome_is_ambiguous = exc.category in {"timeout", "transport"} or (
            exc.category == "provider" and exc.http_status is not None and exc.http_status >= 500
        )
        if outcome_is_ambiguous:
            outbound.status = "ambiguous"
        elif not exc.retryable:
            outbound.status = "rejected"
        else:
            outbound.status = "failed"
        db.commit()
        if outbound.status in {"ambiguous", "rejected"}:
            raise PermanentJobError("WhatsApp delivery cannot be retried safely") from exc
        raise
    if settings.whatsapp_backend == "mock":
        # The local provider deliberately returns a deterministic delivery ID for
        # identical inputs. Namespace that simulated ID by this durable send so
        # separate local messages do not violate Meta's production uniqueness rule.
        outbound.payload = {**outbound.payload, "provider_message_id": message_id}
        outbound.meta_message_id = f"{message_id}:{key.rsplit(':', 1)[-1][:16]}"
    else:
        outbound.meta_message_id = message_id
    outbound.status = "sent"
    db.commit()
    return message_id


def _messaging_provider(
    settings: Settings,
    *,
    meta_sender: tuple[str, str] | None = None,
) -> MessagingProvider:
    if settings.whatsapp_backend == "mock":
        return LocalMessagingProvider()
    if settings.whatsapp_backend == "meta":
        if meta_sender:
            return MetaWhatsAppClient.for_inbound_reply(
                settings,
                waba_id=meta_sender[0],
                phone_number_id=meta_sender[1],
            )
        return MetaWhatsAppClient.from_settings(settings)
    raise PermanentJobError("WhatsApp delivery is disabled")


def _idempotency_key(event_id: str, purpose: str) -> str:
    digest = hashlib.sha256(f"{event_id}\0{purpose}".encode()).hexdigest()
    return f"whatsapp:{digest}"
