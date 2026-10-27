from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    AsyncJob,
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
from app.jobs.runtime import JobFence, JobLeaseLostError, bind_job_fence
from app.providers.hermes import _CITATION_REPAIR_PROMPT, SME_SYSTEM_POLICY, HermesClient
from app.services import run_recovery
from app.services.conversation_scope import receiver_scope
from app.services.run_reconciliation import (
    DeliveryOutcome,
    ModelOutcome,
    ReconciliationAction,
    freeze_request,
)
from app.services.run_recovery import reconcile_application_run, reconcile_expired_runs

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


@dataclass
class State:
    db: Session
    settings: Settings
    tenant: Tenant
    user: User
    member: TenantMembership
    profile: HermesProfile
    conversation: Conversation
    inbound: AgentMessage
    run: AgentExecutionRun


@pytest.fixture()
def state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, app_env="test", agent_backend="hermes")
    cipher = FieldCipher.from_settings(settings)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="synthetic-recovery", name="Synthetic recovery business")
        user = User(primary_phone_e164="+2348000011111")
        db.add_all((tenant, user))
        db.flush()
        member = TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner")
        profile = HermesProfile(
            tenant_id=tenant.id,
            profile_name="synthetic-profile",
            provider="hermes",
            profile_path="/synthetic",
            api_port=8700,
            api_internal_url="http://hermes:8700/v1",
            encrypted_api_key=cipher.encrypt("synthetic-private-key"),
        )
        conversation = Conversation(
            tenant_id=tenant.id, user_id=user.id, channel="web", environment_lane="test"
        )
        db.add_all((member, profile, conversation))
        db.flush()
        inbound = AgentMessage(
            tenant_id=tenant.id,
            user_id=user.id,
            hermes_profile_id=profile.id,
            conversation_id=conversation.id,
            channel="web",
            direction="inbound",
            content="Help with this request",
            event_scope=receiver_scope("test", ("", "")),
        )
        db.add(inbound)
        db.flush()
        run = AgentExecutionRun(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            initiating_message_id=inbound.id,
            provider_run_ids=["provider_one"],
            expires_at=NOW - timedelta(minutes=1),
            created_at=NOW - timedelta(minutes=2),
        )
        db.add(run)
        db.flush()
        conversation.active_run_id = run.id
        value = State(db, settings, tenant, user, member, profile, conversation, inbound, run)
        retain_requests(value)
        yield value
    engine.dispose()


def envelope(state, *, instructions=None, suffix=""):
    return {
        "payload": {
            "model": state.profile.profile_name,
            "input": [{"role": "user", "content": state.inbound.content}],
            "instructions": instructions
            or SME_SYSTEM_POLICY + "\n\nCurrent application context:\nSynthetic context",
            "conversation_history": [],
        },
        "headers": {
            "Idempotency-Key": "bb_"
            + hashlib.sha256(state.inbound.id.encode()).hexdigest()
            + suffix
        },
        "endpoint": state.profile.api_internal_url,
    }


def retain_requests(state, requests=None):
    records = []
    for request in requests or [envelope(state)]:
        frozen = freeze_request(request)
        records.append(
            {"payload_json": frozen.payload_json, "sha256": frozen.sha256, "recoverable": True}
        )
    state.run.request_snapshots_encrypted = FieldCipher.from_settings(state.settings).encrypt(
        json.dumps(records)
    )
    state.db.commit()


def must_not_poll(_endpoint, _provider_id):
    pytest.fail("No provider lookup is authorized for this state")


def completed(_endpoint, provider_id):
    return {
        "run_id": provider_id,
        "status": "completed",
        "output": "Recovered accepted answer",
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }


def recover(state, poll=completed):
    return reconcile_application_run(state.db, state.run.id, state.settings, NOW, poll)


def replies(state):
    return state.db.scalars(select(AgentMessage).where(AgentMessage.direction == "outbound")).all()


