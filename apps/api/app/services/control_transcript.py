"""Record authorized deterministic transport replies in the same visible ledger."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import AgentMessage, Conversation, Tenant, User
from app.providers.redaction import redact_text
from app.services.conversation_scope import receiver_scope


def record_control_reply(
    db: Session,
    *,
    settings: Settings,
    tenant_id: str | None,
    user_id: str | None,
    source_id: str,
    source_text: str,
    body: str,
    purpose: str,
    transport_key: str,
    sender: tuple[str, str] | None,
) -> AgentMessage | None:
    if not tenant_id or not user_id or not db.get(Tenant, tenant_id) or not db.get(User, user_id):
        return None
    # Model replies already have an app row; progress is a transport status rather
    # than merchant dialogue. No synthetic user turns or duplicate chunks.
    if purpose.startswith(("agent-reply", "agent-progress")):
        return None
    prior = db.scalar(
        select(AgentMessage).where(
            AgentMessage.tenant_id == tenant_id,
            AgentMessage.user_id == user_id,
            AgentMessage.media_metadata["transport_key"].as_string() == transport_key,
        )
    )
    if prior:
        return prior
    waba, phone = sender or ("mock", "mock")
    scope = receiver_scope(settings.app_env, (waba, phone))
    inbound = db.scalar(
        select(AgentMessage).where(
            AgentMessage.tenant_id == tenant_id,
            AgentMessage.user_id == user_id,
            AgentMessage.channel == "whatsapp",
            AgentMessage.direction == "inbound",
            AgentMessage.event_scope == scope,
            AgentMessage.external_message_id == source_id,
        )
    )
    conversation = (
        db.get(Conversation, inbound.conversation_id)
        if inbound
        else db.scalar(
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
                Conversation.channel == "whatsapp",
                Conversation.context_generation == 1,
                Conversation.status == "open",
                Conversation.environment_lane == settings.app_env,
                Conversation.receiver_waba_id == waba,
                Conversation.receiver_phone_id == phone,
            )
            .order_by(Conversation.updated_at.desc())
        )
    )
    if conversation is None:
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            channel="whatsapp",
            context_generation=1,
            receiver_waba_id=waba,
            receiver_phone_id=phone,
            environment_lane=settings.app_env,
            title=source_text[:80],
        )
        db.add(conversation)
        db.flush()
    if inbound is None:
        inbound = AgentMessage(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation.id,
            channel="whatsapp",
            direction="inbound",
            content=source_text or "[Transport control event]",
            redacted_content=redact_text(source_text),
            external_message_id=source_id,
            event_scope=scope,
        )
        db.add(inbound)
        db.flush()
    reply = AgentMessage(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation.id,
        channel="whatsapp",
        direction="outbound",
        initiating_message_id=inbound.id,
        content=body,
        redacted_content=redact_text(body),
        media_metadata={
            "event_kind": "control",
            "control_purpose": purpose,
            "transport_key": transport_key,
            "delivery_status": "prepared",
        },
    )
    db.add(reply)
    return reply
