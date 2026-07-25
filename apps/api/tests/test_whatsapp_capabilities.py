from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import (
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
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
from app.jobs.runtime import PermanentJobError
from app.providers.meta import MetaProviderError, MetaWhatsAppClient
from app.services import whatsapp


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_authorized_phone(db: Session) -> tuple[Tenant, User]:
    tenant = Tenant(
        slug="whatsapp-capabilities",
        name="WhatsApp Capabilities",
        research_consent_status="pending",
    )
    user = User(primary_phone_e164="+2348000000044")
    db.add_all((tenant, user))
    db.flush()
    db.add_all(
        (
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                role="owner",
                status="active",
            ),
            PhoneIdentity(
                tenant_id=tenant.id,
                user_id=user.id,
                phone_e164="+2348000000044",
                whatsapp_wa_id="2348000000044",
                status="approved",
                opt_out=False,
            ),
        )
    )
    db.commit()
    return tenant, user


def test_whatsapp_batch_extraction_reply_context_and_progress_language() -> None:
    payload = {
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "987654321"},
                            "messages": [
                                {"id": "m-1", "type": "text", "text": {"body": "Hello"}},
                                {
                                    "id": "m-2",
                                    "type": "interactive",
                                    "interactive": {
                                        "button_reply": {
                                            "id": (
                                                "agent_action:confirm:"
                                                "12345678-1234-1234-1234-123456789012"
                                            ),
                                            "title": "Confirm",
                                        }
                                    },
                                },
                            ],
                            "statuses": [{"id": "out-1", "status": "read", "timestamp": "42"}],
                        }
                    }
                ],
            }
        ]
    }
    parts = whatsapp.split_inbox_events(payload)
    assert len(parts) == 3
    assert whatsapp.extract_message(parts[0][0])["id"] == "m-1"  # type: ignore[index]
    assert whatsapp.extract_delivery_status(parts[2][0])["status"] == "read"  # type: ignore[index]
    assert whatsapp.extract_inbound_sender(payload) == ("123456789", "987654321")
    assert whatsapp._message_text(parts[0][0]["entry"][0]["changes"][0]["value"]["messages"][0])
    interactive = parts[1][0]["entry"][0]["changes"][0]["value"]["messages"][0]
    assert whatsapp._interactive_action(interactive) == (
        "confirm",
        "12345678-1234-1234-1234-123456789012",
    )
    consent_settings = Settings(research_consent_policy_version="pilot-v2")
    assert (
        whatsapp._interactive_research_consent(
            {
                "interactive": {
                    "button_reply": {
                        "id": "research_consent:grant:pilot-v2",
                    }
                }
            },
            consent_settings,
        )
        == "grant"
    )
    assert whatsapp._voice_reply_requested("Please reply with a voice note")
    assert not whatsapp._voice_reply_requested("Show me sales")
    assert "current sources" in whatsapp._progress_body("research_web")
    assert "store data" in whatsapp._progress_body("get_business_overview")
    assert "connected service" in whatsapp._progress_body("gmail_read")
    assert "calculation" in whatsapp._progress_body("sandbox_run_code")
    assert whatsapp._idempotency_key("event", "reply") == whatsapp._idempotency_key(
        "event", "reply"
    )
    text = "A" * 3995 + " boundary " + "B" * 50
    chunks = whatsapp._split_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert whatsapp.extract_message({}) is None
    assert whatsapp.extract_delivery_status({}) is None
    assert whatsapp.split_inbox_events({}) == [({}, "root")]


def test_delivery_status_is_monotonic_and_failure_is_separate() -> None:
    factory = _factory()
    with factory() as db:
        message = WhatsappMessage(
            meta_message_id="wamid.monotonic",
            wa_id="2348000000000",
            phone_e164="+2348000000000",
            direction="outbound",
            message_type="text",
            text_body="Reply",
            payload={},
            status="sent",
            status_rank=3,
        )
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="delivery-read",
            signature_valid=True,
            payload={},
        )
        db.add_all((message, event))
        db.commit()

        whatsapp._process_delivery(
            db,
            event,
            {"id": message.meta_message_id, "status": "read", "timestamp": "3"},
        )
        assert message.status == "read"
        assert message.status_rank == 5

        late_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="delivery-late",
            signature_valid=True,
            payload={},
        )
        db.add(late_event)
        db.commit()
        whatsapp._process_delivery(
            db,
            late_event,
            {"id": message.meta_message_id, "status": "delivered", "timestamp": "2"},
        )
        assert message.status == "read"
        assert message.status_rank == 5

        failed_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="delivery-failed",
            signature_valid=True,
            payload={},
        )
        db.add(failed_event)
        db.commit()
        whatsapp._process_delivery(
            db,
            failed_event,
            {"id": message.meta_message_id, "status": "failed", "timestamp": "4"},
        )
        assert message.status == "read"
        assert message.payload["delivery_failure"]["status"] == "failed"
        assert (
            len(
                db.scalars(
                    select(WhatsappDeliveryEvent).where(
                        WhatsappDeliveryEvent.meta_message_id == message.meta_message_id
                    )
                ).all()
            )
            == 3
        )