def add_output(state, *, parent=None, kind="reply"):
    output = AgentMessage(
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        hermes_profile_id=state.profile.id,
        conversation_id=state.conversation.id,
        channel=state.inbound.channel,
        direction="outbound",
        initiating_message_id=parent or state.inbound.id,
        content="Already accepted answer",
        media_metadata={"event_kind": kind},
    )
    state.db.add(output)
    state.db.flush()
    state.run.output_message_id = output.id
    state.db.commit()
    return output


def make_whatsapp(state):
    identity = PhoneIdentity(
        tenant_id=state.tenant.id, user_id=state.user.id, phone_e164="+2348000011111"
    )
    state.db.add(identity)
    state.db.flush()
    state.conversation.channel = state.inbound.channel = "whatsapp"
    state.conversation.receiver_waba_id = "mock"
    state.conversation.receiver_phone_id = "mock"
    state.inbound.event_scope = receiver_scope("test", ("mock", "mock"))
    state.inbound.media_metadata = {"phone_identity_id": identity.id}
    state.run.actor_identity_id = identity.id
    state.db.commit()
    return identity


def test_known_completion_is_claimed_polled_without_transaction_then_atomically_restored(state):
    calls = []

    def poll(endpoint, provider_id):
        assert not state.db.in_transaction()
        assert endpoint.profile_name == state.profile.profile_name
        calls.append(provider_id)
        with Session(state.db.get_bind()) as other:
            claimed = other.get(AgentExecutionRun, state.run.id)
            assert claimed.status == "reconciling"
            assert claimed.reconcile_epoch == 1
        return completed(endpoint, provider_id)

    result = recover(state, poll)
    assert calls == ["provider_one"]
    assert result.action == ReconciliationAction.PERSIST_OUTPUT
    state.db.refresh(state.run)
    state.db.refresh(state.conversation)
    output = state.db.get(AgentMessage, state.run.output_message_id)
    assert output.initiating_message_id == state.inbound.id
    assert output.media_metadata["event_kind"] == "reply"
    assert output.media_metadata["execution_run_id"] == state.run.id
    assert output.content == "Recovered accepted answer"
    assert output.media_metadata["total_tokens"] == 5
    assert state.run.status == "completed"
    assert state.conversation.active_run_id is None
    assert len(state.db.scalars(select(UsageEvent)).all()) == 1


def test_default_recovery_adapter_only_gets_the_recorded_admission(state, monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"run_id": "provider_one", "status": "completed", "output": "Recovered answer"},
        )

    monkeypatch.setattr(
        run_recovery,
        "HermesClient",
        lambda settings: HermesClient(settings, transport=httpx.MockTransport(handler)),
    )
    result = reconcile_application_run(state.db, state.run.id, state.settings, NOW)
    assert result.action == ReconciliationAction.PERSIST_OUTPUT
    assert calls == [("GET", "/v1/runs/provider_one")]


def test_repeated_recovery_never_duplicates_output_or_usage_or_repolls(state):
    assert recover(state).action == ReconciliationAction.PERSIST_OUTPUT
    output_id = state.run.output_message_id
    second = recover(state, must_not_poll)
    assert second.action == ReconciliationAction.COMPLETE
    assert state.run.output_message_id == output_id
    assert len(replies(state)) == 1
    assert len(state.db.scalars(select(UsageEvent)).all()) == 1


def test_unexpired_running_run_is_not_revoked(state):
    state.run.expires_at = NOW + timedelta(seconds=1)
    state.db.commit()
    assert recover(state, must_not_poll).action == ReconciliationAction.WAIT
    state.db.refresh(state.run)
    assert state.run.status == "running"
    assert state.run.reconcile_epoch == 0
    assert state.conversation.active_run_id == state.run.id


def test_unknown_admission_never_resubmits_or_creates_response(state):
    state.run.provider_run_ids = []
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.action == ReconciliationAction.REVIEW
    assert state.run.status == "unknown"
    assert replies(state) == []
    assert state.conversation.active_run_id is None


def test_cleared_run_anchor_never_resurrects_old_output_from_timestamp_guesses(state):
    state.conversation.active_run_id = None
    state.db.commit()
    assert recover(state, must_not_poll).reason == "active_run_binding_invalid"
    assert replies(state) == []


