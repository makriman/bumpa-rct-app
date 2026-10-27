"""Explicit authorized active-run fixtures for capability tests."""

from sqlalchemy import select

from app.db.models import AgentMessage, Conversation, TenantMembership
from app.services.agent_actions import create_action_context_token
from app.services.agent_execution import begin_run, finish_run


def active_context(db, settings, **claims):
    conversation = db.get(Conversation, claims["conversation_id"])
    if (
        conversation is None
        or conversation.tenant_id != claims["tenant_id"]
        or conversation.user_id != claims["user_id"]
    ):
        return create_action_context_token(settings, **claims)
    member = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == conversation.tenant_id,
            TenantMembership.user_id == conversation.user_id,
        )
    )
    if member is None:
        db.add(
            TenantMembership(
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
                role="owner",
                status="active",
            )
        )
    if conversation.active_run_id:
        from app.db.models import AgentExecutionRun

        finish_run(db, db.get(AgentExecutionRun, conversation.active_run_id), status="completed")
        db.flush()
    inbound = (
        db.get(AgentMessage, claims.get("initiating_message_id"))
        if claims.get("initiating_message_id")
        else None
    )
    if inbound is None:
        inbound = AgentMessage(
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            channel=conversation.channel,
            direction="inbound",
            content="Synthetic explicitly authorized request",
        )
        db.add(inbound)
        db.flush()
        claims["initiating_message_id"] = inbound.id
    run = begin_run(db, conversation, inbound, timeout_seconds=180)
    db.commit()
    return create_action_context_token(settings, execution_run_id=run.id, **claims)
