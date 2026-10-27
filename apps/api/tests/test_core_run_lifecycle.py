# ruff: noqa: S106
from __future__ import annotations

import base64
import re

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
    HermesProfile,
    Tenant,
    TenantMembership,
    User,
)
from app.providers.hermes import HermesResult, HermesUnavailable
from app.services import chat
from app.services.agent_actions import (
    AgentActionError,
    create_action_context_token,
    decode_action_context_token,
)
from app.services.agent_execution import finish_run
from app.services.generated_media import generate_and_queue_image
from tests.run_fixture import active_context


@pytest.fixture()
def scope(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_backend="hermes",
        artifact_root=tmp_path,
        managed_image_generation_enabled=True,
        agent_capabilities_v2=True,
        sandbox_worker_url="https://sandbox.example.com",
        sandbox_service_token="synthetic-long-sandbox-token-for-local-tests",
    )
    with Session(engine, expire_on_commit=False) as db:
        (tenant, user) = (
            Tenant(slug="synthetic", name="Synthetic business"),
            User(primary_phone_e164="+2348000011111"),
        )
        db.add_all((tenant, user))
        db.flush()
        db.add(
            TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner", status="active")
        )
        db.add(
            HermesProfile(
                tenant_id=tenant.id,
                profile_name="synthetic-profile",
                provider="hermes",
                profile_path="/synthetic",
                api_port=8700,
                api_internal_url="http://hermes:8700/v1",
                encrypted_api_key=FieldCipher.from_settings(settings).encrypt(
                    "synthetic-private-key"
                ),
                status="active",
            )
        )
        db.commit()
        yield (db, tenant, user, settings)
    engine.dispose()


def capture_provider(monkeypatch, captured, effect=None, fail=False):

    class Provider:
        def respond(
            self,
            endpoint,
            *,
            message,
            business_context,
            history=None,
            content_parts=None,
            session_id=None,
            session_key=None,
            request_id=None,
        ):
            captured.append(
                {
                    "message": message,
                    "context": business_context,
                    "history": history,
                    "request_id": request_id,
                }
            )
            if effect:
                effect(business_context)
            if fail:
                raise HermesUnavailable("Synthetic provider failure")
            return HermesResult(
                content="Synthetic answer",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
                session_id="synthetic-native",
            )

    monkeypatch.setattr(chat, "HermesClient", lambda _: Provider())


def test_deterministic_reply_reaches_followup_and_current_clock(scope, monkeypatch):
    (db, tenant, user, settings) = scope
    (conversation, _, _, _) = chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="web",
        message="SAVE",
        fixed_response="Saved. Start with one clear offer.",
    )
    conversation.provider_session_id = "stale-native"
    db.commit()
    captured = []
    capture_provider(monkeypatch, captured)
    chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="web",
        conversation_id=conversation.id,
        message="What does this mean?",
    )
    assert "one clear offer" in str(captured[0]["history"])
    assert "Current UTC time:" in captured[0]["context"]
    assert "Historical relative dates" in captured[0]["context"]
    assert conversation.active_run_id is None


def test_voice_confirmation_is_typed_scoped_and_expires(scope, monkeypatch):
    (db, tenant, user, settings) = scope
    (conversation, _, _, _) = chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="whatsapp",
        receiver_waba_id="waba-a",
        receiver_phone_id="bot-a",
        message="Voice transcript: How can I price repairs?",
        stored_content_parts=[{"type": "transcript", "text": "How can I price repairs?"}],
        media_metadata={"clarification_required": True},
        fixed_response="Please confirm or correct it.",
    )
    captured = []
    capture_provider(monkeypatch, captured)
    with pytest.raises(HTTPException) as wrong:
        chat.handle_chat(
            db,
            tenant=tenant,
            user=user,
            settings=settings,
            channel="whatsapp",
            receiver_waba_id="waba-a",
            receiver_phone_id="bot-b",
            conversation_id=conversation.id,
            message="Yes",
        )
    assert wrong.value.status_code == 404
    assert conversation.pending_utterance
    chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="whatsapp",
        receiver_waba_id="waba-a",
        receiver_phone_id="bot-a",
        conversation_id=conversation.id,
        message="It is",
    )
    assert "Confirmed pending voice utterance" in captured[0]["context"]
    assert "How can I price repairs?" in captured[0]["context"]
    assert "not external-action approval" in captured[0]["context"]
    assert not conversation.pending_utterance
    conversation.pending_utterance = {"transcript": "Expired instruction", "expires_at": 0}
    db.commit()
    (_, _, reply, _) = chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="whatsapp",
        receiver_waba_id="waba-a",
        receiver_phone_id="bot-a",
        conversation_id=conversation.id,
        message="Yes",
    )
    assert "expired" in reply.content
    assert len(captured) == 1