def test_recovered_voice_output_remains_unheard_until_actual_playback_callback(state):
    make_whatsapp(state)
    state.conversation.status = "voice"
    state.inbound.media_metadata = {**state.inbound.media_metadata, "type": "voice_call"}
    state.db.commit()
    assert recover(state).action == ReconciliationAction.PERSIST_OUTPUT
    output = state.db.get(AgentMessage, state.run.output_message_id)
    assert output.media_metadata["type"] == "voice_call"
    assert output.media_metadata["voice_playback_state"] == "generated"


@pytest.mark.parametrize("status", ["closed", "cancelled"])
def test_closed_or_cancelled_conversation_cannot_publish_recovered_reply(state, status):
    state.conversation.status = status
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.reason == "conversation_scope_inactive"
    assert state.run.status == "unknown"
    assert replies(state) == []


@pytest.mark.parametrize("target", ["member", "user", "tenant"])
def test_revoked_actor_is_not_reauthorized_by_provider_completion(state, target):
    obj = getattr(state, target)
    obj.status = "suspended"
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.reason == "initiating_actor_revoked"
    assert replies(state) == []


def test_revoked_alias_cannot_receive_recovered_reply(state):
    identity = make_whatsapp(state)
    identity.opt_out = True
    state.db.commit()
    assert recover(state, must_not_poll).reason == "initiating_actor_revoked"
    assert replies(state) == []


def test_old_output_for_another_parent_is_not_adopted_or_replaced(state):
    other = AgentMessage(
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        hermes_profile_id=state.profile.id,
        conversation_id=state.conversation.id,
        channel="web",
        direction="inbound",
        content="Other turn",
    )
    state.db.add(other)
    state.db.flush()
    old = add_output(state, parent=other.id)
    result = recover(state, must_not_poll)
    assert result.reason == "durable_output_binding_invalid"
    assert state.run.output_message_id == old.id
    assert len(replies(state)) == 1


def test_control_output_is_not_mistaken_for_model_reply(state):
    add_output(state, kind="control")
    assert recover(state, must_not_poll).reason == "durable_output_binding_invalid"


def test_existing_durable_answer_can_resume_delivery_without_snapshot_or_model_poll(state):
    make_whatsapp(state)
    output = add_output(state)
    state.run.request_snapshots_encrypted = None
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.action == ReconciliationAction.RESUME_DELIVERY
    assert state.run.output_message_id == output.id
    assert len(replies(state)) == 1


def test_accepted_outage_fallback_does_not_become_successful_model_generation(state):
    output = add_output(state)
    state.run.status = "failed"
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.action == ReconciliationAction.COMPLETE
    assert result.model_outcome == ModelOutcome.FAILED
    assert state.run.status == "failed"
    assert output.media_metadata["model_outcome"] == "failed"
    assert recover(state, must_not_poll).model_outcome == ModelOutcome.FAILED


@pytest.mark.parametrize("status", ["sending", "ambiguous"])
def test_unknown_transport_outcome_cannot_be_retried_even_with_durable_model_output(state, status):
    identity = make_whatsapp(state)
    add_output(state)
    state.run.delivery_effect_keys = ["prepared-effect"]
    state.db.add(
        WhatsappMessage(
            tenant_id=state.tenant.id,
            user_id=state.user.id,
            direction="outbound",
            idempotency_key="prepared-effect",
            receiver_scope=state.inbound.event_scope,
            phone_e164=identity.phone_e164,
            status=status,
            payload={},
        )
    )
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.COMPLETED
    assert result.delivery_outcome == DeliveryOutcome.UNKNOWN
    assert state.run.output_message_id is not None


def test_frozen_full_delivery_manifest_is_required_for_complete_logical_delivery(state):
    make_whatsapp(state)
    add_output(state)
    state.run.delivery_effect_keys = ["part-one", "part-two"]
    state.db.add(
        WhatsappMessage(
            tenant_id=state.tenant.id,
            user_id=state.user.id,
            direction="outbound",
            payload={},
            idempotency_key="part-one",
            receiver_scope=state.inbound.event_scope,
            status="sent",
            meta_message_id="meta-one",
        )
    )
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.action == ReconciliationAction.RESUME_DELIVERY
    assert result.delivery_outcome == DeliveryOutcome.PENDING


