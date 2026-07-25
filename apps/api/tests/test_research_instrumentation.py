from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import (
    AgentMessage,
    GeneratedAgentMedia,
    HermesProfile,
    ResearchEvent,
    ResearchReport,
    SystemError,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.providers.hermes import HermesEndpoint, HermesResult, HermesUnavailable
from app.services.audit import audit
from app.services.chat import handle_chat
from app.services.research_events import (
    record_report_generated_events,
    record_research_event,
)
from tests.conftest import auth_headers


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _tenant(db: Session, slug: str, *, consent: str) -> Tenant:
    tenant = Tenant(
        slug=slug,
        name=f"{slug} store",
        research_consent_status=consent,
    )
    db.add(tenant)
    db.flush()
    return tenant


def test_research_writer_is_consent_gated_tenant_scoped_and_idempotent(db: Session) -> None:
    granted = _tenant(db, "research-granted", consent="granted")
    pending = _tenant(db, "research-pending", consent="pending")
    other = _tenant(db, "research-other", consent="granted")

    assert (
        record_research_event(
            db,
            tenant_id=pending.id,
            event_type="user_message_received",
            source_parts=("reused-client-id",),
            channel="web",
        )
        is None
    )
    first = record_research_event(
        db,
        tenant_id=granted.id,
        event_type="user_message_received",
        source_parts=("reused-client-id",),
        channel="web",
        redacted_text="How are sales?",
        language="en",
        agent_confidence="medium",
        response_latency_ms=0,
        follow_up_detected=False,
    )
    duplicate = record_research_event(
        db,
        tenant_id=granted.id,
        event_type="user_message_received",
        source_parts=("reused-client-id",),
        channel="web",
    )
    independent_tenant = record_research_event(
        db,
        tenant_id=other.id,
        event_type="user_message_received",
        source_parts=("reused-client-id",),
        channel="web",
    )
    db.flush()

    assert first is not None
    assert duplicate is first
    assert independent_tenant is not None
    assert independent_tenant.idempotency_key != first.idempotency_key
    assert len(db.scalars(select(ResearchEvent)).all()) == 2
    assert first.pii_redacted is True
    assert first.raw_text_present is False
    assert first.language == "en"
    assert first.response_latency_ms == 0

    granted.research_consent_status = "withdrawn"
    assert (
        record_research_event(
            db,
            tenant_id=granted.id,
            event_type="assistant_response_sent",
            source_parts=("after-withdrawal",),
            channel="web",
        )
        is None
    )


def test_research_writer_redacts_text_secrets_and_structured_metadata(db: Session) -> None:
    tenant = _tenant(db, "research-redaction", consent="granted")
    raw_phone = "+234 800 111 2222"
    raw_email = "owner.private@example.com"
    raw_url = "https://payments.example/proof/private"
    raw_token = "sk-ant-this-token-must-not-survive"
    event = record_research_event(
        db,
        tenant_id=tenant.id,
        event_type="assistant_response_sent",
        source_parts=("private-provider-id",),
        channel="whatsapp",
        redacted_text=(
            f"Email {raw_email}, call {raw_phone}, proof {raw_url}, credential {raw_token}"
        ),
        business_outcome={
            "status": "completed",
            "access_token": raw_token,
            "operator_email": raw_email,
            "safe_note": f"Contact {raw_phone} or {raw_email}",
            "nested": {"callback_url": raw_url, "count": 3},
        },
        quality_flags=("fallback_used", "fallback_used"),
    )
    db.flush()

    assert event is not None
    serialized = json.dumps(
        {
            "text": event.redacted_text,
            "outcome": event.business_outcome,
            "compatibility_outcome": event.outcome,
        }
    )
    for sensitive in (raw_phone, raw_email, raw_url, raw_token, "payments.example"):
        assert sensitive not in serialized
    assert event.business_outcome["access_token"] == "[REDACTED]"
    assert event.business_outcome["operator_email"] == "[REDACTED]"
    assert event.business_outcome["nested"]["callback_url"] == "[REDACTED]"
    assert event.quality_flags == ["fallback_used"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"response_length_chars": -1}, "response_length_chars"),
        ({"response_latency_ms": True}, "response_latency_ms"),
        ({"language": "english"}, "language"),
        ({"agent_confidence": "certain"}, "confidence"),
        ({"business_outcome": {"Bad-Key": "value"}}, "metadata key"),
        ({"quality_flags": ("contains spaces",)}, "quality flag"),
    ),
)
def test_research_writer_rejects_unbounded_or_invalid_dimensions(
    db: Session, kwargs: dict[str, object], message: str
) -> None:
    tenant = _tenant(db, f"invalid-{message.replace(' ', '-')}", consent="granted")
    with pytest.raises(ValueError, match=message):
        record_research_event(
            db,
            tenant_id=tenant.id,
            event_type="assistant_response_sent",
            source_parts=(message,),
            channel="web",
            **kwargs,  # type: ignore[arg-type]
        )