def test_message_actions_consent_reset_and_media_failures_always_reply(
    monkeypatch,
) -> None:
    factory = _factory()
    delivered: list[dict] = []
    monkeypatch.setattr(
        whatsapp,
        "_deliver_once",
        lambda *_args, **kwargs: delivered.append(kwargs) or "wamid.reply",
    )
    monkeypatch.setattr(whatsapp, "consume_operation_rate_limit", lambda *_args, **_kwargs: None)
    settings = Settings(
        app_env="test",
        whatsapp_backend="mock",
        whatsapp_primary_pilot_enabled=True,
        research_consent_policy_version="pilot-v2",
    )
    with factory() as db:
        tenant, _user = _seed_authorized_phone(db)

        action_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="action-event",
            signature_valid=True,
            payload={},
        )
        db.add(action_event)
        db.commit()
        action_result = whatsapp._process_message(
            db,
            action_event,
            {
                "id": "action-message",
                "from": "2348000000044",
                "type": "interactive",
                "interactive": {
                    "button_reply": {
                        "id": ("agent_action:confirm:12345678-1234-1234-1234-123456789012"),
                        "title": "Confirm",
                    }
                },
            },
            settings,
        )
        assert action_result["status"] == "action_not_found"
        assert delivered[-1]["body"] == "That action is no longer available."

        consent_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="consent-event",
            signature_valid=True,
            payload={},
        )
        db.add(consent_event)
        db.commit()
        consent_result = whatsapp._process_message(
            db,
            consent_event,
            {
                "id": "consent-message",
                "from": "2348000000044",
                "type": "interactive",
                "interactive": {
                    "button_reply": {
                        "id": "research_consent:grant:pilot-v2",
                        "title": "I consent",
                    }
                },
            },
            settings,
        )
        assert consent_result["status"] == "research_consent_grant"
        assert tenant.research_consent_status == "granted"
        assert db.scalar(select(ResearchConsent).where(ResearchConsent.tenant_id == tenant.id))

        reset_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="reset-event",
            signature_valid=True,
            payload={},
        )
        db.add(reset_event)
        db.commit()
        reset_result = whatsapp._process_message(
            db,
            reset_event,
            {
                "id": "reset-message",
                "from": "2348000000044",
                "type": "text",
                "text": {"body": "/new"},
            },
            settings,
        )
        assert reset_result["status"] == "conversation_reset"

        media_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="media-event",
            signature_valid=True,
            payload={},
        )
        db.add(media_event)
        db.commit()
        fallback = whatsapp._process_message(
            db,
            media_event,
            {
                "id": "image-message",
                "from": "2348000000044",
                "type": "image",
                "image": {"id": "image-12345"},
            },
            settings,
        )
        assert fallback["status"] == "media_fallback"
        assert "attachment" in delivered[-1]["body"]


