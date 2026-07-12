from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import (
    AgentMessage,
    PhoneIdentity,
    Tenant,
    User,
    WebhookEvent,
    WhatsappDeliveryEvent,
    WhatsappMessage,
)
from app.db.session import get_db, set_security_context
from app.providers.local import LocalMessagingProvider
from app.services.chat import handle_chat

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])


@router.get("")
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    if mode == "subscribe" and hmac.compare_digest(token or "", settings.meta_webhook_verify_token):
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Invalid webhook verification")


@router.post("")
async def receive_webhook(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Durably accept an event, then process inline only for the deterministic local adapter.

    Failed events remain retriable. Production Meta mode acknowledges the durable inbox quickly and
    requires a configured queue consumer rather than pretending inline work is production-ready.
    """
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not _valid_signature(raw, signature, settings.meta_app_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    set_security_context(db, privileged=True)
    message = _extract_message(payload)
    delivery = _extract_delivery_status(payload)
    external_id = _event_id(raw, message, delivery)
    event = db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == "whatsapp", WebhookEvent.external_event_id == external_id
        )
    )
    if event and event.processing_status in {"processed", "ignored"}:
        return {"status": "duplicate"}
    if not event:
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id=external_id,
            signature_valid=True,
            payload=payload,
        )
        db.add(event)
        try:
            db.commit()  # Durable inbox boundary before any provider or agent work.
        except IntegrityError:
            db.rollback()
            concurrent = db.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "whatsapp",
                    WebhookEvent.external_event_id == external_id,
                )
            )
            if concurrent and concurrent.processing_status in {"received", "failed"}:
                event = concurrent
            else:
                return {"status": "duplicate"}
    if settings.whatsapp_backend == "meta" and not settings.is_local:
        # The event is durable, but acknowledging it as queued without an actual queue consumer
        # would silently lose work. Meta may retry safely until the production adapter is enabled.
        raise HTTPException(
            status_code=503,
            detail="No production WhatsApp queue adapter is configured; retry is safe",
        )
    event.attempts += 1
    try:
        if delivery:
            result = _process_delivery(db, event, delivery)
        elif message:
            result = _process_message(db, event, message)
        else:
            event.processing_status = "ignored"
            db.commit()
            result = {"status": "accepted"}
        return result
    except HTTPException:
        _mark_failed(db, event.id)
        raise
    except Exception as exc:
        _mark_failed(db, event.id)
        raise HTTPException(
            status_code=503, detail="Webhook processing failed; retry is safe"
        ) from exc


def _process_delivery(db: Session, event: WebhookEvent, delivery: dict[str, Any]) -> dict:
    message_id = str(delivery.get("id", ""))
    status = str(delivery.get("status", "unknown"))
    timestamp = str(delivery.get("timestamp", "unknown"))
    message = db.scalar(
        select(WhatsappMessage).where(WhatsappMessage.meta_message_id == message_id)
    )
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


def _process_message(db: Session, event: WebhookEvent, message: dict[str, Any]) -> dict:
    external_id = str(message.get("id", ""))
    phone = "+" + str(message.get("from", "")).lstrip("+")
    text = str(message.get("text", {}).get("body", "")).strip()
    identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
    inbound = db.scalar(
        select(WhatsappMessage).where(WhatsappMessage.meta_message_id == external_id)
    )
    if not inbound:
        inbound = WhatsappMessage(
            tenant_id=identity.tenant_id if identity else None,
            user_id=identity.user_id if identity else None,
            meta_message_id=external_id,
            wa_id=str(message.get("from", "")),
            phone_e164=phone,
            direction="inbound",
            message_type=str(message.get("type", "unknown")),
            text_body=text,
            payload=message,
        )
        db.add(inbound)
    if not identity or identity.status != "approved":
        LocalMessagingProvider().send_text(
            phone, "This number is not approved for Bumpa Bestie. Ask your store owner to add it."
        )
        inbound.status = "rejected"
        event.processing_status = "processed"
        db.commit()
        return {"status": "rejected_unknown_sender"}
    if text.upper() == "STOP":
        identity.opt_out = True
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        return {"status": "opted_out"}
    if text.upper() == "START":
        identity.opt_out = False
        LocalMessagingProvider().send_text(phone, "You are opted back in to Bumpa Bestie.")
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        return {"status": "opted_in"}
    if identity.opt_out:
        inbound.status = "rejected"
        event.processing_status = "processed"
        db.commit()
        return {"status": "opted_out"}
    tenant = db.get(Tenant, identity.tenant_id)
    user = db.get(User, identity.user_id)
    if not tenant or not user or tenant.status != "active":
        raise HTTPException(status_code=409, detail="Sender account is unavailable")
    existing_agent_message = db.scalar(
        select(AgentMessage).where(
            AgentMessage.channel == "whatsapp",
            AgentMessage.external_message_id == external_id,
        )
    )
    if existing_agent_message:
        outgoing = db.scalar(
            select(AgentMessage)
            .where(
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
        )
    outbound_id = LocalMessagingProvider().send_text(phone, outgoing.content)
    if not db.scalar(select(WhatsappMessage).where(WhatsappMessage.meta_message_id == outbound_id)):
        db.add(
            WhatsappMessage(
                tenant_id=tenant.id,
                user_id=user.id,
                meta_message_id=outbound_id,
                phone_e164=phone,
                direction="outbound",
                message_type="text",
                text_body=outgoing.content,
                payload={"local": True},
                status="sent",
            )
        )
    inbound.status = "processed"
    event.processing_status = "processed"
    db.commit()
    return {"status": "accepted"}


def _mark_failed(db: Session, event_id: str) -> None:
    db.rollback()
    set_security_context(db, privileged=True)
    event = db.get(WebhookEvent, event_id)
    if event:
        event.attempts += 1
        event.processing_status = "failed"
        db.commit()


def _valid_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _extract_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        messages = payload["entry"][0]["changes"][0]["value"].get("messages", [])
        return messages[0] if messages else None
    except (KeyError, IndexError, TypeError):
        return None


def _extract_delivery_status(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        statuses = payload["entry"][0]["changes"][0]["value"].get("statuses", [])
        return statuses[0] if statuses else None
    except (KeyError, IndexError, TypeError):
        return None


def _event_id(
    raw: bytes,
    message: dict[str, Any] | None,
    delivery: dict[str, Any] | None,
) -> str:
    if message and message.get("id"):
        return str(message["id"])
    if delivery and delivery.get("id"):
        return ":".join(
            [
                str(delivery["id"]),
                str(delivery.get("status", "unknown")),
                str(delivery.get("timestamp", "unknown")),
            ]
        )
    return hashlib.sha256(raw).hexdigest()
