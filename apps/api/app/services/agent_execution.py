"""Application-owned run fencing. Tokens never create or resurrect a run."""

from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
)
from app.jobs.runtime import assert_current_job_lease, assert_job_lease, current_job_fence


class ExecutionDenied(RuntimeError):
    pass


def begin_run(
    db: Session, conversation: Conversation, inbound: AgentMessage, *, timeout_seconds: float
) -> AgentExecutionRun:
    assert_current_job_lease(db)
    if conversation.context_generation != 1 or inbound.direction != "inbound":
        raise ExecutionDenied("A reviewed conversation and initiating input are required")
    run = AgentExecutionRun(
        tenant_id=conversation.tenant_id,
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        initiating_message_id=inbound.id,
        expires_at=utcnow() + timedelta(seconds=timeout_seconds + 30),
        actor_identity_id=inbound.media_metadata.get("phone_identity_id"),
    )
    fence = current_job_fence()
    if fence is not None:
        run.job_id, run.job_claim_epoch, run.job_stale_lock_seconds = (
            fence.job_id,
            fence.claim_epoch,
            fence.stale_lock_seconds,
        )
    db.add(run)
    db.flush()
    claimed = db.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation.id,
            Conversation.tenant_id == conversation.tenant_id,
            Conversation.active_run_id.is_(None),
        )
        .values(active_run_id=run.id)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise ExecutionDenied("An earlier run requires completion or explicit reconciliation")
    db.refresh(conversation, attribute_names=["active_run_id"])
    return run


def require_active_run(
    db: Session,
    *,
    run_id: str | None,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    initiating_message_id: str | None,
    channel: str,
) -> AgentExecutionRun:
    # Every worker effect takes the job row before conversation/member rows.
    # Progress and recovery use the same order; inversion can deadlock a final
    # reply against its own progress thread or a resumed worker.
    assert_current_job_lease(db)
    reference = (
        db.get(AgentExecutionRun, run_id, populate_existing=True)
        if isinstance(run_id, str)
        else None
    )
    if reference is not None and reference.job_id and reference.job_claim_epoch is not None:
        assert_job_lease(
            db,
            job_id=reference.job_id,
            claim_epoch=reference.job_claim_epoch,
            stale_lock_seconds=reference.job_stale_lock_seconds or 300,
        )
    # Lock the conversation until the tool commits, so finishing/revoking the run
    # cannot race a costly generation/store effect. PostgreSQL is the authority.
    conversation = db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
            Conversation.channel == channel,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = (
        db.get(AgentExecutionRun, run_id, populate_existing=True)
        if isinstance(run_id, str)
        else None
    )
    member = db.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
        )
        .with_for_update()
    )
    tenant = db.get(Tenant, tenant_id, populate_existing=True)
    user = db.get(User, user_id, populate_existing=True)
    identity = (
        db.get(PhoneIdentity, run.actor_identity_id, populate_existing=True)
        if run and run.actor_identity_id
        else None
    )
    if (
        not member
        or not tenant
        or tenant.status != "active"
        or not user
        or user.status != "active"
        or (
            run
            and run.actor_identity_id
            and (
                not identity
                or identity.status != "approved"
                or identity.opt_out
                or identity.tenant_id != tenant_id
                or identity.user_id != user_id
            )
        )
    ):
        raise ExecutionDenied("The initiating actor is no longer authorized")
    if (
        not conversation
        or not run
        or conversation.context_generation != 1
        or conversation.status not in {"open", "voice"}
        or conversation.active_run_id != run.id
        or (
            run.tenant_id != tenant_id
            or run.user_id != user_id
            or run.conversation_id != conversation_id
            or run.initiating_message_id != initiating_message_id
            or run.status != "running"
            or run.expires_at.replace(tzinfo=UTC) <= utcnow()
        )
    ):
        raise ExecutionDenied("The initiating run is no longer active")
    return run


def finish_run(db: Session, run: AgentExecutionRun, *, status: str) -> None:
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.id == run.conversation_id, Conversation.tenant_id == run.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation and conversation.active_run_id == run.id:
        conversation.active_run_id = None
    run.status = status
    if status != "completed":
        for media in db.scalars(
            select(GeneratedAgentMedia).where(
                GeneratedAgentMedia.execution_run_id == run.id,
                GeneratedAgentMedia.status == "pending",
            )
        ):
            media.status = "failed"


def cancel_conversation(db: Session, conversation: Conversation) -> None:
    from app.db.models import PendingAgentAction

    current = db.scalar(
        select(Conversation).where(Conversation.id == conversation.id).with_for_update()
    )
    if current is None:
        return
    if current.active_run_id:
        run = db.get(AgentExecutionRun, current.active_run_id)
        if run is not None:
            finish_run(db, run, status="failed")
    current.pending_utterance = {}
    current.status = "closed"
    for action in db.scalars(
        select(PendingAgentAction).where(
            PendingAgentAction.conversation_id == current.id, PendingAgentAction.status == "pending"
        )
    ):
        action.status = "expired"