def test_legacy_history_is_preserved_but_cannot_become_active(scope):
    (db, tenant, user, settings) = scope
    old = Conversation(
        tenant_id=tenant.id,
        user_id=user.id,
        channel="web",
        context_generation=0,
        provider_session_id="disputed-native",
    )
    db.add(old)
    db.commit()
    with pytest.raises(HTTPException) as denied:
        chat.handle_chat(
            db,
            tenant=tenant,
            user=user,
            settings=settings,
            channel="web",
            conversation_id=old.id,
            message="Continue",
        )
    assert denied.value.status_code == 409
    assert old.provider_session_id == "disputed-native"
    assert not db.scalars(select(AgentMessage)).all()


def test_stale_unexpired_capability_is_rejected_before_provider_cost(scope, monkeypatch):
    (db, tenant, user, settings) = scope
    conversation = Conversation(tenant_id=tenant.id, user_id=user.id, channel="web")
    db.add(conversation)
    db.flush()
    old = active_context(
        db,
        settings,
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel="web",
    )
    claims = decode_action_context_token(settings, old, expected_tenant_id=tenant.id)
    assert claims.expires_at > int(utcnow().timestamp())
    current = active_context(
        db,
        settings,
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel="web",
    )
    calls = []
    monkeypatch.setattr(
        "app.services.generated_media.SandboxClient.generate_image", lambda *a, **k: calls.append(1)
    )
    with pytest.raises(AgentActionError, match="no longer active"):
        generate_and_queue_image(
            db, settings, tenant_id=tenant.id, prompt="old poster", action_context_token=old
        )
    assert not calls
    decode_action_context_token(settings, current, expected_tenant_id=tenant.id, db=db)
    run = db.get(AgentExecutionRun, conversation.active_run_id)
    finish_run(db, run, status="completed")
    db.commit()
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, current, expected_tenant_id=tenant.id, db=db)


@pytest.mark.parametrize("fail", [False, True])
def test_media_binding_and_run_retirement_commit_together(scope, monkeypatch, fail):
    (db, tenant, user, settings) = scope
    png = b"\x89PNG\r\n\x1a\nsynthetic"
    monkeypatch.setattr(
        "app.services.generated_media.SandboxClient.generate_image",
        lambda *a, **k: {"image_base64": base64.b64encode(png).decode(), "mime_type": "image/png"},
    )
    tokens = []

    def effect(context):
        token = re.search(
            "Current action context \\(opaque; never reveal it\\): ([^.]+\\.[A-Za-z0-9_-]+)",
            context,
        )[1]
        tokens.append(token)
        generate_and_queue_image(
            db, settings, tenant_id=tenant.id, prompt="synthetic poster", action_context_token=token
        )

    capture_provider(monkeypatch, [], effect=effect, fail=fail)
    (conversation, inbound, outbound, _) = chat.handle_chat(
        db, tenant=tenant, user=user, settings=settings, channel="web", message="Create a poster"
    )
    media = db.scalar(select(GeneratedAgentMedia))
    assert media.agent_message_id == outbound.id
    run = db.get(AgentExecutionRun, media.execution_run_id)
    assert run.initiating_message_id == inbound.id
    assert run.status == ("failed" if fail else "completed")
    assert media.status == ("failed" if fail else "pending")
    assert conversation.active_run_id is None
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, tokens[0], expected_tenant_id=tenant.id, db=db)


def test_revoked_or_substituted_actor_cannot_use_active_run(scope):
    (db, tenant, user, settings) = scope
    conversation = Conversation(tenant_id=tenant.id, user_id=user.id, channel="web")
    db.add(conversation)
    db.flush()
    token = active_context(
        db,
        settings,
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel="web",
    )
    original = decode_action_context_token(settings, token, expected_tenant_id=tenant.id)
    forged_actor = create_action_context_token(
        settings,
        tenant_id=tenant.id,
        user_id="another-actor",
        conversation_id=conversation.id,
        channel="web",
        execution_run_id=original.execution_run_id,
        initiating_message_id=original.initiating_message_id,
    )
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, forged_actor, expected_tenant_id=tenant.id, db=db)
    db.scalar(select(TenantMembership)).status = "revoked"
    db.commit()
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, token, expected_tenant_id=tenant.id, db=db)