def test_report_and_admin_events_are_consent_gated_and_exactly_once(db: Session) -> None:
    granted = _tenant(db, "artifact-granted", consent="granted")
    withdrawn = _tenant(db, "artifact-withdrawn", consent="withdrawn")
    report = ResearchReport(
        report_type="sme_usage",
        artifact_kind="export",
        filters={},
        status="success",
    )
    db.add(report)
    db.flush()

    first = record_report_generated_events(
        db,
        report=report,
        tenant_ids=(withdrawn.id, granted.id, granted.id),
        event_count=12,
        formats=("jsonl", "csv", "csv"),
    )
    duplicate = record_report_generated_events(
        db,
        report=report,
        tenant_ids=(granted.id,),
        event_count=12,
        formats=("csv", "jsonl"),
    )
    audit_record = audit(
        db,
        tenant_id=granted.id,
        actor_user_id=None,
        action="tenant.settings.updated",
        resource_type="tenant",
        resource_id=granted.id,
        before={"name": "private-before"},
        after={"name": "private-after"},
    )
    audit(
        db,
        tenant_id=withdrawn.id,
        actor_user_id=None,
        action="tenant.settings.updated",
        resource_type="tenant",
        resource_id=withdrawn.id,
    )
    db.flush()

    assert len(first) == len(duplicate) == 1
    assert first[0] is duplicate[0]
    assert first[0].event_type == "export_generated"
    assert first[0].business_outcome == {
        "artifact_count": 2,
        "artifact_kind": "export",
        "event_count": 12,
        "formats": ["csv", "jsonl"],
        "report_type": "sme_usage",
        "status": "success",
    }
    admin_event = db.scalar(
        select(ResearchEvent).where(
            ResearchEvent.event_type == "admin_action",
            ResearchEvent.tenant_id == granted.id,
        )
    )
    assert admin_event is not None
    assert admin_event.business_outcome == {
        "action": "tenant.settings.updated",
        "resource_type": "tenant",
        "status": "completed",
    }
    assert "private-before" not in json.dumps(admin_event.business_outcome)
    assert audit_record.id not in admin_event.idempotency_key
    assert (
        db.scalar(
            select(ResearchEvent).where(
                ResearchEvent.event_type == "admin_action",
                ResearchEvent.tenant_id == withdrawn.id,
            )
        )
        is None
    )