def test_losing_reconcile_epoch_during_poll_cannot_apply_old_result(state):
    def poll(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            run = other.get(AgentExecutionRun, state.run.id)
            run.reconcile_epoch += 1
            other.commit()
        return completed(endpoint, provider_id)

    result = recover(state, poll)
    assert result.reason == "reconciliation_claim_lost"
    assert result.output is None
    assert replies(state) == []
    state.db.refresh(state.run)
    assert state.run.reconcile_epoch == 2
    assert state.run.status == "reconciling"


def test_new_run_admitted_during_poll_is_never_overwritten(state):
    replacement_id = []

    def poll(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            replacement = AgentExecutionRun(
                tenant_id=state.tenant.id,
                user_id=state.user.id,
                conversation_id=state.conversation.id,
                initiating_message_id=state.inbound.id,
                expires_at=NOW + timedelta(minutes=5),
                created_at=NOW,
            )
            other.add(replacement)
            other.flush()
            other.get(Conversation, state.conversation.id).active_run_id = replacement.id
            replacement_id.append(replacement.id)
            other.commit()
        return completed(endpoint, provider_id)

    assert recover(state, poll).reason == "reconciliation_claim_lost"
    state.db.refresh(state.conversation)
    assert state.conversation.active_run_id == replacement_id[0]
    assert replies(state) == []


def test_actor_revocation_after_poll_is_rechecked_before_apply(state):
    def poll(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            other.get(User, state.user.id).status = "suspended"
            other.commit()
        return completed(endpoint, provider_id)

    assert recover(state, poll).reason == "initiating_actor_revoked"
    assert replies(state) == []


@pytest.mark.parametrize(
    "mutation", ["ciphertext", "digest", "endpoint", "model", "request_key", "instructions"]
)
def test_corrupt_or_rebound_snapshot_fails_closed_without_polling(state, mutation):
    if mutation == "ciphertext":
        state.run.request_snapshots_encrypted = "invalid-private-material"
        state.db.commit()
    elif mutation == "digest":
        frozen = freeze_request(envelope(state))
        state.run.request_snapshots_encrypted = FieldCipher.from_settings(state.settings).encrypt(
            json.dumps(
                [{"recoverable": True, "payload_json": frozen.payload_json, "sha256": "0" * 64}]
            )
        )
        state.db.commit()
    else:
        request = envelope(state)
        if mutation == "endpoint":
            request["endpoint"] = "http://hermes:8701/v1"
        elif mutation == "model":
            request["payload"]["model"] = "another-profile"
        elif mutation == "request_key":
            request["headers"]["Idempotency-Key"] = "another-turn"
        else:
            request["payload"]["instructions"] = "Unknown output policy"
        retain_requests(state, [request])
    result = recover(state, must_not_poll)
    assert result.action == ReconciliationAction.REVIEW
    assert result.output is None
    assert replies(state) == []
    events = state.db.scalars(select(OperationalAgentEvent)).all()
    assert "private-material" not in repr([event.event_metadata for event in events])


def test_profile_endpoint_change_after_poll_cannot_publish_old_profile_output(state):
    def poll(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            other.get(HermesProfile, state.profile.id).api_internal_url = "http://hermes:8701/v1"
            other.commit()
        return completed(endpoint, provider_id)

    assert recover(state, poll).reason == "provider_endpoint_changed"
    assert replies(state) == []


def test_incomplete_citation_draft_is_never_published_or_automatically_repaired(state):
    state.inbound.content = "Give current official sources"
    retain_requests(state)
    calls = []

    def poll(endpoint, provider_id):
        calls.append(provider_id)
        return completed(endpoint, provider_id)

    result = recover(state, poll)
    assert result.reason == "output_validation_failed"
    assert calls == ["provider_one"]
    assert replies(state) == []


def test_mutated_input_cannot_weaken_frozen_original_output_policy(state):
    state.inbound.content = "Give current official sources"
    retain_requests(state)
    state.inbound.content = "Hello"
    state.db.commit()
    assert recover(state, must_not_poll).reason == "frozen_input_binding_invalid"
    assert replies(state) == []


def test_admitted_citation_repair_recovers_only_final_answer_and_all_usage(state):
    state.inbound.content = "Give current official sources"
    original = envelope(state)
    repair_instructions = (
        original["payload"]["instructions"]
        + "\nInternal citation repair; the draft was never shown to the merchant.\n"
        + _CITATION_REPAIR_PROMPT.format(required=2)
        + "\nUnverified candidate answer to repair:\nDraft"
    )
    repair = envelope(state, instructions=repair_instructions, suffix="_citation")
    state.run.provider_run_ids = ["provider_one", "provider_repair"]
    retain_requests(state, [original, repair])

    def poll(endpoint, provider_id):
        answer = completed(endpoint, provider_id)
        if provider_id == "provider_repair":
            answer["output"] = "Verified https://official.example/a https://official.example/b"
        return answer

    result = recover(state, poll)
    assert result.action == ReconciliationAction.PERSIST_OUTPUT
    assert result.output.provider_run_id == "provider_repair"
    assert result.output.total_tokens == 10


def test_prepared_but_unknown_repair_admission_does_not_reuse_original_draft(state):
    original = envelope(state)
    retain_requests(state, [original, envelope(state, suffix="_citation")])
    result = recover(state, must_not_poll)
    assert result.reason == "provider_admission_snapshot_mismatch"
    assert replies(state) == []


def test_binary_request_not_retained_is_explicitly_unrecoverable(state):
    record = {
        "recoverable": False,
        "sha256": "0" * 64,
        "reason": "binary_or_oversized_input_not_retained",
    }
    state.run.request_snapshots_encrypted = FieldCipher.from_settings(state.settings).encrypt(
        json.dumps([record])
    )
    state.db.commit()
    result = recover(state, must_not_poll)
    assert result.reason == "frozen_request_not_retained"
    assert replies(state) == []
    assert (
        "payload_json"
        not in json.loads(
            FieldCipher.from_settings(state.settings).decrypt(state.run.request_snapshots_encrypted)
        )[0]
    )


def test_only_matching_current_run_pending_media_are_bound_to_recovered_output(state):
    media = GeneratedAgentMedia(
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        conversation_id=state.conversation.id,
        agent_message_id=state.inbound.id,
        execution_run_id=state.run.id,
        channel="web",
        mime_type="image/png",
        storage_path="synthetic.png",
        byte_size=1,
        checksum_sha256="0" * 64,
    )
    unrelated = GeneratedAgentMedia(
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        conversation_id=state.conversation.id,
        agent_message_id=state.inbound.id,
        execution_run_id=None,
        channel="web",
        mime_type="image/png",
        storage_path="other.png",
        byte_size=1,
        checksum_sha256="1" * 64,
    )
    state.db.add_all((media, unrelated))
    state.db.commit()
    assert recover(state).action == ReconciliationAction.PERSIST_OUTPUT
    assert media.agent_message_id == state.run.output_message_id
    assert unrelated.agent_message_id == state.inbound.id


def test_pending_known_provider_run_stays_revoked_and_can_only_be_polled_later(state):
    result = recover(
        state, lambda _endpoint, provider_id: {"run_id": provider_id, "status": "running"}
    )
    assert result.action == ReconciliationAction.POLL_LATER
    assert state.run.status == "expired"
    assert state.run.expires_at.replace(tzinfo=UTC) > NOW
    assert state.conversation.active_run_id == state.run.id
    assert replies(state) == []


def test_sweeper_uses_bounded_fixed_selection_and_skips_unknown_reviews(state):
    assert (
        reconcile_expired_runs(state.db, state.settings, now=NOW, limit=1, poll_override=completed)[
            0
        ].action
        == ReconciliationAction.PERSIST_OUTPUT
    )
    assert (
        reconcile_expired_runs(
            state.db, state.settings, now=NOW, limit=1, poll_override=must_not_poll
        )
        == ()
    )
    with pytest.raises(ValueError):
        reconcile_expired_runs(state.db, state.settings, limit=101)


def recovery_fence(state):
    from app.core.time import utcnow

    job = AsyncJob(
        kind="system.reconcile_expired_runs",
        payload={"limit": 1},
        idempotency_key="synthetic-recovery-job",
        status="running",
        claim_epoch=1,
        locked_by="recovery-worker",
        locked_at=utcnow(),
    )
    state.db.add(job)
    state.db.commit()
    return job, JobFence(job.id, "recovery-worker", 1, 600)


def assert_no_recovery_application(state):
    state.db.refresh(state.run)
    state.db.refresh(state.conversation)
    assert state.run.output_message_id is None
    assert state.conversation.active_run_id == state.run.id
    assert replies(state) == []
    assert list(state.db.scalars(select(UsageEvent))) == []
    assert list(state.db.scalars(select(OperationalAgentEvent))) == []


def test_invalid_current_recovery_fence_cannot_claim_or_retire_originating_run(state):
    _, fence = recovery_fence(state)
    fence.invalidate()
    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        recover(state, must_not_poll)
    assert_no_recovery_application(state)
    assert state.run.reconcile_epoch == 0 and state.run.status == "running"


def test_recovery_fence_is_checked_immediately_before_claim_mutation(state, monkeypatch):
    _, fence = recovery_fence(state)
    validate = run_recovery._validated_requests

    def revoke_during_validation(*args, **kwargs):
        frozen = validate(*args, **kwargs)
        fence.invalidate()
        return frozen

    monkeypatch.setattr(run_recovery, "_validated_requests", revoke_during_validation)
    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        recover(state, must_not_poll)
    assert_no_recovery_application(state)
    assert state.run.reconcile_epoch == 0 and state.run.status == "running"


def test_recovery_fence_invalidated_during_get_cannot_apply_returned_output(state):
    _, fence = recovery_fence(state)
    polls = []

    def revoke_during_get(endpoint, provider_id):
        polls.append(provider_id)
        fence.invalidate()
        return completed(endpoint, provider_id)

    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        recover(state, revoke_during_get)
    assert_no_recovery_application(state)
    assert polls == ["provider_one"]
    assert state.run.status == "reconciling" and state.run.reconcile_epoch == 1


def test_recovery_worker_epoch_replaced_during_get_cannot_apply_result(state):
    job, fence = recovery_fence(state)

    def replace_job_epoch(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            replacement = other.get(AsyncJob, job.id)
            replacement.claim_epoch += 1
            replacement.locked_by = "replacement-recovery-worker"
            other.commit()
        return completed(endpoint, provider_id)

    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        recover(state, replace_job_epoch)
    assert fence.invalid
    assert_no_recovery_application(state)
    assert state.run.status == "reconciling"


def test_lost_fence_before_retirement_rolls_back_already_flushed_output(state, monkeypatch):
    _, fence = recovery_fence(state)
    retire = run_recovery._retire

    def revoke_before_retire(*args, **kwargs):
        assert len(replies(state)) == 1  # output was flushed in this transaction
        fence.invalidate()
        return retire(*args, **kwargs)

    monkeypatch.setattr(run_recovery, "_retire", revoke_before_retire)
    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        recover(state)
    assert not state.db.in_transaction()
    assert_no_recovery_application(state)
    assert state.run.status == "reconciling"


def test_revoked_original_job_does_not_revoke_valid_recovery_worker(state):
    original = AsyncJob(
        kind="whatsapp.process_webhook",
        payload={},
        idempotency_key="synthetic-expired-origin",
        status="cancelled",
        claim_epoch=2,
    )
    state.db.add(original)
    state.db.flush()
    state.run.job_id, state.run.job_claim_epoch = original.id, 1
    state.run.job_stale_lock_seconds = 60
    state.db.commit()
    _, fence = recovery_fence(state)
    with bind_job_fence(fence):
        assert recover(state).action == ReconciliationAction.PERSIST_OUTPUT
    assert len(replies(state)) == 1 and state.run.status == "completed"


@pytest.mark.parametrize("overdue_seconds", [0, 1])
def test_long_get_expiring_reconciliation_claim_cannot_apply_with_frozen_explicit_now(
    state, monkeypatch, overdue_seconds
):
    clock = [0.0]
    monkeypatch.setattr(run_recovery, "monotonic", lambda: clock[0])
    lease = min(3600, max(60, state.settings.hermes_read_timeout_seconds * 2 + 30))
    polls = []

    def slow_get(endpoint, provider_id):
        polls.append(provider_id)
        clock[0] = lease + overdue_seconds
        return completed(endpoint, provider_id)

    result = recover(state, slow_get)
    assert result.action == ReconciliationAction.REVIEW
    assert result.reason == "reconciliation_claim_expired" and result.output is None
    assert polls == ["provider_one"]
    assert_no_recovery_application(state)
    assert state.run.status == "reconciling" and state.run.reconcile_epoch == 1


def test_persisted_claim_expiration_without_new_epoch_is_checked_after_get(state):
    def expire_claim(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            other.get(AgentExecutionRun, state.run.id).expires_at = NOW - timedelta(seconds=1)
            other.commit()
        return completed(endpoint, provider_id)

    result = recover(state, expire_claim)
    assert result.reason == "reconciliation_claim_expired" and result.output is None
    assert_no_recovery_application(state)
    assert state.run.reconcile_epoch == 1


def test_extending_persisted_expiry_without_new_epoch_cannot_extend_immutable_claim(
    state, monkeypatch
):
    clock = [0.0]
    monkeypatch.setattr(run_recovery, "monotonic", lambda: clock[0])
    lease = min(3600, max(60, state.settings.hermes_read_timeout_seconds * 2 + 30))

    def extend_without_claim(endpoint, provider_id):
        with Session(state.db.get_bind()) as other:
            other.get(AgentExecutionRun, state.run.id).expires_at = NOW + timedelta(days=1)
            other.commit()
        clock[0] = lease + 1
        return completed(endpoint, provider_id)

    result = recover(state, extend_without_claim)
    assert result.reason == "reconciliation_claim_expired" and result.output is None
    assert_no_recovery_application(state)


def test_claim_expiring_during_final_application_rolls_back_partial_output(state, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(run_recovery, "monotonic", lambda: clock[0])
    lease = min(3600, max(60, state.settings.hermes_read_timeout_seconds * 2 + 30))
    durable = run_recovery._durable_output
    calls = []

    def slow_final_validation(*args, **kwargs):
        value = durable(*args, **kwargs)
        calls.append(True)
        if len(calls) == 2:
            clock[0] = lease + 1
        return value

    monkeypatch.setattr(run_recovery, "_durable_output", slow_final_validation)
    result = recover(state)
    assert result.reason == "reconciliation_claim_expired" and result.output is None
    assert_no_recovery_application(state)


def test_sweep_advances_supplied_moment_between_bounded_run_calls(state, monkeypatch):
    additional = AgentExecutionRun(
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        conversation_id=state.conversation.id,
        initiating_message_id=state.inbound.id,
        expires_at=NOW - timedelta(minutes=2),
    )
    state.db.add(additional)
    state.db.commit()
    clock = [0.0]
    monkeypatch.setattr(run_recovery, "monotonic", lambda: clock[0])
    supplied = []

    def capture_call(db, run_id, settings, now=None, poll_override=None):
        supplied.append(now)
        clock[0] += 7
        return run_recovery._review("synthetic_review")

    monkeypatch.setattr(run_recovery, "reconcile_application_run", capture_call)
    decisions = reconcile_expired_runs(state.db, state.settings, now=NOW, limit=2)
    assert len(decisions) == 2
    assert supplied == [NOW, NOW + timedelta(seconds=7)]