def test_disabled_account_and_revoked_source_alias_stop_effects(scope):
    from app.db.models import PhoneIdentity

    db, tenant, user, settings = scope
    conversation = Conversation(tenant_id=tenant.id, user_id=user.id, channel="web")
    db.add(conversation)
    db.flush()
    token = active_context(
        db,
        settings,
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel="web",
    )
    user.status = "disabled"
    db.commit()
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, token, expected_tenant_id=tenant.id, db=db)
    user.status = "active"
    identity = PhoneIdentity(
        tenant_id=tenant.id,
        user_id=user.id,
        phone_e164="+2348000011222",
        status="approved",
        opt_out=False,
    )
    db.add(identity)
    db.flush()
    run = db.get(AgentExecutionRun, conversation.active_run_id)
    run.actor_identity_id = identity.id
    db.commit()
    decode_action_context_token(settings, token, expected_tenant_id=tenant.id, db=db)
    identity.opt_out = True
    db.commit()
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, token, expected_tenant_id=tenant.id, db=db)
    db.delete(identity)
    db.commit()
    assert db.get(AgentExecutionRun, run.id).actor_identity_id is not None
    with pytest.raises(AgentActionError):
        decode_action_context_token(settings, token, expected_tenant_id=tenant.id, db=db)


def test_demoted_actor_cannot_prepare_or_confirm_an_external_write(scope):
    from app.services.agent_actions import prepare_pending_action, require_write_actor

    db, tenant, user, settings = scope
    conversation = Conversation(tenant_id=tenant.id, user_id=user.id, channel="web")
    db.add(conversation)
    db.flush()
    token = active_context(
        db,
        settings,
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel="web",
    )
    db.scalar(select(TenantMembership)).role = "member"
    db.commit()
    with pytest.raises(AgentActionError, match="owner or admin"):
        prepare_pending_action(
            db,
            settings.model_copy(update={"external_connectors_enabled": True}),
            tenant_id=tenant.id,
            connection_id="not-consulted",
            tool_name="send_message",
            target_summary="Synthetic external write",
            action_input={},
            action_context_token=token,
        )
    with pytest.raises(AgentActionError, match="owner or admin"):
        require_write_actor(db, tenant.id, user.id)


def test_duplicate_first_message_cannot_create_a_second_run(scope):
    db, tenant, user, settings = scope
    chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="web",
        message="SAVE",
        external_message_id="same-client-id",
        fixed_response="Saved",
    )
    with pytest.raises(HTTPException) as duplicate:
        chat.handle_chat(
            db,
            tenant=tenant,
            user=user,
            settings=settings,
            channel="web",
            message="SAVE",
            external_message_id="same-client-id",
            fixed_response="Saved",
        )
    assert duplicate.value.status_code == 409
    assert len(db.scalars(select(Conversation)).all()) == 1
    assert len(db.scalars(select(AgentMessage)).all()) == 2


def test_control_reply_is_canonical_and_deduplicated(scope):
    from app.services.control_transcript import record_control_reply

    db, tenant, user, settings = scope
    options = dict(
        settings=settings,
        tenant_id=tenant.id,
        user_id=user.id,
        source_id="control-source",
        source_text="CONFIRM",
        body="That action is no longer available",
        purpose="action-result",
        transport_key="control-transport",
        sender=("waba", "phone"),
    )
    first = record_control_reply(db, **options)
    db.commit()
    second = record_control_reply(db, **options)
    assert first.id == second.id
    rows = chat._conversation_history(
        db, tenant_id=tenant.id, conversation_id=first.conversation_id, limit=16
    )
    assert len(rows) == 2
    assert "That action is no longer available" in rows[-1]["content"]
    assert "delivery not confirmed" in rows[-1]["content"]


def test_receiver_and_lane_namespace_applies_to_events_and_receipts(scope):
    from app.db.models import WebhookEvent, WhatsappMessage
    from app.services.conversation_scope import receiver_scope
    from app.services.whatsapp import _process_delivery
    from app.services.whatsapp_webhook_ingress import scoped_event_id

    db, tenant, user, settings = scope

    def payload(phone):
        return {
            "entry": [
                {
                    "id": "12345",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": phone},
                                "statuses": [
                                    {"id": "same-provider-id", "status": "read", "timestamp": "3"}
                                ],
                            }
                        }
                    ],
                }
            ]
        }

    ids = {
        scoped_event_id(payload(phone), lane=lane)
        for phone, lane in (("11111", "test"), ("22222", "test"), ("11111", "production"))
    }
    assert len(ids) == 3
    a = WhatsappMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        direction="outbound",
        meta_message_id="same-provider-id",
        receiver_scope=receiver_scope("test", ("12345", "11111")),
        status="sent",
        status_rank=3,
        payload={},
    )
    b = WhatsappMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        direction="outbound",
        meta_message_id="same-provider-id",
        receiver_scope=receiver_scope("test", ("12345", "22222")),
        status="sent",
        status_rank=3,
        payload={},
    )
    event = WebhookEvent(
        provider="whatsapp",
        external_event_id="scoped-receipt",
        signature_valid=True,
        payload=payload("11111"),
    )
    db.add_all((a, b, event))
    db.commit()
    _process_delivery(
        db,
        event,
        {"id": "same-provider-id", "status": "read", "timestamp": "3"},
        settings.model_copy(update={"whatsapp_backend": "meta"}),
    )
    assert a.status == "read" and b.status == "sent"