def test_successful_message_delivers_text_generated_media_action_preview_and_consent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory = _factory()
    text_deliveries: list[dict] = []
    direct_deliveries: list[dict] = []
    media_deliveries: list[dict] = []
    monkeypatch.setattr(
        whatsapp,
        "_deliver_text_chunks",
        lambda *_args, **kwargs: text_deliveries.append(kwargs) or ["wamid.text"],
    )
    monkeypatch.setattr(
        whatsapp,
        "_deliver_once",
        lambda *_args, **kwargs: direct_deliveries.append(kwargs) or "wamid.direct",
    )
    monkeypatch.setattr(
        whatsapp,
        "_deliver_media_once",
        lambda *_args, **kwargs: media_deliveries.append(kwargs) or "wamid.media",
    )
    monkeypatch.setattr(whatsapp, "consume_operation_rate_limit", lambda *_args, **_kwargs: None)
    settings = Settings(
        app_env="test",
        artifact_root=tmp_path,
        whatsapp_backend="mock",
        whatsapp_primary_pilot_enabled=True,
        research_consent_policy_version="pilot-v2",
        internal_service_token="whatsapp-capability-service-token-0001",  # noqa: S106
    )
    generated_content = b"\x89PNG\r\n\x1a\ngenerated-whatsapp"

    def fake_chat(
        db: Session,
        *,
        tenant: Tenant,
        user: User,
        message: str,
        **_kwargs,
    ):
        conversation = Conversation(
            tenant_id=tenant.id,
            user_id=user.id,
            channel="whatsapp",
        )
        db.add(conversation)
        db.flush()
        incoming = AgentMessage(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel="whatsapp",
            direction="inbound",
            content=message,
            redacted_content=message,
            external_message_id="successful-message",
        )
        outgoing = AgentMessage(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel="whatsapp",
            direction="outbound",
            content="Your generated poster and exact supplier draft are ready.",
            redacted_content="Your generated poster and exact supplier draft are ready.",
        )
        db.add_all((incoming, outgoing))
        db.flush()
        media_id = new_id()
        relative = Path("generated-media") / "test" / f"{media_id}.png"
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(generated_content)
        db.add(
            GeneratedAgentMedia(
                id=media_id,
                tenant_id=tenant.id,
                user_id=user.id,
                conversation_id=conversation.id,
                channel="whatsapp",
                media_type="image",
                mime_type="image/png",
                storage_path=relative.as_posix(),
                byte_size=len(generated_content),
                checksum_sha256=hashlib.sha256(generated_content).hexdigest(),
                status="pending",
            )
        )
        db.add(
            PendingAgentAction(
                tenant_id=tenant.id,
                user_id=user.id,
                conversation_id=conversation.id,
                tool_name="create_file",
                target_summary="Create the exact supplier draft.",
                action_input={"name": "supplier-draft.txt", "content": "Exact content"},
                status="pending",
                idempotency_key="whatsapp-action-preview",
                confirmation_token_hash="0" * 64,
                expires_at=utcnow() + timedelta(minutes=10),
            )
        )
        db.commit()
        return conversation, incoming, outgoing, None

    monkeypatch.setattr(whatsapp, "handle_chat", fake_chat)
    with factory() as db:
        tenant, _user = _seed_authorized_phone(db)
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="successful-event",
            signature_valid=True,
            payload={},
        )
        db.add(event)
        db.commit()
        result = whatsapp._process_message(
            db,
            event,
            {
                "id": "successful-message",
                "from": "2348000000044",
                "type": "text",
                "text": {"body": "Create a poster and prepare the supplier draft"},
            },
            settings,
        )
        assert result["status"] == "accepted"
        assert text_deliveries[0]["body"].startswith("Your generated poster")
        assert any("External action:" in delivery["body"] for delivery in text_deliveries)
        assert media_deliveries[0]["content"] == generated_content
        assert any(delivery.get("interactive_buttons") for delivery in direct_deliveries)
        assert any(
            delivery["purpose"].startswith("research-consent-request:")
            for delivery in direct_deliveries
        )
        assert tenant.research_consent_status == "pending"
        media = db.scalar(select(GeneratedAgentMedia))
        assert media is not None and media.status == "delivered"


def _meta_settings() -> Settings:
    return Settings(
        app_env="test",
        whatsapp_backend="meta",
        meta_waba_id="123456789",
        meta_phone_number_id="987654321",
        meta_system_user_access_token="meta-test-access-token-0000000000001",  # noqa: S106
        meta_inbound_reply_senders=["123456789:987654321"],
    )


def _meta_client() -> MetaWhatsAppClient:
    return MetaWhatsAppClient(
        graph_version="v23.0",
        phone_number_id="987654321",
        access_token="meta-test-access-token-0000000000001",  # noqa: S106
    )


