from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import secret_hash
from app.core.rate_limit import (
    RateLimitExceeded,
    RateLimitUnavailable,
    consume_operation_rate_limit,
)
from app.core.time import utcnow
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    AsyncJob,
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
from app.jobs.runtime import (
    JobDeferredError,
    JobLeaseLostError,
    PermanentJobError,
    assert_current_job_lease,
    current_job_fence,
)
from app.providers.contracts import MessagingProvider
from app.providers.hermes import HermesProfileError, endpoint_for
from app.providers.local import LocalMessagingProvider
from app.providers.meta import MAX_TEXT_LENGTH, MetaProviderError, MetaWhatsAppClient
from app.services.agent_actions import (
    AgentActionError,
    action_preview,
    confirmation_token,
    decide_pending_action,
    validate_approval_scope,
)
from app.services.agent_execution import cancel_conversation
from app.services.chat import handle_chat, reply_for_input
from app.services.control_transcript import record_control_reply
from app.services.conversation_scope import receiver_scope
from app.services.generated_media import GeneratedMediaError, generated_media_path
from app.services.ingress_ordering import require_head
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
    if message:
        if event.arrival_sequence is not None:
            require_head(db, event)
            db.commit()  # sequence reservation is durable before slow preprocessing
        elif not settings.is_local:
            raise PermanentJobError("Legacy unordered receipt requires explicit reconciliation")
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
            return _process_delivery(db, event, delivery, settings)
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