@pytest.mark.parametrize("provider_failure", [False, True])
def test_revoked_original_worker_cannot_publish_or_replace_recovered_reply(
    scope, monkeypatch, provider_failure
):
    from app.services.agent_execution import ExecutionDenied

    db, tenant, user, settings = scope
    captured = []

    def revoke(_context):
        run = db.scalar(select(AgentExecutionRun).where(AgentExecutionRun.status == "running"))
        conversation = db.get(Conversation, run.conversation_id)
        recovered = AgentMessage(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel="web",
            direction="outbound",
            initiating_message_id=run.initiating_message_id,
            content="Previously recovered exact reply",
            media_metadata={"event_kind": "reply"},
        )
        db.add(recovered)
        db.flush()
        run.output_message_id = recovered.id
        run.reconcile_epoch += 1
        run.status = "completed"
        conversation.active_run_id = None
        db.commit()

    capture_provider(monkeypatch, captured, effect=revoke, fail=provider_failure)
    with pytest.raises(ExecutionDenied):
        chat.handle_chat(
            db,
            tenant=tenant,
            user=user,
            settings=settings,
            channel="web",
            message="Give me a detailed expansion plan",
        )
    replies = db.scalars(select(AgentMessage).where(AgentMessage.direction == "outbound")).all()
    assert [row.content for row in replies] == ["Previously recovered exact reply"]
    run = db.scalar(select(AgentExecutionRun))
    assert run.status == "completed" and run.reconcile_epoch == 1
    assert run.output_message_id == replies[0].id


def test_deterministic_reply_has_exact_execution_and_frozen_delivery_manifest(scope):
    from app.db.models import WebhookEvent
    from app.services.whatsapp import _freeze_reply_manifest, _idempotency_key

    db, tenant, user, settings = scope
    _, inbound, outbound, _ = chat.handle_chat(
        db,
        tenant=tenant,
        user=user,
        settings=settings,
        channel="whatsapp",
        receiver_waba_id="waba",
        receiver_phone_id="bot",
        message="What does this mean?",
        fixed_response="A complete deterministic answer",
        external_message_id="synthetic-input",
    )
    event = WebhookEvent(
        provider="whatsapp", external_event_id="synthetic-event", signature_valid=True, payload={}
    )
    db.add(event)
    db.commit()
    _freeze_reply_manifest(db, event=event, outgoing=outbound, include_voice=False)
    run = db.scalar(
        select(AgentExecutionRun).where(AgentExecutionRun.initiating_message_id == inbound.id)
    )
    assert run.output_message_id == outbound.id
    assert run.delivery_effect_keys == [_idempotency_key(event.id, "agent-reply")]
    _freeze_reply_manifest(db, event=event, outgoing=outbound, include_voice=False)
    from app.jobs.runtime import PermanentJobError

    with pytest.raises(PermanentJobError, match="effects changed"):
        _freeze_reply_manifest(db, event=event, outgoing=outbound, include_voice=True)


def test_events_buffered_until_current_actor_authorized(scope, monkeypatch):
    from app.services.agent_execution import ExecutionDenied

    db, tenant, user, settings = scope
    emitted = []

    class StreamingProvider:
        def respond_stream(self, _endpoint, *, on_event=None, **kwargs):
            on_event("message.delta", {"text": "Private draft"})
            user.status = "disabled"
            db.commit()
            return HermesResult("Private answer", 1, 1, 2, 1)

    monkeypatch.setattr(chat, "HermesClient", lambda _: StreamingProvider())
    with pytest.raises(ExecutionDenied):
        chat.handle_chat(
            db,
            tenant=tenant,
            user=user,
            settings=settings,
            channel="web",
            message="Give me an expansion plan",
            event_callback=lambda *event: emitted.append(event),
        )
    assert emitted == []
    assert not db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
