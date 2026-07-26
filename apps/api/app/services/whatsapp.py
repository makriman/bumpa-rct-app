from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.rate_limit import (
    RateLimitExceeded,
    RateLimitUnavailable,
    consume_operation_rate_limit,
)
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
    HermesProfile,
    OperationalAgentEvent,
    PendingAgentAction,
    PhoneIdentity,
    ResearchConsent,
    Tenant,
    TenantMembership,
    User,
    WebhookEvent,
    WhatsappDeliveryEvent,
    WhatsappMessage,
)
from app.db.session import SessionLocal, set_security_context
from app.jobs.runtime import PermanentJobError
from app.providers.contracts import MessagingProvider
from app.providers.hermes import HermesProfileError, endpoint_for
from app.providers.local import LocalMessagingProvider
from app.providers.meta import MAX_TEXT_LENGTH, MetaProviderError, MetaWhatsAppClient
from app.services.agent_actions import (
    AgentActionError,
    action_preview,
    confirmation_token,
    decide_pending_action,
)
from app.services.chat import handle_chat
from app.services.generated_media import GeneratedMediaError, generated_media_path
from app.services.media import (
    MediaProcessingError,
    process_whatsapp_content,
    synthesize_voice_note,
)
from app.services.research_events import record_research_event

DELIVERY_STATUS_RANK = {
    "received": 0,
    "prepared": 1,
    "sending": 2,
    "sent": 3,
    "delivered": 4,
    "read": 5,
}


def process_inbox_event(db: Session, event_id: str, settings: Settings) -> dict[str, Any]:
    event = db.get(WebhookEvent, event_id)
    if not event:
        raise PermanentJobError("Webhook inbox event does not exist")
    if event.processing_status in {"processed", "ignored"}:
        return {"status": "duplicate"}
    message = extract_message(event.payload)
    delivery = extract_delivery_status(event.payload)
    if message and not settings.is_local:
        phone = "+" + str(message.get("from", "")).lstrip("+")
        with _whatsapp_fifo_lock(settings, phone):
            return _process_claimed_event(db, event, message, delivery, settings)
    return _process_claimed_event(db, event, message, delivery, settings)


def _process_claimed_event(
    db: Session,
    event: WebhookEvent,
    message: dict[str, Any] | None,
    delivery: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any]:
    event.attempts += 1
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


def acknowledge_inbound_receipts(payload: dict[str, Any], settings: Settings) -> None:
    """Best-effort read receipt and typing start immediately after durable ingress."""

    if settings.whatsapp_backend != "meta":
        return
    for item_payload, _fallback_key in split_inbox_events(payload):
        message = extract_message(item_payload)
        sender = extract_inbound_sender(item_payload)
        if (
            message is None
            or sender is None
            or sender not in settings.allowed_meta_inbound_reply_senders
        ):
            continue
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        try:
            MetaWhatsAppClient.for_inbound_reply(
                settings,
                waba_id=sender[0],
                phone_number_id=sender[1],
            ).mark_read(message_id, typing=True)
        except (MetaProviderError, ValueError):
            continue