def test_outbound_text_interactive_chunking_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    client = _meta_client()
    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "for_inbound_reply",
        classmethod(lambda _cls, _settings, **_kwargs: client),
    )
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "send_text",
        lambda _self, phone, body, **kwargs: (
            calls.append(("text", phone, (body, kwargs))),
            f"wamid.text.{len(calls)}",
        )[1],
    )
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "send_interactive_buttons",
        lambda _self, phone, **kwargs: (
            calls.append(("interactive", phone, kwargs)),
            f"wamid.interactive.{len(calls)}",
        )[1],
    )
    settings = _meta_settings()
    with factory() as db:
        tenant, user = _seed_authorized_phone(db)
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="outbound-text-event",
            signature_valid=True,
            payload={},
        )
        db.add(event)
        db.commit()
        sent = whatsapp._deliver_once(
            db,
            event=event,
            purpose="normal-reply",
            phone="+2348000000044",
            body="Exact answer",
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=("123456789", "987654321"),
            reply_to_message_id="wamid.inbound.1",
        )
        assert sent == "wamid.text.1"
        assert (
            whatsapp._deliver_once(
                db,
                event=event,
                purpose="normal-reply",
                phone="+2348000000044",
                body="Exact answer",
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=("123456789", "987654321"),
                reply_to_message_id="wamid.inbound.1",
            )
            == "wamid.text.1"
        )
        interactive = whatsapp._deliver_once(
            db,
            event=event,
            purpose="confirmation",
            phone="+2348000000044",
            body="Confirm the exact action?",
            settings=settings,
            tenant_id=tenant.id,
            user_id=user.id,
            meta_sender=("123456789", "987654321"),
            reply_to_message_id="wamid.inbound.1",
            interactive_buttons=[("confirm:1", "Confirm"), ("deny:1", "Cancel")],
        )
        assert interactive == "wamid.interactive.2"
        assert len(calls) == 2
        messages = db.scalars(
            select(WhatsappMessage).where(WhatsappMessage.direction == "outbound")
        ).all()
        assert {message.status for message in messages} == {"sent"}
        assert {message.status_rank for message in messages} == {
            whatsapp.DELIVERY_STATUS_RANK["sent"]
        }

        local_event = WebhookEvent(
            provider="whatsapp",
            external_event_id="outbound-chunks-event",
            signature_valid=True,
            payload={},
        )
        db.add(local_event)
        db.commit()
        chunk_ids = whatsapp._deliver_text_chunks(
            db,
            event=local_event,
            purpose="long-reply",
            phone="+2348000000044",
            body=("A" * 3990) + " break " + ("B" * 100),
            settings=Settings(app_env="test", whatsapp_backend="mock"),
            tenant_id=tenant.id,
            user_id=user.id,
        )
        assert len(chunk_ids) == 2
        assert len(set(chunk_ids)) == 2
        with pytest.raises(ValueError, match="positive"):
            whatsapp._split_text("text", max_length=0)


def test_outbound_text_failures_are_persisted_for_safe_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    client = _meta_client()
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "for_inbound_reply",
        classmethod(lambda _cls, _settings, **_kwargs: client),
    )
    settings = _meta_settings()
    with factory() as db:
        tenant, user = _seed_authorized_phone(db)
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="outbound-failure-event",
            signature_valid=True,
            payload={},
        )
        db.add(event)
        db.commit()

        monkeypatch.setattr(
            MetaWhatsAppClient,
            "send_text",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                MetaProviderError("transport", retryable=True)
            ),
        )
        with pytest.raises(PermanentJobError, match="cannot be retried"):
            whatsapp._deliver_once(
                db,
                event=event,
                purpose="ambiguous",
                phone="+2348000000044",
                body="May have been sent",
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=("123456789", "987654321"),
            )
        ambiguous = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.idempotency_key == whatsapp._idempotency_key(event.id, "ambiguous")
            )
        )
        assert ambiguous is not None and ambiguous.status == "ambiguous"
        with pytest.raises(PermanentJobError, match="reconciliation"):
            whatsapp._deliver_once(
                db,
                event=event,
                purpose="ambiguous",
                phone="+2348000000044",
                body="May have been sent",
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=("123456789", "987654321"),
            )

        monkeypatch.setattr(
            MetaWhatsAppClient,
            "send_text",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad request")),
        )
        with pytest.raises(PermanentJobError, match="invalid"):
            whatsapp._deliver_once(
                db,
                event=event,
                purpose="invalid",
                phone="+2348000000044",
                body="Invalid send",
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=("123456789", "987654321"),
            )
        rejected = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.idempotency_key == whatsapp._idempotency_key(event.id, "invalid")
            )
        )
        assert rejected is not None and rejected.status == "rejected"

        monkeypatch.setattr(
            MetaWhatsAppClient,
            "send_text",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                MetaProviderError("provider", retryable=True, http_status=429)
            ),
        )
        with pytest.raises(MetaProviderError):
            whatsapp._deliver_once(
                db,
                event=event,
                purpose="retryable",
                phone="+2348000000044",
                body="Retryable send",
                settings=settings,
                tenant_id=tenant.id,
                user_id=user.id,
                meta_sender=("123456789", "987654321"),
            )
        retryable = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.idempotency_key == whatsapp._idempotency_key(event.id, "retryable")
            )
        )
        assert retryable is not None and retryable.status == "failed"