def _process_delivery(
    db: Session, event: WebhookEvent, delivery: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    scope = receiver_scope(
        settings.app_env,
        extract_inbound_sender(event.payload) if settings.whatsapp_backend == "meta" else None,
    )
    message_id = str(delivery.get("id", ""))
    status = str(delivery.get("status", "unknown"))
    timestamp = str(delivery.get("timestamp", "unknown"))
    message = db.scalar(
        select(WhatsappMessage).where(
            WhatsappMessage.meta_message_id == message_id, WhatsappMessage.receiver_scope == scope
        )
    )
    existing = db.scalar(
        select(WhatsappDeliveryEvent).where(
            WhatsappDeliveryEvent.meta_message_id == message_id,
            WhatsappDeliveryEvent.receiver_scope == scope,
            WhatsappDeliveryEvent.status == status,
            WhatsappDeliveryEvent.event_timestamp == timestamp,
        )
    )
    if not existing:
        db.add(
            WhatsappDeliveryEvent(
                whatsapp_message_id=message.id if message else None,
                receiver_scope=scope,
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
    scope = receiver_scope(settings.app_env, reply_sender)
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
        select(WhatsappMessage).where(
            WhatsappMessage.meta_message_id == external_id, WhatsappMessage.receiver_scope == scope
        )
    )
    if not inbound:
        inbound = WhatsappMessage(
            tenant_id=identity.tenant_id if identity else None,
            user_id=identity.user_id if identity else None,
            idempotency_key=_idempotency_key(event.id, "inbound"),
            meta_message_id=external_id,
            receiver_scope=scope,
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
    _bind_inbound_job_tenant(db, event=event, tenant_id=tenant.id)
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
            Conversation.context_generation == 1,
            Conversation.receiver_waba_id == (reply_sender[0] if reply_sender else "mock"),
            Conversation.receiver_phone_id == (reply_sender[1] if reply_sender else "mock"),
            Conversation.environment_lane == settings.app_env,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    if text.lower() in {"/new", "/reset"}:
        if active_conversation:
            cancel_conversation(db, active_conversation)
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
                validate_approval_scope(
                    db,
                    action,
                    conversation_id=active_conversation.id if active_conversation else "",
                    channel="whatsapp",
                    actor_identity_id=identity.id,
                )
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
        select(AgentMessage)
        .join(Conversation, Conversation.id == AgentMessage.conversation_id)
        .where(
            AgentMessage.tenant_id == tenant.id,
            AgentMessage.user_id == user.id,
            Conversation.context_generation == 1,
            Conversation.receiver_waba_id == (reply_sender[0] if reply_sender else "mock"),
            Conversation.receiver_phone_id == (reply_sender[1] if reply_sender else "mock"),
            Conversation.environment_lane == settings.app_env,
            AgentMessage.channel == "whatsapp",
            AgentMessage.external_message_id == external_id,
        )
    )
    if existing_agent_message:
        outgoing = reply_for_input(
            db, tenant_id=tenant.id, user_id=user.id, inbound_id=existing_agent_message.id
        )
        if not outgoing:
            from app.services.run_reconciliation import ReconciliationAction
            from app.services.run_recovery import reconcile_application_run

            interrupted = db.scalar(
                select(AgentExecutionRun).where(
                    AgentExecutionRun.tenant_id == tenant.id,
                    AgentExecutionRun.user_id == user.id,
                    AgentExecutionRun.initiating_message_id == existing_agent_message.id,
                )
            )
            if interrupted is None:
                raise PermanentJobError("Interrupted input has no verified execution record")
            recovery_decision = reconcile_application_run(db, interrupted.id, settings)
            if recovery_decision.action in {
                ReconciliationAction.WAIT,
                ReconciliationAction.POLL_LATER,
            }:
                raise JobDeferredError("Earlier model outcome is still pending", delay_seconds=5)
            outgoing = reply_for_input(
                db, tenant_id=tenant.id, user_id=user.id, inbound_id=existing_agent_message.id
            )
            if outgoing is None or recovery_decision.action in {
                ReconciliationAction.REVIEW,
                ReconciliationAction.FAIL,
            }:
                raise PermanentJobError("Interrupted run requires explicit outcome review")
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
                    workspace=f"media-{hashlib.sha256(f'{scope}\0{external_id}'.encode()).hexdigest()[:24]}",
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
                    AgentMessage.user_id == user.id,
                    AgentMessage.conversation_id
                    == (active_conversation.id if active_conversation else ""),
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
        processed.metadata["phone_identity_id"] = identity.id
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
                receiver_waba_id=reply_sender[0] if reply_sender else "mock",
                receiver_phone_id=reply_sender[1] if reply_sender else "mock",
                environment_lane=settings.app_env,
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
                    receiver_waba_id=reply_sender[0] if reply_sender else "mock",
                    receiver_phone_id=reply_sender[1] if reply_sender else "mock",
                    environment_lane=settings.app_env,
                    external_message_id=external_id,
                    provider_content_parts=processed.provider_content_parts,
                    stored_content_parts=processed.stored_content_parts,
                    reply_to_external_message_id=reply_to_meta_message_id,
                    media_metadata=processed.metadata,
                    event_callback=progress.on_event,
                    whatsapp_identity=(
                        identity.id,
                        secret_hash(
                            "onboarding-existing-pilot:" + identity.phone_e164, settings.otp_secret
                        ),
                    ),
                    settings=settings,
                )
            finally:
                progress.finish()
    prior_plan = (outgoing.media_metadata or {}).get("delivery_plan")
    if isinstance(prior_plan, dict):
        voice_reply_requested = prior_plan.get("voice_requested") is True
        stored_language = prior_plan.get("voice_language")
        voice_reply_language = stored_language if isinstance(stored_language, str) else "en"
    else:
        outgoing.media_metadata = {
            **(outgoing.media_metadata or {}),
            "voice_reply_language": voice_reply_language,
        }
    _freeze_reply_manifest(
        db,
        event=event,
        outgoing=outgoing,
        include_voice=bool(
            voice_reply_requested
            and settings.whatsapp_speech_enabled
            and settings.whatsapp_backend == "meta"
        ),
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
        reply_to_message_id=external_id,
    )
    outgoing.media_metadata = {**outgoing.media_metadata, "delivery_status": "sent"}
    db.commit()
    voice_receipt = db.scalar(
        select(WhatsappMessage).where(
            WhatsappMessage.idempotency_key == _idempotency_key(event.id, "agent-voice-reply")
        )
    )
    voice_already_sent = bool(
        voice_receipt and voice_receipt.status_rank >= 3 and voice_receipt.meta_message_id
    )
    if voice_already_sent and outgoing.media_metadata.get("voice_artifact"):
        _clear_voice_cache(outgoing, settings)
        db.commit()
    if (
        voice_reply_requested
        and not voice_already_sent
        and settings.whatsapp_speech_enabled
        and settings.whatsapp_backend == "meta"
    ):
        try:
            voice_note = _cached_voice_reply(
                db,
                outgoing=outgoing,
                settings=settings,
                synthesize=lambda: synthesize_voice_note(
                    settings,
                    text=outgoing.content,
                    endpoint=speech_endpoint,
                    language=voice_reply_language,
                ),
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
            _clear_voice_cache(outgoing, settings)
            db.commit()
        except MetaProviderError as exc:
            db.commit()
            if (
                exc.retryable
                and exc.category not in {"timeout", "transport"}
                and (exc.http_status is None or exc.http_status < 500)
            ):
                raise JobDeferredError(
                    "Voice delivery is temporarily unavailable", delay_seconds=5
                ) from exc
            raise PermanentJobError("Voice delivery outcome requires reconciliation") from exc
        except PermanentJobError:
            raise
        except (MediaProcessingError, ValueError):
            failed_voice = db.scalar(
                select(WhatsappMessage).where(
                    WhatsappMessage.idempotency_key
                    == _idempotency_key(event.id, "agent-voice-reply")
                )
            )
            if failed_voice is not None and failed_voice.status not in {
                "prepared",
                "failed",
                "rejected",
            }:
                raise PermanentJobError("Voice delivery outcome requires reconciliation") from None
            if failed_voice is None:
                failed_voice = WhatsappMessage(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    direction="outbound",
                    phone_e164=phone,
                    message_type="audio",
                    receiver_scope=receiver_scope(settings.app_env, reply_sender),
                    idempotency_key=_idempotency_key(event.id, "agent-voice-reply"),
                    payload={
                        "purpose": "agent-voice-reply",
                        "error_code": "local_speech_unavailable",
                    },
                    status="rejected",
                    status_rank=0,
                )
                db.add(failed_voice)
            else:
                failed_voice.status = "rejected"
            outgoing.media_metadata = {
                **outgoing.media_metadata,
                "voice_delivery_outcome": "failed",
            }
            _clear_voice_cache(outgoing, settings)
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
    pending_actions = [
        action
        for action in pending_actions
        if (action.approval_binding or {}).get("initiating_message_id")
        == outgoing.initiating_message_id
    ]
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
        except MetaProviderError as exc:
            db.commit()
            if (
                exc.retryable
                and exc.category not in {"timeout", "transport"}
                and (exc.http_status is None or exc.http_status < 500)
            ):
                raise JobDeferredError(
                    "Generated media delivery is temporarily unavailable", delay_seconds=5
                ) from exc
            media.status = "failed"
            db.commit()
            if exc.category in {"timeout", "transport"} or (
                exc.http_status is not None and exc.http_status >= 500
            ):
                raise PermanentJobError(
                    "Generated media delivery outcome requires reconciliation"
                ) from exc
        except (
            GeneratedMediaError,
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


def _voice_cache_path(outgoing: AgentMessage, settings: Settings) -> Path:
    tenant_key = hashlib.sha256(outgoing.tenant_id.encode()).hexdigest()[:24]
    return settings.artifact_root.resolve() / "voice-replies" / tenant_key / f"{outgoing.id}.ogg"


def _cached_voice_reply(
    db: Session,
    *,
    outgoing: AgentMessage,
    settings: Settings,
    synthesize: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """Retain exact private audio across known-unsent retries; never resynthesize it."""
    path = _voice_cache_path(outgoing, settings)
    cache = outgoing.media_metadata.get("voice_artifact")
    if isinstance(cache, dict):
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise PermanentJobError("Prepared voice artifact requires reconciliation") from exc
        if len(content) != cache.get("size") or hashlib.sha256(content).hexdigest() != cache.get(
            "sha256"
        ):
            raise PermanentJobError("Prepared voice artifact integrity check failed")
        return {"content": content, "mime_type": cache["mime_type"], "filename": cache["filename"]}
    note = synthesize()
    raw_content = note.get("content")
    if not isinstance(raw_content, bytes) or not 0 < len(raw_content) <= 16_777_216:
        raise MediaProcessingError("The voice provider returned invalid media")
    content = raw_content
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        outgoing.media_metadata = {
            **outgoing.media_metadata,
            "voice_artifact": {
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mime_type": str(note["mime_type"]),
                "filename": str(note["filename"]),
            },
        }
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return note


def _clear_voice_cache(outgoing: AgentMessage, settings: Settings) -> None:
    _voice_cache_path(outgoing, settings).unlink(missing_ok=True)
    outgoing.media_metadata = {
        key: value for key, value in outgoing.media_metadata.items() if key != "voice_artifact"
    }


def _text_effect_keys(event_id: str, purpose: str, body: str) -> list[str]:
    chunks = _split_text(body)
    return [
        _idempotency_key(event_id, f"{purpose}:chunk:{index:04d}" if len(chunks) > 1 else purpose)
        for index in range(1, len(chunks) + 1)
    ]


def _freeze_reply_manifest(
    db: Session, *, event: WebhookEvent, outgoing: AgentMessage, include_voice: bool
) -> None:
    """Freeze the entire logical reply before the first transport submission."""
    run = db.scalar(
        select(AgentExecutionRun)
        .where(
            AgentExecutionRun.tenant_id == outgoing.tenant_id,
            AgentExecutionRun.user_id == outgoing.user_id,
            AgentExecutionRun.output_message_id == outgoing.id,
            AgentExecutionRun.initiating_message_id == outgoing.initiating_message_id,
        )
        .with_for_update()
    )
    if run is None:
        raise PermanentJobError("Reply has no verified execution binding")
    body_digest = hashlib.sha256(outgoing.content.encode()).hexdigest()
    prior_plan = (outgoing.media_metadata or {}).get("delivery_plan")
    if run.delivery_effect_keys:
        if not isinstance(prior_plan, dict) or prior_plan != {
            "version": 1,
            "event_id": event.id,
            "body_sha256": body_digest,
            "voice_requested": include_voice,
            "voice_language": outgoing.media_metadata.get("voice_reply_language"),
        }:
            raise PermanentJobError("Reply effects changed after delivery was planned")
        db.commit()
        return
    keys = _text_effect_keys(event.id, "agent-reply", outgoing.content)
    if include_voice:
        keys.append(_idempotency_key(event.id, "agent-voice-reply"))
    for media in db.scalars(
        select(GeneratedAgentMedia)
        .where(
            GeneratedAgentMedia.execution_run_id == run.id,
            GeneratedAgentMedia.agent_message_id == outgoing.id,
            GeneratedAgentMedia.channel == "whatsapp",
            GeneratedAgentMedia.status == "pending",
        )
        .order_by(GeneratedAgentMedia.id)
    ):
        keys.append(_idempotency_key(event.id, f"generated-media:{media.id}"))
    for action in db.scalars(
        select(PendingAgentAction)
        .where(
            PendingAgentAction.tenant_id == outgoing.tenant_id,
            PendingAgentAction.user_id == outgoing.user_id,
            PendingAgentAction.conversation_id == outgoing.conversation_id,
            PendingAgentAction.status == "pending",
        )
        .order_by(PendingAgentAction.id)
    ):
        if (action.approval_binding or {}).get(
            "initiating_message_id"
        ) != outgoing.initiating_message_id:
            continue
        keys.extend(
            _text_effect_keys(event.id, f"action-preview:{action.id}", action_preview(action))
        )
        keys.append(_idempotency_key(event.id, f"action-confirmation:{action.id}"))
    if len(keys) > 100 or len(set(keys)) != len(keys):
        raise PermanentJobError("Reply exceeds its bounded effect manifest")
    if run.delivery_effect_keys and run.delivery_effect_keys != keys:
        raise PermanentJobError("Reply effects changed after delivery was planned")
    run.delivery_effect_keys = keys
    outgoing.media_metadata = {
        **outgoing.media_metadata,
        "delivery_plan": {
            "version": 1,
            "event_id": event.id,
            "body_sha256": body_digest,
            "voice_requested": include_voice,
            "voice_language": outgoing.media_metadata.get("voice_reply_language"),
        },
    }
    db.commit()


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


def _bind_inbound_job_tenant(db: Session, *, event: WebhookEvent, tenant_id: str) -> None:
    """Bind only the current verified ingress claim before tenant-scoped effects.

    Ingress cannot assign a tenant before identifying and authorizing its sender.
    Progress and MCP use tenant-only sessions, so the worker must durably bind the
    job after that verification. Never broaden their RLS context or detach the
    shared revocation fence to hide a missing job.
    """
    fence = current_job_fence()
    if fence is None:
        return  # Inline local processing has no durable worker claim.
    assert_current_job_lease(db)
    job = db.get(AsyncJob, fence.job_id, populate_existing=True)
    if (
        job is None
        or job.kind != "whatsapp.process_webhook"
        or job.payload.get("event_id") != event.id
        or job.tenant_id not in {None, tenant_id}
    ):
        raise PermanentJobError("Verified ingress job scope does not match its claim")
    job.tenant_id = tenant_id
    # Release the claim-row lock before preprocessing/progress can renew or
    # validate it. Repeated admission preserves the same tenant and claim epoch.
    db.commit()


def assert_delivery_actor(
    db: Session, *, tenant_id: str | None, user_id: str | None, phone: str
) -> None:
    if not tenant_id or not user_id:
        return  # generic unknown-sender denial contains no business/private data
    identity = db.scalar(
        select(PhoneIdentity)
        .where(
            PhoneIdentity.tenant_id == tenant_id,
            PhoneIdentity.user_id == user_id,
            PhoneIdentity.phone_e164 == phone,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = db.get(User, user_id, populate_existing=True)
    tenant = db.get(Tenant, tenant_id, populate_existing=True)
    member = db.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
        )
        .with_for_update()
    )
    if (
        not identity
        or identity.status != "approved"
        or identity.opt_out
        or not user
        or user.status != "active"
        or not tenant
        or tenant.status != "active"
        or not member
    ):
        raise PermanentJobError("The receiving account is no longer authorized")


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
    if outbound and (
        (outbound.text_body is not None and outbound.text_body != body)
        or (outbound.phone_e164 is not None and outbound.phone_e164 != phone)
        or (
            outbound.receiver_scope
            and outbound.receiver_scope != receiver_scope(settings.app_env, meta_sender)
        )
    ):
        raise PermanentJobError("WhatsApp replay differs from its prepared effect")
    if (
        outbound
        and outbound.status_rank >= DELIVERY_STATUS_RANK["sent"]
        and outbound.meta_message_id
    ):
        return outbound.meta_message_id
    if outbound and outbound.status in {"sending", "ambiguous", "rejected"}:
        raise PermanentJobError("WhatsApp delivery outcome requires reconciliation")
    source = extract_message(event.payload) or {}
    control_reply = record_control_reply(
        db,
        settings=settings,
        tenant_id=tenant_id,
        user_id=user_id,
        source_id=str(source.get("id") or event.id),
        source_text=_message_text(source),
        body=body,
        purpose=purpose,
        transport_key=key,
        sender=meta_sender,
    )
    if not outbound:
        outbound = WhatsappMessage(
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=key,
            receiver_scope=receiver_scope(settings.app_env, meta_sender),
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
    assert_current_job_lease(db)
    outbound.status = "sending"
    outbound.status_rank = DELIVERY_STATUS_RANK["sending"]
    db.commit()
    try:
        assert_current_job_lease(db)
        assert_delivery_actor(db, tenant_id=tenant_id, user_id=user_id, phone=phone)
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
        outbound.payload = {
            **(outbound.payload or {}),
            "provider_error": _meta_provider_error_payload(exc),
        }
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
    if control_reply is not None:
        control_reply.media_metadata = {**control_reply.media_metadata, "delivery_status": "sent"}
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
    if outbound and (
        (
            outbound.payload.get("sha256")
            and outbound.payload["sha256"] != hashlib.sha256(content).hexdigest()
        )
        or (outbound.phone_e164 is not None and outbound.phone_e164 != phone)
        or (
            outbound.receiver_scope
            and outbound.receiver_scope != receiver_scope(settings.app_env, meta_sender)
        )
    ):
        raise PermanentJobError("WhatsApp media replay differs from its prepared effect")
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
            receiver_scope=receiver_scope(settings.app_env, meta_sender),
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
    assert_current_job_lease(db)
    outbound.status = "sending"
    outbound.status_rank = DELIVERY_STATUS_RANK["sending"]
    db.commit()
    assert_current_job_lease(db)
    assert_delivery_actor(db, tenant_id=tenant_id, user_id=user_id, phone=phone)
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
        assert_current_job_lease(db)
        assert_delivery_actor(db, tenant_id=tenant_id, user_id=user_id, phone=phone)
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
        outbound.payload = {
            **(outbound.payload or {}),
            "provider_error": _meta_provider_error_payload(exc),
        }
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


def _meta_provider_error_payload(exc: MetaProviderError) -> dict[str, object]:
    payload: dict[str, object] = {
        "category": exc.category,
        "retryable": exc.retryable,
    }
    if exc.http_status is not None:
        payload["http_status"] = exc.http_status
    if exc.provider_code is not None:
        payload["provider_code"] = exc.provider_code
    if exc.request_id_hash is not None:
        payload["request_id_hash"] = exc.request_id_hash
    if exc.retry_after_seconds is not None:
        payload["retry_after_seconds"] = exc.retry_after_seconds
    return payload


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
    """Emit at most two distinct tool updates and keep typing until completion."""

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
        self._latest: str | None = None
        self._thread: Thread | None = None
        self._started_at = monotonic()

    def start(self) -> None:
        if (
            self._settings.whatsapp_backend != "meta"
            or not self._settings.whatsapp_progress_enabled
            or self._meta_sender is None
        ):
            return
        context = copy_context()
        self._thread = Thread(
            target=lambda: context.run(self._run),
            name="whatsapp-progress",
            daemon=True,
        )
        self._thread.start()

    def on_event(self, name: str, data: dict[str, object]) -> None:
        if name != "tool.progress":
            return
        body = _progress_body(str(data.get("tool") or "approved_tool"))
        with self._lock:
            self._latest = body
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
        sent_bodies: set[str] = set()
        next_message_at = 0.0
        while not self._done.is_set():
            current = monotonic()
            if current - last_typing_at >= typing_interval:
                self._refresh_typing()
                last_typing_at = current
            with self._lock:
                latest = self._latest
                self._changed.clear()
            if (
                latest is not None
                and latest not in sent_bodies
                and len(sent_bodies) < 2
                and current >= next_message_at
                and not self._done.is_set()
            ):
                sent_bodies.add(latest)
                self._send(latest)
                next_message_at = monotonic() + self._settings.whatsapp_progress_after_seconds
            self._changed.wait(timeout=0.5)

    def _refresh_typing(self) -> None:
        assert self._meta_sender is not None
        try:
            with SessionLocal() as progress_db:
                set_security_context(progress_db, tenant_id=self._tenant_id)
                assert_current_job_lease(progress_db)
                assert_delivery_actor(
                    progress_db, tenant_id=self._tenant_id, user_id=self._user_id, phone=self._phone
                )
                MetaWhatsAppClient.for_inbound_reply(
                    self._settings,
                    waba_id=self._meta_sender[0],
                    phone_number_id=self._meta_sender[1],
                ).mark_read(self._inbound_message_id, typing=True)
        except (MetaProviderError, PermanentJobError, JobLeaseLostError, ValueError):
            self._done.set()
            return

    def _send(self, body: str) -> None:
        if self._done.is_set():
            return
        assert self._meta_sender is not None
        try:
            with SessionLocal() as progress_db:
                set_security_context(progress_db, tenant_id=self._tenant_id)
                assert_current_job_lease(progress_db)
                assert_delivery_actor(
                    progress_db, tenant_id=self._tenant_id, user_id=self._user_id, phone=self._phone
                )
                if self._done.is_set():
                    return
                MetaWhatsAppClient.for_inbound_reply(
                    self._settings,
                    waba_id=self._meta_sender[0],
                    phone_number_id=self._meta_sender[1],
                ).mark_read(self._inbound_message_id, typing=True)
                # A slow provider read request can finish after the final answer.
                if self._done.is_set():
                    return
                progress_event = progress_db.get(WebhookEvent, self._event_id)
                if progress_event is None:
                    return
                _deliver_once(
                    progress_db,
                    event=progress_event,
                    # Stable across tool start/completion events and worker retries,
                    # even when those events arrive in a different order.
                    purpose=f"tool-progress:{hashlib.sha256(body.encode()).hexdigest()}",
                    phone=self._phone,
                    body=body,
                    settings=self._settings,
                    tenant_id=self._tenant_id,
                    user_id=self._user_id,
                    meta_sender=self._meta_sender,
                    reply_to_message_id=self._inbound_message_id,
                )
        except (MetaProviderError, PermanentJobError, JobLeaseLostError, ValueError):
            self._done.set()
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