def test_failed_hermes_call_persists_only_sanitized_diagnostics_and_events(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _tenant(db, "hermes-failure", consent="granted")
    user = User(primary_phone_e164="+2348000009090")
    settings = get_settings().model_copy(update={"agent_backend": "hermes"})
    profile = HermesProfile(
        tenant_id=tenant.id,
        profile_name="tenant_hermes_failure",
        profile_path="/var/lib/hermes/profiles/hermes-failure",
        provider="hermes",
        api_internal_url="http://hermes:8700/v1",
        api_port=8700,
        encrypted_api_key=FieldCipher(settings.field_encryption_key).encrypt("private-api-key"),
        status="active",
    )
    db.add_all((user, profile))
    db.commit()

    private_cause = "upstream-body owner.private@example.com sk-ant-never-persist"

    class FailingHermesClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def respond(
            self,
            _endpoint: HermesEndpoint,
            *,
            message: str,
            business_context: str,
        ) -> None:
            assert "owner.private@example.com" not in message
            assert "private-api-key" not in business_context
            error = HermesUnavailable("Hermes profile is unreachable")
            error.__cause__ = RuntimeError(private_cause)
            raise error

    monkeypatch.setattr("app.services.chat.HermesClient", FailingHermesClient)
    conversation, inbound, outbound, _freshness = handle_chat(
        db,
        tenant=tenant,
        user=user,
        message="Help owner.private@example.com with sales",
        channel="web",
        external_message_id="client-private-id",
        settings=settings,
    )

    assert conversation.id == inbound.conversation_id == outbound.conversation_id
    assert "consultant service is temporarily unavailable" in outbound.content
    assert "haven’t guessed or taken any external action" in outbound.content
    assert len(db.scalars(select(AgentMessage)).all()) == 2
    system_error = db.scalar(select(SystemError))
    assert system_error is not None
    assert system_error.tenant_id == tenant.id
    assert system_error.service == "hermes"
    assert system_error.severity == "error"
    assert system_error.message == "Hermes call failed"
    assert system_error.stack is None
    assert system_error.error_metadata == {
        "category": "hermes_unavailable",
        "profile_id": profile.id,
    }

    events = list(db.scalars(select(ResearchEvent).order_by(ResearchEvent.created_at)).all())
    assert [event.event_type for event in events] == [
        "user_message_received",
        "bumpa_context_built",
        "hermes_call_started",
        "hermes_call_failed",
        "research_classification_completed",
        "assistant_response_sent",
    ]
    assert all(event.raw_text_present is False for event in events[:-1])
    assert events[-1].raw_text_present is True
    failed = next(event for event in events if event.event_type == "hermes_call_failed")
    assert failed.business_outcome == {
        "error_code": "hermes_unavailable",
        "provider": "hermes",
        "retryable": True,
        "status": "failed",
    }
    assert failed.quality_flags == ["hermes_unavailable"]
    serialized = json.dumps(
        {
            "system_error": {
                "message": system_error.message,
                "stack": system_error.stack,
                "metadata": system_error.error_metadata,
            },
            "events": [
                {
                    "text": event.redacted_text,
                    "outcome": event.business_outcome,
                    "flags": event.quality_flags,
                    "key": event.idempotency_key,
                }
                for event in events
            ],
        }
    )
    for sensitive in (
        private_cause,
        "owner.private@example.com",
        "sk-ant-never-persist",
        "private-api-key",
        "client-private-id",
    ):
        assert sensitive not in serialized


def test_chat_persists_the_initiating_message_for_cross_transaction_mcp_media(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = _tenant(db, "mcp-media-transaction", consent="pending")
    user = User(primary_phone_e164="+2348000009091")
    settings = get_settings().model_copy(
        update={
            "agent_backend": "hermes",
            "agent_capabilities_v2": True,
        }
    )
    profile = HermesProfile(
        tenant_id=tenant.id,
        profile_name="tenant_mcp_media_transaction",
        profile_path="/var/lib/hermes/profiles/mcp-media-transaction",
        provider="hermes",
        api_internal_url="http://hermes:8700/v1",
        api_port=8700,
        encrypted_api_key=FieldCipher(settings.field_encryption_key).encrypt("private-api-key"),
        status="active",
    )
    db.add_all((user, profile))
    db.commit()
    secondary_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    class MediaProducingHermesClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def respond(
            self,
            _endpoint: HermesEndpoint,
            *,
            message: str,
            business_context: str,
        ) -> HermesResult:
            assert "Current action context" in business_context
            with secondary_factory() as tool_db:
                initiating = tool_db.scalar(
                    select(AgentMessage).where(
                        AgentMessage.tenant_id == tenant.id,
                        AgentMessage.external_message_id == "media-client-message",
                    )
                )
                assert initiating is not None
                tool_db.add(
                    GeneratedAgentMedia(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        conversation_id=initiating.conversation_id,
                        agent_message_id=initiating.id,
                        channel="web",
                        media_type="document",
                        mime_type="text/csv",
                        filename="weekly-sales.csv",
                        storage_path="generated-media/test/weekly-sales.csv",
                        byte_size=10,
                        checksum_sha256="0" * 64,
                        status="pending",
                    )
                )
                tool_db.commit()
            return HermesResult(
                content="Your weekly sales file is ready.",
                input_tokens=10,
                output_tokens=7,
                total_tokens=17,
                latency_ms=12,
            )

    monkeypatch.setattr(
        "app.services.chat.HermesClient",
        MediaProducingHermesClient,
    )
    _conversation, inbound, outbound, _freshness = handle_chat(
        db,
        tenant=tenant,
        user=user,
        message="Create my weekly sales file.",
        channel="web",
        external_message_id="media-client-message",
        settings=settings,
    )
    media = db.scalar(select(GeneratedAgentMedia))
    assert media is not None
    assert media.agent_message_id == outbound.id
    assert media.agent_message_id != inbound.id


def test_web_client_message_id_is_tenant_scoped_and_replays_within_tenant(
    client: TestClient,
) -> None:
    shared_client_id = f"tenant-isolation-{uuid4().hex}"
    first_owner = auth_headers(client, "+2348012345678")
    second_owner = auth_headers(client, "+2348012345679")
    payload = {
        "message": "Compare sales for my store",
        "client_message_id": shared_client_id,
    }

    first = client.post("/v1/chat/web", headers=first_owner, json=payload)
    second = client.post("/v1/chat/web", headers=second_owner, json=payload)
    replay = client.post("/v1/chat/web", headers=first_owner, json=payload)

    assert first.status_code == second.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["conversation_id"] != second.json()["conversation_id"]
    assert first.json()["inbound_message_id"] != second.json()["inbound_message_id"]
    with SessionLocal() as session:
        inbound_messages = list(
            session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.channel == "web",
                    AgentMessage.direction == "inbound",
                    AgentMessage.external_message_id == shared_client_id,
                )
                .order_by(AgentMessage.tenant_id)
            ).all()
        )
        assert len(inbound_messages) == 2
        assert len({message.tenant_id for message in inbound_messages}) == 2
        research_rows = list(
            session.scalars(
                select(ResearchEvent).where(
                    ResearchEvent.agent_message_id.in_(message.id for message in inbound_messages)
                )
            ).all()
        )
        assert len(research_rows) == 2
        assert len({row.tenant_id for row in research_rows}) == 2
        assert len({row.idempotency_key for row in research_rows}) == 2