def test_outbound_media_upload_delivery_and_failure_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    client = _meta_client()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "for_inbound_reply",
        classmethod(lambda _cls, _settings, **_kwargs: client),
    )
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "upload_media",
        lambda _self, **kwargs: (calls.append(("upload", kwargs)), "media-12345")[1],
    )
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "send_media",
        lambda _self, phone, **kwargs: (
            calls.append(("send", (phone, kwargs))),
            "wamid.media.1",
        )[1],
    )
    settings = _meta_settings()
    with factory() as db:
        tenant, user = _seed_authorized_phone(db)
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="outbound-media-event",
            signature_valid=True,
            payload={},
        )
        db.add(event)
        db.commit()
        kwargs = {
            "db": db,
            "event": event,
            "purpose": "media-reply",
            "phone": "+2348000000044",
            "content": b"voice-note-content",
            "mime_type": "audio/ogg",
            "filename": "reply.ogg",
            "media_type": "audio",
            "voice": True,
            "settings": settings,
            "tenant_id": tenant.id,
            "user_id": user.id,
            "meta_sender": ("123456789", "987654321"),
            "reply_to_message_id": "wamid.inbound.2",
        }
        assert whatsapp._deliver_media_once(**kwargs) == "wamid.media.1"
        assert whatsapp._deliver_media_once(**kwargs) == "wamid.media.1"
        assert [kind for kind, _value in calls] == ["upload", "send"]
        outbound = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.idempotency_key
                == whatsapp._idempotency_key(event.id, "media-reply")
            )
        )
        assert outbound is not None
        assert outbound.payload["uploaded_media_id"] == "media-12345"
        assert outbound.status == "sent"

        no_sender = {**kwargs, "purpose": "no-sender", "meta_sender": None}
        with pytest.raises(PermanentJobError, match="configured Meta sender"):
            whatsapp._deliver_media_once(**no_sender)

        monkeypatch.setattr(
            MetaWhatsAppClient,
            "send_media",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                MetaProviderError("provider", retryable=False, http_status=400)
            ),
        )
        rejected = {**kwargs, "purpose": "media-rejected"}
        with pytest.raises(PermanentJobError, match="cannot be retried"):
            whatsapp._deliver_media_once(**rejected)
        rejected_row = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.idempotency_key
                == whatsapp._idempotency_key(event.id, "media-rejected")
            )
        )
        assert rejected_row is not None and rejected_row.status == "rejected"


def test_immediate_read_receipts_and_basic_event_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    client = _meta_client()
    reads: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "for_inbound_reply",
        classmethod(lambda _cls, _settings, **_kwargs: client),
    )
    monkeypatch.setattr(
        MetaWhatsAppClient,
        "mark_read",
        lambda _self, message_id, *, typing=False: reads.append((message_id, typing)),
    )
    payload = {
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "987654321"},
                            "messages": [
                                {
                                    "id": "wamid.inbound.receipt",
                                    "from": "2348000000044",
                                    "type": "text",
                                    "text": {"body": "Hello"},
                                }
                            ],
                        }
                    }
                ],
            }
        ]
    }
    whatsapp.acknowledge_inbound_receipts(payload, _meta_settings())
    whatsapp.acknowledge_inbound_receipts(
        payload,
        Settings(app_env="test", whatsapp_backend="mock"),
    )
    assert reads == [("wamid.inbound.receipt", True)]

    with factory() as db:
        with pytest.raises(PermanentJobError, match="does not exist"):
            whatsapp.process_inbox_event(db, "missing", Settings(app_env="test"))
        duplicate = WebhookEvent(
            provider="whatsapp",
            external_event_id="already-processed",
            signature_valid=True,
            payload={},
            processing_status="processed",
        )
        empty = WebhookEvent(
            provider="whatsapp",
            external_event_id="empty-event",
            signature_valid=True,
            payload={},
        )
        db.add_all((duplicate, empty))
        db.commit()
        assert whatsapp.process_inbox_event(db, duplicate.id, Settings(app_env="test")) == {
            "status": "duplicate"
        }
        assert whatsapp.process_inbox_event(db, empty.id, Settings(app_env="test")) == {
            "status": "accepted"
        }
        assert empty.processing_status == "ignored"

    assert whatsapp.extract_inbound_sender({}) is None
    assert whatsapp._message_text({"type": "button", "button": {"text": "Button"}}) == "Button"
    assert (
        whatsapp._message_text(
            {
                "type": "interactive",
                "interactive": {"list_reply": {"title": "List choice"}},
            }
        )
        == "List choice"
    )
    assert whatsapp._interactive_action({"interactive": {}}) is None
    assert (
        whatsapp._interactive_research_consent(
            {"interactive": {"button_reply": {"id": "research_consent:grant:wrong-version"}}},
            Settings(research_consent_policy_version="pilot-v2"),
        )
        is None
    )
    with pytest.raises(PermanentJobError, match="disabled"):
        whatsapp._messaging_provider(Settings(app_env="test", whatsapp_backend="disabled"))