@contextmanager
def _whatsapp_fifo_lock(settings: Settings, phone: str) -> Iterator[None]:
    digest = hashlib.sha256(phone.encode()).hexdigest()
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )
    lock = client.lock(
        f"bumpabestie:whatsapp:fifo:{digest}",
        timeout=settings.whatsapp_fifo_lock_seconds,
        blocking_timeout=settings.whatsapp_fifo_wait_seconds,
        thread_local=False,
    )
    try:
        acquired = lock.acquire(blocking=True)
    except RedisError as exc:
        client.close()
        raise RuntimeError("WhatsApp ordering is temporarily unavailable") from exc
    if not acquired:
        client.close()
        raise RuntimeError("An earlier WhatsApp message is still processing")
    stopped = Event()

    def renew() -> None:
        interval = min(30.0, settings.whatsapp_fifo_lock_seconds / 3)
        while not stopped.wait(interval):
            try:
                lock.extend(settings.whatsapp_fifo_lock_seconds, replace_ttl=True)
            except (LockError, RedisError):
                return

    renewal = Thread(target=renew, name="whatsapp-fifo-renewal", daemon=True)
    renewal.start()
    try:
        yield
    finally:
        stopped.set()
        renewal.join(timeout=0.5)
        try:
            lock.release()
        except (LockError, RedisError):
            pass
        client.close()


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
        rank = DELIVERY_STATUS_RANK.get(status)
        if rank is not None and rank >= message.status_rank:
            message.status = status
            message.status_rank = rank
        elif status == "failed":
            message.payload = {
                **message.payload,
                "delivery_failure": {
                    "status": "failed",
                    "timestamp": timestamp,
                },
            }
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
    text = _message_text(message)
    reply_to_meta_message_id = (
        str(message.get("context", {}).get("id"))
        if isinstance(message.get("context"), dict) and message.get("context", {}).get("id")
        else None
    )
    if settings.whatsapp_backend == "meta" and settings.whatsapp_progress_enabled:
        assert reply_sender is not None
        try:
            MetaWhatsAppClient.for_inbound_reply(
                settings,
                waba_id=reply_sender[0],
                phone_number_id=reply_sender[1],
            ).mark_read(external_id, typing=True)
        except (MetaProviderError, ValueError):
            # Read/typing UX is best effort. The durable response path remains primary.
            pass
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
            reply_to_meta_message_id=reply_to_meta_message_id,
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
    active_conversation = db.scalar(
        select(Conversation)
        .where(
            Conversation.tenant_id == tenant.id,
            Conversation.user_id == user.id,
            Conversation.channel == "whatsapp",
            Conversation.status == "open",
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    if text.lower() in {"/new", "/reset"}:
        if active_conversation:
            active_conversation.status = "closed"
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        _deliver_once(
            db,
            event=event,
            purpose="conversation-reset",
            phone=phone,
            body="Started a fresh conversation. What would you like to work on?",
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
        )
        return {"status": "conversation_reset"}
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
    action_decision = _interactive_action(message)
    if action_decision is not None:
        decision, action_id = action_decision
        action = db.scalar(
            select(PendingAgentAction).where(
                PendingAgentAction.id == action_id,
                PendingAgentAction.tenant_id == tenant.id,
                PendingAgentAction.user_id == user.id,
            )
        )
        if action is None:
            action_body = "That action is no longer available."
            action_status = "action_not_found"
        else:
            try:
                decided = decide_pending_action(
                    db,
                    settings,
                    action_id=action.id,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    supplied_token=confirmation_token(settings, action),
                    decision=decision,
                )
                action_body = (
                    "Done. The confirmed action was completed."
                    if decided.status == "succeeded"
                    else "Cancelled. Nothing was changed."
                )
                action_status = f"action_{decided.status}"
            except AgentActionError:
                action_body = (
                    "I could not complete that action safely. Please review it and try again."
                )
                action_status = "action_failed"
        _deliver_once(
            db,
            event=event,
            purpose=f"action-decision:{action_id}",
            phone=phone,
            body=action_body,
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
            reply_to_message_id=external_id,
        )
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        return {"status": action_status}
    consent_decision = _interactive_research_consent(message, settings)
    if consent_decision is not None:
        tenant.research_consent_status = "granted" if consent_decision == "grant" else "withdrawn"
        db.add(
            ResearchConsent(
                tenant_id=tenant.id,
                status=tenant.research_consent_status,
                policy_version=settings.research_consent_policy_version,
                actor_user_id=user.id,
            )
        )
        _deliver_once(
            db,
            event=event,
            purpose=f"research-consent:{consent_decision}",
            phone=phone,
            body=(
                "Thank you. Your conversation data can now be included in the RCT research."
                if consent_decision == "grant"
                else "Understood. Research collection stays off; every product feature still works."
            ),
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
            reply_to_message_id=external_id,
        )
        inbound.status = "processed"
        event.processing_status = "processed"
        db.commit()
        return {"status": f"research_consent_{consent_decision}"}
    voice_reply_requested = False
    voice_reply_language = "en"
    speech_endpoint = None
    if settings.whatsapp_speech_enabled:
        profile = db.scalar(
            select(HermesProfile).where(
                HermesProfile.tenant_id == tenant.id,
                HermesProfile.status == "active",
            )
        )
        if profile is not None:
            try:
                speech_endpoint = endpoint_for(profile, settings)
            except HermesProfileError:
                speech_endpoint = None
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
        try:
            if settings.whatsapp_backend == "meta":
                assert reply_sender is not None
                processed = process_whatsapp_content(
                    message=message,
                    settings=settings,
                    meta_client=MetaWhatsAppClient.for_inbound_reply(
                        settings,
                        waba_id=reply_sender[0],
                        phone_number_id=reply_sender[1],
                    ),
                    tenant_id=tenant.id,
                    workspace=f"media-{hashlib.sha256(external_id.encode()).hexdigest()[:24]}",
                    speech_endpoint=speech_endpoint,
                )
            elif text:
                processed = process_whatsapp_content(
                    message=message,
                    settings=settings,
                )
            else:
                raise MediaProcessingError("This attachment cannot be processed in local mode.")
        except (MediaProcessingError, MetaProviderError, ValueError) as exc:
            fallback = str(exc).strip() or "I could not read that attachment."
            if len(fallback) > 300:
                fallback = "I could not read that attachment safely. Please resend it in a supported format."
            _deliver_once(
                db,
                event=event,
                purpose="media-fallback",
                phone=phone,
                body=fallback,
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=reply_sender,
            )
            inbound.status = "processed"
            inbound.media_metadata = {
                "type": str(message.get("type", "unknown")),
                "processing_status": "failed",
            }
            event.processing_status = "processed"
            db.add(
                OperationalAgentEvent(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    channel="whatsapp",
                    event_type="media_processing",
                    status="failed",
                    media_type=str(message.get("type", "unknown")),
                    error_code="media_processing_failed",
                )
            )
            db.commit()
            return {"status": "media_fallback"}
        if reply_to_meta_message_id:
            replied = db.scalar(
                select(AgentMessage).where(
                    AgentMessage.tenant_id == tenant.id,
                    AgentMessage.channel == "whatsapp",
                    AgentMessage.external_message_id == reply_to_meta_message_id,
                )
            )
            if replied:
                processed = processed.__class__(
                    prompt=(
                        f"{processed.prompt}\n\nThis replies to the earlier message: "
                        f"{replied.redacted_content or replied.content[:500]}"
                    ),
                    provider_content_parts=processed.provider_content_parts,
                    stored_content_parts=processed.stored_content_parts,
                    metadata=processed.metadata,
                )
        inbound.media_metadata = processed.metadata
        voice_reply_requested = _voice_reply_requested(processed.prompt)
        voice_reply_language = _voice_reply_language(processed)
        clarification = _low_confidence_clarification(processed)
        if clarification is not None:
            voice_reply_requested = False
            processed.metadata["clarification_required"] = True
            _conversation, _incoming, outgoing, _freshness = handle_chat(
                db,
                tenant=tenant,
                user=user,
                message=processed.prompt,
                channel="whatsapp",
                conversation_id=active_conversation.id if active_conversation else None,
                external_message_id=external_id,
                provider_content_parts=processed.provider_content_parts,
                stored_content_parts=processed.stored_content_parts,
                reply_to_external_message_id=reply_to_meta_message_id,
                media_metadata=processed.metadata,
                fixed_response=clarification,
                settings=settings,
            )
        else:
            progress = _WhatsAppProgress(
                settings=settings,
                event_id=event.id,
                inbound_message_id=external_id,
                phone=phone,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=reply_sender,
            )
            progress.start()
            try:
                _conversation, _incoming, outgoing, _freshness = handle_chat(
                    db,
                    tenant=tenant,
                    user=user,
                    message=processed.prompt,
                    channel="whatsapp",
                    conversation_id=active_conversation.id if active_conversation else None,
                    external_message_id=external_id,
                    provider_content_parts=processed.provider_content_parts,
                    stored_content_parts=processed.stored_content_parts,
                    reply_to_external_message_id=reply_to_meta_message_id,
                    media_metadata=processed.metadata,
                    event_callback=progress.on_event,
                    settings=settings,
                )
            finally:
                progress.finish()
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
        reply_to_message_id=external_id,
    )
    if (
        voice_reply_requested
        and settings.whatsapp_speech_enabled
        and settings.whatsapp_backend == "meta"
    ):
        try:
            voice_note = synthesize_voice_note(
                settings,
                text=outgoing.content,
                endpoint=speech_endpoint,
                language=voice_reply_language,
            )
            voice_content = voice_note["content"]
            if not isinstance(voice_content, bytes):
                raise MediaProcessingError("The voice provider returned invalid media.")
            _deliver_media_once(
                db,
                event=event,
                purpose="agent-voice-reply",
                phone=phone,
                content=voice_content,
                mime_type=str(voice_note["mime_type"]),
                filename=str(voice_note["filename"]),
                media_type="audio",
                voice=True,
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=reply_sender,
                reply_to_message_id=external_id,
            )
        except (MediaProcessingError, MetaProviderError, PermanentJobError, ValueError):
            db.add(
                OperationalAgentEvent(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    conversation_id=outgoing.conversation_id,
                    channel="whatsapp",
                    event_type="outbound_media",
                    status="failed",
                    media_type="audio",
                    error_code="voice_reply_failed",
                )
            )
            db.commit()
    pending_actions = db.scalars(
        select(PendingAgentAction).where(
            PendingAgentAction.tenant_id == tenant.id,
            PendingAgentAction.user_id == user.id,
            PendingAgentAction.conversation_id == outgoing.conversation_id,
            PendingAgentAction.status == "pending",
        )
    ).all()
    generated_media = db.scalars(
        select(GeneratedAgentMedia).where(
            GeneratedAgentMedia.tenant_id == tenant.id,
            GeneratedAgentMedia.user_id == user.id,
            GeneratedAgentMedia.conversation_id == outgoing.conversation_id,
            GeneratedAgentMedia.agent_message_id == outgoing.id,
            GeneratedAgentMedia.channel == "whatsapp",
            GeneratedAgentMedia.status == "pending",
        )
    ).all()
    for media in generated_media:
        try:
            path = generated_media_path(settings, media)
            content = path.read_bytes()
            if (
                len(content) != media.byte_size
                or hashlib.sha256(content).hexdigest() != media.checksum_sha256
            ):
                raise GeneratedMediaError("Generated media integrity check failed.")
            _deliver_media_once(
                db,
                event=event,
                purpose=f"generated-media:{media.id}",
                phone=phone,
                content=content,
                mime_type=media.mime_type,
                filename=media.filename or _generated_media_filename(media),
                media_type=media.media_type,
                voice=False,
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=reply_sender,
                reply_to_message_id=external_id,
            )
            media.status = "delivered"
            media.delivered_at = utcnow()
            path.unlink(missing_ok=True)
        except (
            GeneratedMediaError,
            MetaProviderError,
            OSError,
            PermanentJobError,
            ValueError,
        ):
            media.status = "failed"
            db.add(
                OperationalAgentEvent(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    conversation_id=outgoing.conversation_id,
                    channel="whatsapp",
                    event_type="managed_media",
                    status="failed",
                    media_type=media.media_type,
                    error_code="generated_media_delivery_failed",
                )
            )
    for action in pending_actions:
        _deliver_text_chunks(
            db,
            event=event,
            purpose=f"action-preview:{action.id}",
            phone=phone,
            body=action_preview(action),
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
            reply_to_message_id=external_id,
        )
        _deliver_once(
            db,
            event=event,
            purpose=f"action-confirmation:{action.id}",
            phone=phone,
            body="Confirm the exact action preview above? Nothing has been written yet.",
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
            reply_to_message_id=external_id,
            interactive_buttons=[
                (f"agent_action:confirm:{action.id}", "Confirm"),
                (f"agent_action:deny:{action.id}", "Cancel"),
            ],
        )
    if (
        settings.whatsapp_primary_pilot_enabled
        and tenant.research_consent_status == "pending"
        and db.scalar(
            select(ResearchConsent.id).where(
                ResearchConsent.tenant_id == tenant.id,
                ResearchConsent.status == "pending",
                ResearchConsent.policy_version == settings.research_consent_policy_version,
            )
        )
        is None
    ):
        _deliver_once(
            db,
            event=event,
            purpose=f"research-consent-request:{settings.research_consent_policy_version}",
            phone=phone,
            body=(
                "Bumpa Bestie now offers broader AI-assistant, business guidance, research and "
                "media features. "
                "May we include your conversation data in the RCT research? Choosing no will "
                "not disable any product feature."
            ),
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=reply_sender,
            reply_to_message_id=external_id,
            interactive_buttons=[
                (
                    f"research_consent:grant:{settings.research_consent_policy_version}",
                    "I consent",
                ),
                (
                    f"research_consent:decline:{settings.research_consent_policy_version}",
                    "No thanks",
                ),
            ],
        )
        db.add(
            ResearchConsent(
                tenant_id=tenant.id,
                status="pending",
                policy_version=settings.research_consent_policy_version,
                actor_user_id=None,
            )
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
    reply_to_message_id: str | None = None,
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
                reply_to_message_id=reply_to_message_id,
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
    reply_to_message_id: str | None = None,
    interactive_buttons: list[tuple[str, str]] | None = None,
) -> str:
    key = _idempotency_key(event.id, purpose)
    outbound = db.scalar(select(WhatsappMessage).where(WhatsappMessage.idempotency_key == key))
    if (
        outbound
        and outbound.status_rank >= DELIVERY_STATUS_RANK["sent"]
        and outbound.meta_message_id
    ):
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
            message_type="interactive" if interactive_buttons else "text",
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
            status_rank=DELIVERY_STATUS_RANK["prepared"],
        )
        db.add(outbound)
        db.commit()
    outbound.status = "sending"
    outbound.status_rank = DELIVERY_STATUS_RANK["sending"]
    db.commit()
    try:
        provider = _messaging_provider(
            settings,
            meta_sender=meta_sender,
        )
        if isinstance(provider, MetaWhatsAppClient) and interactive_buttons:
            message_id = provider.send_interactive_buttons(
                phone,
                body=body,
                buttons=interactive_buttons,
                reply_to_message_id=reply_to_message_id,
            )
        elif isinstance(provider, MetaWhatsAppClient):
            message_id = provider.send_text(phone, body, reply_to_message_id=reply_to_message_id)
        else:
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
    outbound.status_rank = DELIVERY_STATUS_RANK["sent"]
    db.commit()
    return message_id


