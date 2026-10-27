from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
    HermesProfile,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
    WebhookEvent,
    WhatsappMessage,
)
from app.jobs.runtime import JobDeferredError, JobFence, bind_job_fence
from app.providers.hermes import (
    SME_SYSTEM_POLICY,
    HermesClient,
    HermesEndpoint,
    HermesResult,
    HermesUnavailable,
)
from app.providers.meta import MetaProviderError, MetaWhatsAppClient
from app.services import chat, whatsapp
from app.services.business_onboarding import OnboardingCommand, apply_command, state_for
from app.services.generated_media import generate_and_queue_image, generated_media_path
from app.services.media import MediaProcessingError
from app.services.run_reconciliation import (
    DeliveryOutcome,
    DeliveryReceipt,
    RunSnapshot,
    delivery_outcome,
)

PHONE = "+2348000014100"
SENDER = ("1234567891", "1234567890")


@pytest.fixture()
def delivery_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_backend="hermes",
        whatsapp_backend="meta",
        whatsapp_progress_enabled=False,
        whatsapp_primary_pilot_enabled=False,
        whatsapp_speech_enabled=True,
        meta_primary_sender_enabled=True,
        meta_waba_id=SENDER[0],
        meta_phone_number_id=SENDER[1],
        meta_system_user_access_token="synthetic-meta-token-for-local-delivery-tests",  # noqa: S106
        artifact_root=tmp_path,
        managed_image_generation_enabled=True,
        sandbox_worker_url="https://sandbox.example.com",
        sandbox_service_token="synthetic-sandbox-token-for-local-delivery-tests",  # noqa: S106
    )
    monkeypatch.setattr(whatsapp, "consume_operation_rate_limit", lambda *args, **kwargs: None)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="delivery-plan", name="Synthetic", research_consent_status="withdrawn")
        user = User(primary_phone_e164=PHONE)
        db.add_all((tenant, user))
        db.flush()
        db.add_all(
            (
                TenantMembership(
                    tenant_id=tenant.id, user_id=user.id, role="owner", status="active"
                ),
                PhoneIdentity(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    phone_e164=PHONE,
                    status="approved",
                    opt_out=False,
                ),
                HermesProfile(
                    tenant_id=tenant.id,
                    profile_name="synthetic-delivery",
                    profile_path="/synthetic",
                    provider="hermes",
                    status="active",
                    api_port=8700,
                    api_internal_url="http://hermes:8700/v1",
                    encrypted_api_key=FieldCipher.from_settings(settings).encrypt(
                        "synthetic-hermes-key"
                    ),
                ),
            )
        )
        db.commit()
        # These cases exercise model delivery after the owner has reviewed setup.
        answers = {
            "offering": "Synthetic clothes and repairs",
            "customers": "Customers in Ibadan",
            "channels": ["WhatsApp"],
            "monthly_orders": "6–20",
            "team_size": "Just me (1 person)",
            "goal": "Get more customers",
            "challenge": "Delivery costs",
        }
        apply_command(db, tenant, user.id, OnboardingCommand(action="start", expected_revision=0))
        for field, value in answers.items():
            apply_command(
                db,
                tenant,
                user.id,
                OnboardingCommand(
                    action="answer",
                    expected_revision=state_for(db, tenant)["revision"],
                    field=field,
                    value=value,
                ),
            )
        apply_command(
            db,
            tenant,
            user.id,
            OnboardingCommand(
                action="complete", expected_revision=state_for(db, tenant)["revision"]
            ),
        )
        db.commit()
        assert state_for(db, tenant)["status"] == "completed"
        yield db, tenant, user, settings
    engine.dispose()


