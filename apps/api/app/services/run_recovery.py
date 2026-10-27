"""Fenced database application of interrupted-run reconciliation decisions.

Call with a worker-owned session: this service commits the claim before provider
polling and commits the atomic result afterwards. It never submits a provider
request, executes a tool, stops a run, or sends a transport message.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
    HermesProfile,
    OperationalAgentEvent,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    UsageEvent,
    User,
    WhatsappMessage,
)
from app.jobs.runtime import JobLeaseLostError, assert_current_job_lease
from app.providers.hermes import (
    _CITATION_REPAIR_PROMPT,
    SME_SYSTEM_POLICY,
    HermesClient,
    HermesEndpoint,
    HermesError,
    _required_citation_count,
    endpoint_for,
)
from app.providers.redaction import redact_text
from app.services.conversation_scope import receiver_scope
from app.services.run_reconciliation import (
    DeliveryOutcome,
    DeliveryReceipt,
    DurableModelOutput,
    FrozenRequest,
    ModelOutcome,
    ReconciliationAction,
    ReconciliationDecision,
    RunSnapshot,
    delivery_outcome,
    reconcile_run,
)

ProviderPoll = Callable[[HermesEndpoint, str], object]


@dataclass(frozen=True, slots=True)
class _Claim:
    run_id: str
    epoch: int
    expires_at: datetime
    active_run_id: str | None
    retained_requests: str | None
    snapshot: RunSnapshot
    profile_id: str
    endpoint: HermesEndpoint | None
    durable_model_failed: bool


class _UnsafeRecovery(RuntimeError):
    """Only constant, non-secret reason codes are used as messages."""


def reconcile_application_run(
    db: Session,
    run_id: str,
    settings: Settings,
    now: datetime | None = None,
    poll_override: ProviderPoll | None = None,
) -> ReconciliationDecision:
    try:
        return _reconcile_application_run(db, run_id, settings, now, poll_override)
    except JobLeaseLostError:
        # A failed final fence must not leave flushed output/retirement changes
        # available for an unrelated caller to commit after catching the error.
        db.rollback()
        raise


def _reconcile_application_run(
    db: Session,
    run_id: str,
    settings: Settings,
    now: datetime | None,
    poll_override: ProviderPoll | None,
) -> ReconciliationDecision:
    clock_started = monotonic()
    moment = _as_utc(now or utcnow())
    # This is the recovery worker's current lease, not the expired originating
    # job's lease. Recovery is expressly allowed after that old job is revoked.
    assert_current_job_lease(db)
    run, conversation = _lock_run(db, run_id)
    if run is None:
        db.rollback()
        return _review("application_run_missing")
    if run.status in {"running", "reconciling"} and _as_utc(run.expires_at) > moment:
        db.rollback()
        return ReconciliationDecision(
            ReconciliationAction.WAIT,
            ModelOutcome.PENDING,
            DeliveryOutcome.UNKNOWN,
            "active_run_not_expired",
        )
    try:
        inbound, profile = _authorized_context(db, run, conversation, settings)
        durable = _durable_output(db, run, inbound, profile)
        if durable is None and (conversation is None or conversation.active_run_id != run.id):
            # A cleared pointer cannot prove that no newer turn completed. Do
            # not infer conversation order from timestamps or resurrect an old
            # run after its ordering anchor has been explicitly retired.
            raise _UnsafeRecovery("active_run_binding_invalid")
        snapshot = _snapshot(db, run, conversation, inbound, durable)
        endpoint: HermesEndpoint | None = None
        if durable is None:
            request = _validated_requests(run, inbound, profile, settings)
            snapshot = replace(snapshot, request_snapshot=request)
            endpoint = endpoint_for(profile, settings)
            # Apply the same private-boundary validation used by poll_run even
            # when a test or operator supplies an injected read-only poller.
            client = HermesClient(settings)
            client._validate_endpoint_identity(endpoint)
            client._validated_base_url(endpoint.api_url)
        if conversation is None:
            raise _UnsafeRecovery("conversation_missing")
        previous_epoch = run.reconcile_epoch
        previous_status = run.status
        active_run_id = conversation.active_run_id
        retained = run.request_snapshots_encrypted
        lease_seconds = min(3600, max(60, settings.hermes_read_timeout_seconds * 2 + 30))
        claim_expires_at = _elapsed_moment(moment, clock_started) + timedelta(seconds=lease_seconds)
        assert_current_job_lease(db)
        epoch = db.scalar(
            update(AgentExecutionRun)
            .where(
                AgentExecutionRun.id == run.id,
                AgentExecutionRun.reconcile_epoch == previous_epoch,
                AgentExecutionRun.status == previous_status,
            )
            .values(
                reconcile_epoch=AgentExecutionRun.reconcile_epoch + 1,
                status="reconciling",
                expires_at=claim_expires_at,
            )
            .returning(AgentExecutionRun.reconcile_epoch)
            .execution_options(synchronize_session=False)
        )
        if epoch is None:
            db.rollback()
            return _review("reconciliation_claim_lost")
        claim = _Claim(
            run.id,
            epoch,
            claim_expires_at,
            active_run_id,
            retained,
            snapshot,
            profile.id,
            endpoint,
            durable is not None and _durable_model_failed(db, durable, previous_status),
        )
        db.commit()
    except _UnsafeRecovery as exc:
        decision = _review(str(exc))
        _retire(db, run, conversation, decision, _elapsed_moment(moment, clock_started))
        db.commit()
        return decision
    except (ValueError, TypeError, KeyError, HermesError):
        decision = _review("recovery_snapshot_invalid")
        _retire(db, run, conversation, decision, _elapsed_moment(moment, clock_started))
        db.commit()
        return decision

    # No transaction or database lock is retained during network polling.
    def poll(provider_id: str) -> object:
        if claim.endpoint is None:
            raise _UnsafeRecovery("provider_endpoint_unavailable")
        if poll_override is not None:
            return poll_override(claim.endpoint, provider_id)
        return HermesClient(settings).poll_run(claim.endpoint, provider_id)

    decision = reconcile_run(claim.snapshot, now=moment, poll=poll)
    assert_current_job_lease(db)
    run, conversation = _lock_run(db, claim.run_id)
    if (
        run is None
        or conversation is None
        or run.reconcile_epoch != claim.epoch
        or run.status != "reconciling"
        or conversation.active_run_id != claim.active_run_id
        or tuple(run.provider_run_ids or ()) != claim.snapshot.provider_run_ids
        or run.request_snapshots_encrypted != claim.retained_requests
    ):
        db.rollback()
        return _review("reconciliation_claim_lost")
    applied_at = _elapsed_moment(moment, clock_started)
    if _claim_expired(run, claim, applied_at):
        db.rollback()
        return _review("reconciliation_claim_expired")
    try:
        inbound, profile = _authorized_context(db, run, conversation, settings)
        if profile.id != claim.profile_id:
            raise _UnsafeRecovery("provider_profile_changed")
        durable = _durable_output(db, run, inbound, profile)
        if claim.snapshot.durable_output is None:
            _validated_requests(run, inbound, profile, settings)
        current = _snapshot(db, run, conversation, inbound, durable)
        # Delivery receipts may advance while Hermes is polled. Recheck them
        # before returning a delivery decision; never replace an accepted reply.
        if durable is not None:
            decision = reconcile_run(current, now=applied_at, poll=_no_poll)
            if claim.durable_model_failed or _durable_model_failed(db, durable, run.status):
                # A committed outage fallback can still need delivery, but its
                # accepted text is not proof that Hermes generated an answer.
                decision = replace(decision, model_outcome=ModelOutcome.FAILED)
        elif decision.action == ReconciliationAction.PERSIST_OUTPUT and decision.output is not None:
            recovered = decision.output
            outbound = AgentMessage(
                tenant_id=run.tenant_id,
                user_id=run.user_id,
                hermes_profile_id=profile.id,
                conversation_id=run.conversation_id,
                channel=inbound.channel,
                direction="outbound",
                initiating_message_id=inbound.id,
                event_scope=inbound.event_scope,
                content=recovered.content,
                content_parts=[{"type": "text", "text": recovered.content}],
                redacted_content=redact_text(recovered.content),
                media_metadata={
                    "event_kind": "reply",
                    "execution_run_id": run.id,
                    "recovered": True,
                    "model_outcome": "completed",
                    "provider_run_id": recovered.provider_run_id,
                    "input_tokens": recovered.input_tokens,
                    "output_tokens": recovered.output_tokens,
                    "total_tokens": recovered.total_tokens,
                    **(
                        {"type": "voice_call", "voice_playback_state": "generated"}
                        if inbound.media_metadata.get("type") == "voice_call"
                        else {}
                    ),
                },
                created_at=applied_at,
            )
            db.add(outbound)
            db.flush()
            run.output_message_id = outbound.id
            for media in db.scalars(
                select(GeneratedAgentMedia)
                .where(
                    GeneratedAgentMedia.execution_run_id == run.id,
                    GeneratedAgentMedia.agent_message_id == inbound.id,
                    GeneratedAgentMedia.tenant_id == run.tenant_id,
                    GeneratedAgentMedia.user_id == run.user_id,
                    GeneratedAgentMedia.conversation_id == run.conversation_id,
                    GeneratedAgentMedia.channel == inbound.channel,
                    GeneratedAgentMedia.status == "pending",
                )
                .with_for_update()
            ):
                media.agent_message_id = outbound.id
            db.add(
                UsageEvent(
                    tenant_id=run.tenant_id,
                    user_id=run.user_id,
                    event_name="chat.response",
                    units=Decimal(recovered.total_tokens),
                    event_metadata={
                        "provider": "hermes",
                        "execution_run_id": run.id,
                        "recovered": True,
                        "input_tokens": recovered.input_tokens,
                        "output_tokens": recovered.output_tokens,
                        "total_tokens": recovered.total_tokens,
                    },
                )
            )
            conversation.updated_at = applied_at
            decision = replace(decision, delivery_outcome=delivery_outcome(current))
        elif run.output_message_id:
            raise _UnsafeRecovery("durable_output_binding_invalid")
        if durable is not None:
            run.output_message_id = durable.message_id
            accepted = db.get(AgentMessage, durable.message_id)
            if accepted is not None:
                accepted.media_metadata = {
                    **accepted.media_metadata,
                    "model_outcome": decision.model_outcome.value,
                }
        applied_at = _elapsed_moment(moment, clock_started)
        if _claim_expired(run, claim, applied_at):
            db.rollback()
            return _review("reconciliation_claim_expired")
        _retire(db, run, conversation, decision, applied_at)
        db.commit()
        return decision
    except _UnsafeRecovery as exc:
        decision = _review(str(exc))
        applied_at = _elapsed_moment(moment, clock_started)
        if _claim_expired(run, claim, applied_at):
            db.rollback()
            return _review("reconciliation_claim_expired")
        _retire(db, run, conversation, decision, applied_at)
        db.commit()
        return decision


def reconcile_expired_runs(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    limit: int = 50,
    poll_override: ProviderPoll | None = None,
) -> tuple[ReconciliationDecision, ...]:
    """Reconcile a fixed bounded selection; unknown outcomes require explicit review."""
    if not 1 <= limit <= 100:
        raise ValueError("Run recovery batch limit must be between one and one hundred")
    clock_started = monotonic()
    moment = _as_utc(now or utcnow())
    ids = tuple(
        db.scalars(
            select(AgentExecutionRun.id)
            .where(
                AgentExecutionRun.status.in_(("running", "expired", "reconciling")),
                AgentExecutionRun.expires_at <= moment,
            )
            .order_by(AgentExecutionRun.expires_at, AgentExecutionRun.id)
            .limit(limit)
        )
    )
    db.commit()
    return tuple(
        reconcile_application_run(
            db, run_id, settings, _elapsed_moment(moment, clock_started), poll_override
        )
        for run_id in ids
    )


def _lock_run(db: Session, run_id: str) -> tuple[AgentExecutionRun | None, Conversation | None]:
    coordinates = db.execute(
        select(AgentExecutionRun.conversation_id, AgentExecutionRun.tenant_id).where(
            AgentExecutionRun.id == run_id
        )
    ).first()
    if coordinates is None:
        return None, None
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.id == coordinates[0], Conversation.tenant_id == coordinates[1])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = db.scalar(
        select(AgentExecutionRun)
        .where(AgentExecutionRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return run, conversation


def _authorized_context(
    db: Session, run: AgentExecutionRun, conversation: Conversation | None, settings: Settings
) -> tuple[AgentMessage, HermesProfile]:
    if (
        conversation is None
        or conversation.tenant_id != run.tenant_id
        or conversation.user_id != run.user_id
        or conversation.context_generation != 1
        or conversation.status not in {"open", "voice"}
        or conversation.environment_lane != settings.app_env
    ):
        raise _UnsafeRecovery("conversation_scope_inactive")
    if conversation.active_run_id not in {None, run.id}:
        raise _UnsafeRecovery("replacement_run_active")
    tenant = _locked(db, Tenant, run.tenant_id)
    user = _locked(db, User, run.user_id)
    member = db.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == run.tenant_id,
            TenantMembership.user_id == run.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not tenant
        or tenant.status != "active"
        or not user
        or user.status != "active"
        or not member
        or member.status != "active"
    ):
        raise _UnsafeRecovery("initiating_actor_revoked")
    inbound = _locked(db, AgentMessage, run.initiating_message_id)
    if (
        inbound is None
        or inbound.direction != "inbound"
        or inbound.tenant_id != run.tenant_id
        or inbound.user_id != run.user_id
        or inbound.conversation_id != run.conversation_id
        or inbound.channel != conversation.channel
        or inbound.event_scope
        != receiver_scope(
            conversation.environment_lane,
            (conversation.receiver_waba_id, conversation.receiver_phone_id),
        )
    ):
        raise _UnsafeRecovery("initiating_message_scope_invalid")
    if inbound.media_metadata.get("phone_identity_id") != run.actor_identity_id:
        raise _UnsafeRecovery("initiating_identity_binding_invalid")
    if run.actor_identity_id:
        identity = _locked(db, PhoneIdentity, run.actor_identity_id)
        if (
            not identity
            or identity.status != "approved"
            or identity.opt_out
            or identity.tenant_id != run.tenant_id
            or identity.user_id != run.user_id
        ):
            raise _UnsafeRecovery("initiating_actor_revoked")
    elif inbound.channel == "whatsapp":
        raise _UnsafeRecovery("initiating_identity_missing")
    if (
        inbound.channel == "whatsapp"
        and settings.whatsapp_backend == "meta"
        and (conversation.receiver_waba_id, conversation.receiver_phone_id)
        not in settings.allowed_meta_inbound_reply_senders
    ):
        raise _UnsafeRecovery("receiver_scope_revoked")
    profile = db.scalar(
        select(HermesProfile)
        .where(HermesProfile.tenant_id == run.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not profile
        or profile.status != "active"
        or profile.provider != "hermes"
        or inbound.hermes_profile_id != profile.id
    ):
        raise _UnsafeRecovery("provider_profile_inactive")
    return inbound, profile


def _locked(db: Session, model: Any, identity: str) -> Any:
    return db.scalar(
        select(model)
        .where(model.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _durable_output(
    db: Session, run: AgentExecutionRun, inbound: AgentMessage, profile: HermesProfile
) -> DurableModelOutput | None:
    if run.output_message_id:
        rows = [_locked(db, AgentMessage, run.output_message_id)]
    else:
        rows = list(
            db.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.initiating_message_id == inbound.id,
                    AgentMessage.direction == "outbound",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
    if not rows:
        return None
    if len(rows) != 1:
        raise _UnsafeRecovery("durable_output_duplicate")
    output = rows[0]
    if (
        not output
        or output.tenant_id != run.tenant_id
        or output.user_id != run.user_id
        or output.conversation_id != run.conversation_id
        or output.initiating_message_id != inbound.id
        or output.hermes_profile_id != profile.id
        or output.direction != "outbound"
        or output.channel != inbound.channel
        or output.media_metadata.get("event_kind") != "reply"
        or not output.content.strip()
        or output.media_metadata.get("execution_run_id", run.id) != run.id
    ):
        raise _UnsafeRecovery("durable_output_binding_invalid")
    return DurableModelOutput(output.id, run.id, inbound.id, output.content)


def _snapshot(
    db: Session,
    run: AgentExecutionRun,
    conversation: Conversation | None,
    inbound: AgentMessage,
    durable: DurableModelOutput | None,
) -> RunSnapshot:
    keys = tuple(run.delivery_effect_keys or ())
    if len(keys) > 100 or any(not isinstance(key, str) or not key for key in keys):
        raise _UnsafeRecovery("delivery_manifest_invalid")
    receipts = []
    for receipt in db.scalars(
        select(WhatsappMessage).where(WhatsappMessage.idempotency_key.in_(keys))
    ):
        if (
            not conversation
            or receipt.tenant_id != run.tenant_id
            or receipt.user_id != run.user_id
            or receipt.direction != "outbound"
            or receipt.receiver_scope != inbound.event_scope
        ):
            raise _UnsafeRecovery("delivery_receipt_scope_invalid")
        receipts.append(
            DeliveryReceipt(receipt.idempotency_key or "", receipt.status, receipt.meta_message_id)
        )
    return RunSnapshot(
        run_id=run.id,
        initiating_message_id=inbound.id,
        status="reconciling",
        expires_at=run.expires_at,
        provider_run_ids=tuple(run.provider_run_ids or ()),
        durable_output=durable,
        delivery_required=inbound.channel == "whatsapp",
        delivery_effect_keys=keys or None,
        deliveries=tuple(receipts),
        minimum_citations=_required_citation_count(inbound.content),
    )


def _durable_model_failed(db: Session, durable: DurableModelOutput, run_status: str) -> bool:
    output = db.get(AgentMessage, durable.message_id)
    return run_status == "failed" or bool(
        output and output.media_metadata.get("model_outcome") == "failed"
    )


def _validated_requests(
    run: AgentExecutionRun, inbound: AgentMessage, profile: HermesProfile, settings: Settings
) -> FrozenRequest:
    try:
        if not run.request_snapshots_encrypted:
            raise _UnsafeRecovery("frozen_request_missing")
        retained = json.loads(
            FieldCipher.from_settings(settings).decrypt(run.request_snapshots_encrypted)
        )
        if not isinstance(retained, list) or not 1 <= len(retained) <= 2:
            raise _UnsafeRecovery("frozen_request_invalid")
        if len(retained) != len(run.provider_run_ids or ()):
            raise _UnsafeRecovery("provider_admission_snapshot_mismatch")
        first_payload: dict[str, Any] | None = None
        final: FrozenRequest | None = None
        for index, record in enumerate(retained):
            if not isinstance(record, dict) or record.get("recoverable") is not True:
                raise _UnsafeRecovery("frozen_request_not_retained")
            serialized, digest = record.get("payload_json"), record.get("sha256")
            if not isinstance(serialized, str) or not isinstance(digest, str):
                raise _UnsafeRecovery("frozen_request_integrity_invalid")
            frozen = FrozenRequest(serialized, digest)
            if not frozen.is_valid():
                raise _UnsafeRecovery("frozen_request_integrity_invalid")
            envelope = json.loads(frozen.payload_json)
            payload = envelope.get("payload")
            headers = envelope.get("headers")
            if envelope.get("endpoint") != profile.api_internal_url.rstrip("/"):
                raise _UnsafeRecovery("provider_endpoint_changed")
            if not isinstance(payload, dict) or payload.get("model") != profile.profile_name:
                raise _UnsafeRecovery("provider_profile_changed")
            expected_key = (
                "bb_"
                + hashlib.sha256(inbound.id.encode()).hexdigest()
                + ("_citation" if index else "")
            )
            if not isinstance(headers, dict) or headers != {"Idempotency-Key": expected_key}:
                raise _UnsafeRecovery("provider_request_identity_invalid")
            instructions = payload.get("instructions")
            if not isinstance(instructions, str) or not instructions.startswith(
                SME_SYSTEM_POLICY + "\n\nCurrent application context:\n"
            ):
                raise _UnsafeRecovery("frozen_output_policy_unknown")
            if not isinstance(payload.get("input"), list) or not isinstance(
                payload.get("conversation_history"), list
            ):
                raise _UnsafeRecovery("frozen_request_invalid")
            inputs = payload["input"]
            if (
                len(inputs) != 1
                or not isinstance(inputs[0], dict)
                or inputs[0].get("role") != "user"
            ):
                raise _UnsafeRecovery("frozen_input_binding_invalid")
            content = inputs[0].get("content")
            expected_texts = (inbound.content, redact_text(inbound.content))
            if isinstance(content, str):
                if content != expected_texts[1]:
                    raise _UnsafeRecovery("frozen_input_binding_invalid")
            elif (
                not isinstance(content, list)
                or not content
                or not isinstance(content[0], dict)
                or content[0].get("type") != "text"
                or content[0].get("text") not in expected_texts
            ):
                raise _UnsafeRecovery("frozen_input_binding_invalid")
            if "session_id" in payload or "previous_response_id" in payload:
                raise _UnsafeRecovery("frozen_context_contract_unknown")
            if index and first_payload is not None:
                required = _required_citation_count(inbound.content)
                repair_prefix = (
                    str(first_payload["instructions"])
                    + "\nInternal citation repair; the draft was never shown to the merchant.\n"
                    + _CITATION_REPAIR_PROMPT.format(required=required)
                    + "\nUnverified candidate answer to repair:\n"
                )
                if (
                    not required
                    or not instructions.startswith(repair_prefix)
                    or payload.get("input") != first_payload.get("input")
                    or payload.get("conversation_history")
                    != first_payload.get("conversation_history")
                ):
                    raise _UnsafeRecovery("frozen_repair_policy_unknown")
            first_payload = first_payload or payload
            final = frozen
        if final is None:
            raise _UnsafeRecovery("frozen_request_invalid")
        return final
    except _UnsafeRecovery:
        raise
    except (ValueError, TypeError, KeyError):
        raise _UnsafeRecovery("frozen_request_invalid") from None


def _retire(
    db: Session,
    run: AgentExecutionRun,
    conversation: Conversation | None,
    decision: ReconciliationDecision,
    moment: datetime,
) -> None:
    assert_current_job_lease(db)
    action = decision.action
    if action == ReconciliationAction.POLL_LATER:
        run.status = "expired"
        run.expires_at = moment + timedelta(seconds=30)
    else:
        run.status = (
            "unknown"
            if action == ReconciliationAction.REVIEW
            else (
                "failed"
                if action == ReconciliationAction.FAIL
                or decision.model_outcome == ModelOutcome.FAILED
                else "completed"
            )
        )
        run.expires_at = moment
        if conversation and conversation.active_run_id == run.id:
            conversation.active_run_id = None
    if action in {ReconciliationAction.REVIEW, ReconciliationAction.FAIL}:
        for media in db.scalars(
            select(GeneratedAgentMedia)
            .where(
                GeneratedAgentMedia.execution_run_id == run.id,
                GeneratedAgentMedia.status == "pending",
            )
            .with_for_update()
        ):
            media.status = "failed"
    db.add(
        OperationalAgentEvent(
            tenant_id=run.tenant_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            channel=conversation.channel if conversation else "system",
            event_type="run_reconciliation",
            status=run.status,
            error_code=decision.reason[:80],
            event_metadata={
                "execution_run_id": run.id,
                "reconcile_epoch": run.reconcile_epoch,
                "model_outcome": decision.model_outcome.value,
                "delivery_outcome": decision.delivery_outcome.value,
                "action": action.value,
            },
        )
    )


def _review(reason: str) -> ReconciliationDecision:
    return ReconciliationDecision(
        ReconciliationAction.REVIEW, ModelOutcome.UNKNOWN, DeliveryOutcome.UNKNOWN, reason
    )


def _no_poll(_run_id: str) -> object:
    raise _UnsafeRecovery("durable_output_must_not_be_repolled")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _elapsed_moment(moment: datetime, clock_started: float) -> datetime:
    """Explicit test/batch timestamps cannot freeze a live reconciliation lease."""
    return moment + timedelta(seconds=max(0.0, monotonic() - clock_started))


def _claim_expired(run: AgentExecutionRun, claim: _Claim, moment: datetime) -> bool:
    return claim.expires_at <= moment or _as_utc(run.expires_at) <= moment