def _deliver_media_once(
    db: Session,
    *,
    event: WebhookEvent,
    purpose: str,
    phone: str,
    content: bytes,
    mime_type: str,
    filename: str,
    media_type: str,
    voice: bool,
    settings: Settings,
    tenant_id: str,
    user_id: str,
    meta_sender: tuple[str, str] | None,
    reply_to_message_id: str | None,
) -> str:
    key = _idempotency_key(event.id, purpose)
    outbound = db.scalar(select(WhatsappMessage).where(WhatsappMessage.idempotency_key == key))
    if (
        outbound
        and outbound.status_rank >= DELIVERY_STATUS_RANK["sent"]
        and outbound.meta_message_id
    ):
        return outbound.meta_message_id
    if outbound and outbound.status in {"sending", "ambiguous", "rejected"}:
        raise PermanentJobError("WhatsApp media delivery outcome requires reconciliation")
    if outbound is None:
        outbound = WhatsappMessage(
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=key,
            phone_e164=phone,
            direction="outbound",
            message_type=media_type,
            payload={
                "provider": settings.whatsapp_backend,
                "purpose": purpose,
                "mime_type": mime_type,
                "filename": filename,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            status="prepared",
            status_rank=DELIVERY_STATUS_RANK["prepared"],
        )
        db.add(outbound)
        db.commit()
    if settings.whatsapp_backend != "meta" or meta_sender is None:
        raise PermanentJobError("WhatsApp media delivery requires the configured Meta sender")
    outbound.status = "sending"
    outbound.status_rank = DELIVERY_STATUS_RANK["sending"]
    db.commit()
    client = MetaWhatsAppClient.for_inbound_reply(
        settings,
        waba_id=meta_sender[0],
        phone_number_id=meta_sender[1],
    )
    try:
        uploaded_id = outbound.payload.get("uploaded_media_id")
        if not isinstance(uploaded_id, str):
            uploaded_id = client.upload_media(
                content=content,
                mime_type=mime_type,
                filename=filename,
            )
            outbound.payload = {**outbound.payload, "uploaded_media_id": uploaded_id}
            db.commit()
        message_id = client.send_media(
            phone,
            media_type=media_type,
            media_id=uploaded_id,
            voice=voice,
            reply_to_message_id=reply_to_message_id,
        )
    except MetaProviderError as exc:
        ambiguous = exc.category in {"timeout", "transport"} or (
            exc.category == "provider" and exc.http_status is not None and exc.http_status >= 500
        )
        outbound.status = "ambiguous" if ambiguous else ("failed" if exc.retryable else "rejected")
        db.commit()
        if outbound.status in {"ambiguous", "rejected"}:
            raise PermanentJobError("WhatsApp media delivery cannot be retried safely") from exc
        raise
    outbound.meta_message_id = message_id
    outbound.status = "sent"
    outbound.status_rank = DELIVERY_STATUS_RANK["sent"]
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


def _generated_media_filename(media: GeneratedAgentMedia) -> str:
    extension = {
        "application/json": "json",
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
        "text/csv": "csv",
        "text/plain": "txt",
        "video/mp4": "mp4",
    }.get(media.mime_type, "bin")
    return f"bumpa-bestie-{media.id}.{extension}"


def _message_text(message: dict[str, Any]) -> str:
    message_type = str(message.get("type", "unknown"))
    if message_type == "text":
        value = message.get("text")
        return str(value.get("body", "")).strip() if isinstance(value, dict) else ""
    if message_type in {"image", "video", "document"}:
        value = message.get(message_type)
        return str(value.get("caption", "")).strip() if isinstance(value, dict) else ""
    if message_type == "button":
        value = message.get("button")
        return str(value.get("text", "")).strip() if isinstance(value, dict) else ""
    if message_type == "interactive":
        value = message.get("interactive")
        if isinstance(value, dict):
            for key in ("button_reply", "list_reply"):
                reply = value.get(key)
                if isinstance(reply, dict):
                    return str(reply.get("title") or reply.get("id") or "").strip()
    return ""


def _interactive_action(message: dict[str, Any]) -> tuple[str, str] | None:
    interactive = message.get("interactive")
    if not isinstance(interactive, dict):
        return None
    button = interactive.get("button_reply")
    if not isinstance(button, dict) or not isinstance(button.get("id"), str):
        return None
    match = re.fullmatch(
        r"agent_action:(confirm|deny):([0-9a-fA-F-]{36})",
        button["id"],
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _interactive_research_consent(
    message: dict[str, Any],
    settings: Settings,
) -> str | None:
    interactive = message.get("interactive")
    if not isinstance(interactive, dict):
        return None
    button = interactive.get("button_reply")
    if not isinstance(button, dict) or not isinstance(button.get("id"), str):
        return None
    match = re.fullmatch(
        r"research_consent:(grant|decline):([A-Za-z0-9][A-Za-z0-9._-]{0,31})",
        button["id"],
    )
    if not match:
        return None
    if match.group(2) != settings.research_consent_policy_version:
        return None
    return match.group(1)


def _voice_reply_requested(prompt: str) -> bool:
    return bool(
        re.search(
            r"\b(?:reply|respond|answer|send|give me)\b.{0,30}\b"
            r"(?:voice|voice note|audio)\b",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def _voice_reply_language(processed: Any) -> str:
    prompt = str(getattr(processed, "prompt", ""))
    explicit = (
        ("yo", ("yoruba",)),
        ("ig", ("igbo",)),
        ("ha", ("hausa",)),
        ("en", ("english", "pidgin")),
    )
    normalized = prompt.lower()
    for code, names in explicit:
        if any(re.search(rf"\b{re.escape(name)}\b", normalized) for name in names):
            return code
    detected = getattr(processed, "metadata", {}).get("language")
    if isinstance(detected, str) and re.fullmatch(r"[a-z]{2,3}", detected.lower()):
        return detected.lower()
    return "en"


def _low_confidence_clarification(processed: Any) -> str | None:
    probability = processed.metadata.get("language_probability")
    if not isinstance(probability, int | float) or probability >= 0.6:
        return None
    transcript = next(
        (
            part.get("text")
            for part in processed.stored_content_parts
            if isinstance(part, dict)
            and part.get("type") == "transcript"
            and isinstance(part.get("text"), str)
        ),
        None,
    )
    if not transcript:
        return (
            "I couldn’t transcribe that voice or video reliably. "
            "Please resend it more clearly or type the request."
        )
    language = processed.metadata.get("language")
    language_note = f" ({language})" if isinstance(language, str) and language else ""
    return (
        f"I heard{language_note}: “{transcript[:1_500]}”\n\n"
        "I’m not confident that transcription is accurate, so I haven’t acted on it or "
        "treated any names, amounts or instructions as fact. Please confirm or correct it."
    )


class _WhatsAppProgress:
    """Emit at most two real-tool-derived progress bubbles after the threshold."""

    def __init__(
        self,
        *,
        settings: Settings,
        event_id: str,
        inbound_message_id: str,
        phone: str,
        tenant_id: str,
        user_id: str,
        meta_sender: tuple[str, str] | None,
    ) -> None:
        self._settings = settings
        self._event_id = event_id
        self._inbound_message_id = inbound_message_id
        self._phone = phone
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._meta_sender = meta_sender
        self._done = Event()
        self._changed = Event()
        self._lock = Lock()
        self._latest: tuple[int, str] | None = None
        self._version = 0
        self._thread: Thread | None = None
        self._started_at = monotonic()

    def start(self) -> None:
        if (
            self._settings.whatsapp_backend != "meta"
            or not self._settings.whatsapp_progress_enabled
            or self._meta_sender is None
        ):
            return
        self._thread = Thread(
            target=self._run,
            name="whatsapp-progress",
            daemon=True,
        )
        self._thread.start()

    def on_event(self, name: str, data: dict[str, object]) -> None:
        if name != "tool.progress":
            return
        body = _progress_body(str(data.get("tool") or "approved_tool"))
        with self._lock:
            self._version += 1
            self._latest = (self._version, body)
        self._changed.set()

    def finish(self) -> None:
        self._done.set()
        self._changed.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)

    def _run(self) -> None:
        delay = max(
            0.0,
            self._settings.whatsapp_progress_after_seconds - (monotonic() - self._started_at),
        )
        if self._done.wait(delay):
            return
        typing_interval = min(
            20.0,
            max(5.0, self._settings.whatsapp_progress_after_seconds),
        )
        last_typing_at = 0.0
        last_version = 0
        for index in range(1, 3):
            while not self._done.is_set():
                current = monotonic()
                if current - last_typing_at >= typing_interval:
                    self._refresh_typing()
                    last_typing_at = current
                with self._lock:
                    latest = self._latest
                if latest is not None and latest[0] > last_version:
                    last_version = latest[0]
                    self._send(index, latest[1])
                    break
                self._changed.clear()
                self._changed.wait(timeout=0.5)
            if self._done.is_set():
                return
            if self._done.wait(self._settings.whatsapp_progress_after_seconds):
                return

    def _refresh_typing(self) -> None:
        assert self._meta_sender is not None
        try:
            MetaWhatsAppClient.for_inbound_reply(
                self._settings,
                waba_id=self._meta_sender[0],
                phone_number_id=self._meta_sender[1],
            ).mark_read(self._inbound_message_id, typing=True)
        except (MetaProviderError, ValueError):
            return

    def _send(self, index: int, body: str) -> None:
        assert self._meta_sender is not None
        try:
            client = MetaWhatsAppClient.for_inbound_reply(
                self._settings,
                waba_id=self._meta_sender[0],
                phone_number_id=self._meta_sender[1],
            )
            client.mark_read(self._inbound_message_id, typing=True)
            with SessionLocal() as progress_db:
                set_security_context(progress_db, tenant_id=self._tenant_id)
                progress_event = progress_db.get(WebhookEvent, self._event_id)
                if progress_event is None:
                    return
                _deliver_once(
                    progress_db,
                    event=progress_event,
                    purpose=f"tool-progress:{index}",
                    phone=self._phone,
                    body=body,
                    settings=self._settings,
                    tenant_id=self._tenant_id,
                    user_id=self._user_id,
                    meta_sender=self._meta_sender,
                    reply_to_message_id=self._inbound_message_id,
                )
        except (MetaProviderError, PermanentJobError, ValueError):
            return


def _progress_body(tool_name: str) -> str:
    normalized = tool_name.lower()
    if "research" in normalized or "web" in normalized:
        return "I’m checking current sources now."
    if any(value in normalized for value in ("business", "sales", "order", "product", "inventory")):
        return "I’m checking your store data now."
    if "connector" in normalized or any(
        value in normalized for value in ("drive", "sheet", "gmail", "calendar", "meta_ads")
    ):
        return "I’m checking the connected service now."
    if "sandbox" in normalized or "calculate" in normalized:
        return "I’m working through the calculation now."
    return "I’m working through the requested check now."