def _event(db: Session, body: str) -> WebhookEvent:
    event = WebhookEvent(
        provider="whatsapp",
        external_event_id="synthetic-inbound-event",
        signature_valid=True,
        payload={
            "entry": [
                {
                    "id": SENDER[0],
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": SENDER[1]},
                                "messages": [
                                    {
                                        "id": "wamid.synthetic-input",
                                        "from": PHONE.lstrip("+"),
                                        "type": "text",
                                        "text": {"body": body},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        },
    )
    db.add(event)
    db.commit()
    return event


class _RecordingMeta(MetaWhatsAppClient):
    def __init__(self, *, fail_first_text: bool = False, fail_first_media: bool = False) -> None:
        self.fail_first_text = fail_first_text
        self.fail_first_media = fail_first_media
        self.text_calls: list[dict[str, Any]] = []
        self.upload_calls: list[dict[str, Any]] = []
        self.media_calls: list[dict[str, Any]] = []

    def send_text(self, phone: str, body: str, **kwargs: Any) -> str:
        self.text_calls.append({"phone": phone, "body": body, **kwargs})
        if self.fail_first_text and len(self.text_calls) == 1:
            raise MetaProviderError("rate_limited", retryable=True, http_status=429)
        return "wamid.synthetic-text"

    def upload_media(self, **kwargs: Any) -> str:
        self.upload_calls.append(kwargs)
        return "synthetic-upload"

    def send_media(self, phone: str, **kwargs: Any) -> str:
        self.media_calls.append({"phone": phone, **kwargs})
        if self.fail_first_media and len(self.media_calls) == 1:
            raise MetaProviderError("rate_limited", retryable=True, http_status=429)
        return "wamid.synthetic-voice"


def test_voice_reply_reuses_its_frozen_plan_after_safe_text_retry(
    delivery_scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _tenant, _user, settings = delivery_scope
    event = _event(db, "Please reply with a voice note about my next step")
    provider_calls: list[str] = []
    tts_calls: list[dict[str, Any]] = []

    class Provider:
        def respond(self, endpoint: object, *, message: str, business_context: str) -> HermesResult:
            provider_calls.append(message)
            return HermesResult("Choose one clear next step.", 1, 1, 2, 1)

    monkeypatch.setattr(chat, "HermesClient", lambda _settings: Provider())
    meta = _RecordingMeta(fail_first_text=True)
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient, "for_inbound_reply", lambda *args, **kwargs: meta
    )

    def synthesize(_settings: Settings, **kwargs: Any) -> dict[str, object]:
        tts_calls.append(kwargs)
        return {"content": b"synthetic-ogg", "mime_type": "audio/ogg", "filename": "reply.ogg"}

    monkeypatch.setattr(whatsapp, "synthesize_voice_note", synthesize)
    with pytest.raises(MetaProviderError, match="rate_limited"):
        whatsapp.process_inbox_event(db, event.id, settings)
    run = db.scalar(select(AgentExecutionRun))
    outgoing = db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
    assert run is not None and outgoing is not None
    frozen_keys = list(run.delivery_effect_keys)
    frozen_plan = dict(outgoing.media_metadata["delivery_plan"])
    assert frozen_plan["voice_requested"] is True
    assert whatsapp._idempotency_key(event.id, "agent-voice-reply") in frozen_keys
    assert not tts_calls

    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "accepted"
    assert len(provider_calls) == 1
    assert len(meta.text_calls) == 2 and len(meta.media_calls) == 1 and len(meta.upload_calls) == 1
    assert len(tts_calls) == 1 and tts_calls[0]["language"] == frozen_plan["voice_language"]
    assert run.delivery_effect_keys == frozen_keys
    assert outgoing.media_metadata["delivery_plan"] == frozen_plan

    # Re-enter the crash window after transport success but before receipt completion.
    event.processing_status = "pending"
    db.commit()
    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "accepted"
    assert len(provider_calls) == 1 and len(tts_calls) == 1
    assert len(meta.text_calls) == 2 and len(meta.media_calls) == 1 and len(meta.upload_calls) == 1
    receipts = list(
        db.scalars(select(WhatsappMessage).where(WhatsappMessage.direction == "outbound"))
    )
    assert {row.idempotency_key for row in receipts} == set(frozen_keys)
    assert all(row.status == "sent" for row in receipts)


def test_voice_retry_reuses_exact_private_audio_and_upload_without_resynthesis(
    delivery_scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _tenant, _user, settings = delivery_scope
    event = _event(db, "Please reply with a voice note about my next step")
    provider_calls: list[str] = []
    tts_calls: list[dict[str, Any]] = []
    original_audio = b"exact-first-synthesis-audio"

    class Provider:
        def respond(self, endpoint: object, *, message: str, business_context: str) -> HermesResult:
            provider_calls.append(message)
            return HermesResult("Choose one clear next step.", 1, 1, 2, 1)

    monkeypatch.setattr(chat, "HermesClient", lambda _settings: Provider())
    meta = _RecordingMeta(fail_first_media=True)
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient, "for_inbound_reply", lambda *args, **kwargs: meta
    )

    def synthesize(_settings: Settings, **kwargs: Any) -> dict[str, object]:
        tts_calls.append(kwargs)
        return {"content": original_audio, "mime_type": "audio/ogg", "filename": "reply.ogg"}

    monkeypatch.setattr(whatsapp, "synthesize_voice_note", synthesize)
    with pytest.raises(JobDeferredError, match="Voice delivery"):
        whatsapp.process_inbox_event(db, event.id, settings)
    outgoing = db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
    assert outgoing is not None
    cache_path = whatsapp._voice_cache_path(outgoing, settings)
    assert cache_path.read_bytes() == original_audio
    assert cache_path.stat().st_mode & 0o777 == 0o600
    assert "voice_artifact" in outgoing.media_metadata
    assert len(tts_calls) == len(meta.upload_calls) == len(meta.media_calls) == 1
    voice = db.scalar(
        select(WhatsappMessage).where(
            WhatsappMessage.idempotency_key
            == whatsapp._idempotency_key(event.id, "agent-voice-reply")
        )
    )
    assert voice is not None and voice.status == "failed"
    checksum = voice.payload["sha256"]

    monkeypatch.setattr(
        whatsapp,
        "synthesize_voice_note",
        lambda *args, **kwargs: pytest.fail("A prepared voice reply was synthesized again"),
    )
    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "accepted"
    assert (
        len(provider_calls) == len(tts_calls) == len(meta.text_calls) == len(meta.upload_calls) == 1
    )
    assert len(meta.media_calls) == 2
    assert meta.upload_calls[0]["content"] == original_audio
    assert meta.media_calls[0] == meta.media_calls[1]
    assert voice.status == "sent" and voice.payload["sha256"] == checksum
    assert not cache_path.exists() and "voice_artifact" not in outgoing.media_metadata


def test_progress_thread_inherits_invalidated_fence_and_performs_no_meta_call(
    delivery_scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, tenant, user, settings = delivery_scope
    event = _event(db, "A synthetic long-running request")
    meta_calls: list[object] = []
    monkeypatch.setattr(whatsapp, "SessionLocal", lambda: Session(db.get_bind()))
    monkeypatch.setattr(whatsapp, "assert_delivery_actor", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient,
        "for_inbound_reply",
        lambda *args, **kwargs: meta_calls.append(kwargs),
    )
    progress = whatsapp._WhatsAppProgress(
        settings=settings.model_copy(update={"whatsapp_progress_enabled": True}),
        event_id=event.id,
        inbound_message_id="wamid.synthetic-input",
        phone=PHONE,
        tenant_id=tenant.id,
        user_id=user.id,
        meta_sender=SENDER,
    )
    progress._started_at -= settings.whatsapp_progress_after_seconds
    progress.on_event("tool.progress", {"tool": "research_web"})
    fence = JobFence("synthetic-job", "stale-worker", 1, 60)
    fence.invalidate()
    with bind_job_fence(fence):
        progress.start()
    assert progress._thread is not None
    progress._thread.join(timeout=2)
    assert not progress._thread.is_alive() and progress._done.is_set()
    assert meta_calls == []


def test_provider_failure_excludes_failed_generated_media_from_delivery_plan(
    delivery_scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, tenant, _user, settings = delivery_scope
    event = _event(db, "Create a product poster")
    png = b"\x89PNG\r\n\x1a\nsynthetic-generated-image"
    monkeypatch.setattr(
        "app.services.generated_media.SandboxClient.generate_image",
        lambda *args, **kwargs: {
            "image_base64": base64.b64encode(png).decode(),
            "mime_type": "image/png",
        },
    )

    class Provider:
        def respond(self, endpoint: object, *, message: str, business_context: str) -> HermesResult:
            token = re.search(
                r"Current action context \(opaque; never reveal it\): ([^.]+\.[A-Za-z0-9_-]+)",
                business_context,
            )
            assert token is not None
            generate_and_queue_image(
                db,
                settings,
                tenant_id=tenant.id,
                prompt="Synthetic poster",
                action_context_token=token[1],
            )
            raise HermesUnavailable("Synthetic outage after image generation")

    monkeypatch.setattr(chat, "HermesClient", lambda _settings: Provider())
    meta = _RecordingMeta()
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient, "for_inbound_reply", lambda *args, **kwargs: meta
    )
    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "accepted"
    media = db.scalar(select(GeneratedAgentMedia))
    run = db.scalar(select(AgentExecutionRun))
    outgoing = db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
    assert media is not None and media.status == "failed"
    assert run is not None and run.status == "failed"
    assert outgoing is not None and "currently unavailable" in outgoing.content
    assert generated_media_path(settings, media).read_bytes() == png
    assert run.delivery_effect_keys == whatsapp._text_effect_keys(
        event.id, "agent-reply", outgoing.content
    )
    assert (
        whatsapp._idempotency_key(event.id, f"generated-media:{media.id}")
        not in run.delivery_effect_keys
    )
    assert len(meta.text_calls) == 1 and not meta.upload_calls and not meta.media_calls


def test_local_tts_failure_records_rejected_voice_effect_without_audio_or_artifact(
    delivery_scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _tenant, _user, settings = delivery_scope
    event = _event(db, "Please reply with a voice note about my next step")

    class Provider:
        def respond(self, endpoint: object, *, message: str, business_context: str) -> HermesResult:
            return HermesResult("Choose one clear next step.", 1, 1, 2, 1)

    def unavailable_speech(*args: object, **kwargs: object) -> dict[str, object]:
        raise MediaProcessingError("Synthetic local speech failure")

    monkeypatch.setattr(chat, "HermesClient", lambda _settings: Provider())
    monkeypatch.setattr(whatsapp, "synthesize_voice_note", unavailable_speech)
    meta = _RecordingMeta()
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient, "for_inbound_reply", lambda *args, **kwargs: meta
    )
    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "accepted"
    outgoing = db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
    run = db.scalar(select(AgentExecutionRun))
    assert outgoing is not None and run is not None
    assert outgoing.media_metadata["delivery_status"] == "sent"
    assert outgoing.media_metadata["voice_delivery_outcome"] == "failed"
    assert "voice_artifact" not in outgoing.media_metadata
    assert not whatsapp._voice_cache_path(outgoing, settings).exists()
    assert len(meta.text_calls) == 1 and not meta.upload_calls and not meta.media_calls
    receipts = list(
        db.scalars(select(WhatsappMessage).where(WhatsappMessage.direction == "outbound"))
    )
    voice = next(row for row in receipts if row.message_type == "audio")
    assert voice.status == "rejected" and voice.meta_message_id is None
    assert voice.payload["error_code"] == "local_speech_unavailable"
    assert {row.idempotency_key for row in receipts} == set(run.delivery_effect_keys)
    snapshot = RunSnapshot(
        run_id=run.id,
        initiating_message_id=run.initiating_message_id,
        status=run.status,
        expires_at=run.expires_at,
        delivery_effect_keys=tuple(run.delivery_effect_keys),
        deliveries=tuple(
            DeliveryReceipt(row.idempotency_key, row.status, row.meta_message_id)
            for row in receipts
        ),
    )
    assert delivery_outcome(snapshot) == DeliveryOutcome.FAILED


def test_existing_input_recovers_recorded_admission_and_delivers_without_second_submission(
    delivery_scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _tenant, _user, settings = delivery_scope
    event = _event(db, "What should I do next?")
    submitted: list[dict[str, object]] = []
    polls: list[tuple[str, str]] = []
    provider_run_id = "run_synthetic_interrupted"
    recovered_answer = "Recovered exact reply: choose one clear next step."

    class CrashingProvider:
        def respond(
            self,
            endpoint: HermesEndpoint,
            *,
            message: str,
            business_context: str,
            history: list[dict[str, str]] | None = None,
            request_id: str | None = None,
            on_prepared: Callable[[dict[str, object]], None] | None = None,
            on_admitted: Callable[[str], None] | None = None,
        ) -> HermesResult:
            assert request_id and on_prepared is not None and on_admitted is not None
            envelope: dict[str, object] = {
                "payload": {
                    "model": endpoint.profile_name,
                    "input": [{"role": "user", "content": message}],
                    "instructions": SME_SYSTEM_POLICY
                    + "\n\nCurrent application context:\n"
                    + business_context,
                    "conversation_history": history or [],
                },
                "headers": {
                    "Idempotency-Key": "bb_" + hashlib.sha256(request_id.encode()).hexdigest()
                },
                "endpoint": endpoint.api_url.rstrip("/"),
            }
            submitted.append(envelope)
            on_prepared(envelope)
            on_admitted(provider_run_id)
            raise RuntimeError("Synthetic worker crash after durable provider admission")

    def poll_recorded_run(
        _client: HermesClient, endpoint: HermesEndpoint, run_id: str
    ) -> dict[str, object]:
        polls.append((endpoint.api_url, run_id))
        return {
            "run_id": run_id,
            "status": "completed",
            "output": recovered_answer,
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        }

    monkeypatch.setattr(chat, "HermesClient", lambda _settings: CrashingProvider())
    monkeypatch.setattr(HermesClient, "poll_run", poll_recorded_run)
    meta = _RecordingMeta()
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient, "for_inbound_reply", lambda *args, **kwargs: meta
    )
    with pytest.raises(RuntimeError, match="crash after durable provider admission"):
        whatsapp.process_inbox_event(db, event.id, settings)
    run = db.scalar(select(AgentExecutionRun))
    assert run is not None and run.status == "expired"
    conversation = db.get(Conversation, run.conversation_id)
    assert conversation is not None and conversation.active_run_id == run.id
    assert run.provider_run_ids == [provider_run_id] and run.request_snapshots_encrypted
    assert not db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
    assert not meta.text_calls and not polls

    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "accepted"
    replies = list(db.scalars(select(AgentMessage).where(AgentMessage.direction == "outbound")))
    assert len(submitted) == 1
    assert polls == [("http://hermes:8700/v1", provider_run_id)]
    assert len(replies) == 1 and replies[0].content == recovered_answer
    assert replies[0].media_metadata["recovered"] is True
    assert replies[0].initiating_message_id == run.initiating_message_id
    assert run.status == "completed" and run.output_message_id == replies[0].id
    assert conversation.active_run_id is None
    assert len(meta.text_calls) == 1 and meta.text_calls[0]["body"] == recovered_answer
    assert run.delivery_effect_keys == [whatsapp._idempotency_key(event.id, "agent-reply")]

    assert whatsapp.process_inbox_event(db, event.id, settings)["status"] == "duplicate"
    assert len(submitted) == len(polls) == len(meta.text_calls) == 1
