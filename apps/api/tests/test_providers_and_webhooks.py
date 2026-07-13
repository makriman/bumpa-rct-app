import hashlib
import hmac
import json
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.crypto import FieldCipher
from app.db.models import (
    AgentMessage,
    Conversation,
    ResearchEvent,
    Tenant,
    TenantMembership,
    WebhookEvent,
    WhatsappMessage,
)
from app.db.session import SessionLocal
from app.providers.redaction import parse_money, redact_order_payload, redact_text
from app.services.research_events import research_event_key


def test_money_redaction_and_field_encryption() -> None:
    assert parse_money("₦1,234.50") == Decimal("1234.50")
    assert parse_money(12.3) == Decimal("12.3")
    assert parse_money("unavailable") is None
    assert redact_order_payload({"customer_details": {"phone": "x"}, "total": "12"}) == {
        "customer_details": "[REDACTED]",
        "total": "12",
    }
    assert "[EMAIL]" in redact_text("contact me@example.com")
    cipher = FieldCipher("test-key")
    encrypted = cipher.encrypt("provider-secret")
    assert "provider-secret" not in encrypted
    assert cipher.decrypt(encrypted) == "provider-secret"


def _webhook_payload(message_id: str, sender: str, text: str) -> bytes:
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": sender,
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()


def _signature(body: bytes) -> str:
    return "sha256=" + hmac.new(b"local-meta-app-secret", body, hashlib.sha256).hexdigest()


def test_whatsapp_verification_signature_routing_and_dedupe(client: TestClient) -> None:
    verify = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "local-webhook-token",
            "hub.challenge": "42",
        },
    )
    assert verify.status_code == 200 and verify.text == "42"
    body = _webhook_payload("wa-test-1", "2348012345678", "How are sales?")
    rejected = client.post("/webhooks/whatsapp", content=body)
    assert rejected.status_code == 403
    accepted = client.post(
        "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": _signature(body)}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    duplicate = client.post(
        "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": _signature(body)}
    )
    assert duplicate.json()["status"] == "duplicate"


def test_whatsapp_replay_lookup_is_tenant_scoped(client: TestClient) -> None:
    shared_external_id = "wa-cross-tenant-replay-boundary"
    private_response = "TENANT_A_PRIVATE_RESPONSE_MUST_NEVER_LEAVE_TENANT_A"
    with SessionLocal() as db:
        tenant_a = db.scalar(select(Tenant).where(Tenant.slug == "demo-store"))
        tenant_b = db.scalar(select(Tenant).where(Tenant.slug == "other-store"))
        assert tenant_a is not None and tenant_b is not None
        owner_a = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_a.id,
                TenantMembership.role == "owner",
            )
        )
        assert owner_a is not None
        conversation_a = Conversation(
            tenant_id=tenant_a.id,
            user_id=owner_a.user_id,
            channel="whatsapp",
            title="Private tenant A conversation",
        )
        db.add(conversation_a)
        db.flush()
        inbound_a = AgentMessage(
            tenant_id=tenant_a.id,
            user_id=owner_a.user_id,
            conversation_id=conversation_a.id,
            channel="whatsapp",
            direction="inbound",
            content="Private tenant A question",
            redacted_content="Private tenant A question",
            external_message_id=shared_external_id,
        )
        db.add(inbound_a)
        db.flush()
        db.add(
            AgentMessage(
                tenant_id=tenant_a.id,
                user_id=owner_a.user_id,
                conversation_id=conversation_a.id,
                channel="whatsapp",
                direction="outbound",
                content=private_response,
                redacted_content=private_response,
            )
        )
        db.commit()
        tenant_b_id = tenant_b.id

    body = _webhook_payload(
        shared_external_id,
        "2348012345679",
        "Give me my own store summary",
    )
    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": _signature(body)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"

    with SessionLocal() as db:
        event_b = db.scalar(
            select(WebhookEvent).where(
                WebhookEvent.provider == "whatsapp",
                WebhookEvent.external_event_id == shared_external_id,
            )
        )
        assert event_b is not None
        inbound_b = db.scalar(
            select(AgentMessage).where(
                AgentMessage.tenant_id == tenant_b_id,
                AgentMessage.channel == "whatsapp",
                AgentMessage.direction == "inbound",
                AgentMessage.external_message_id == shared_external_id,
            )
        )
        assert inbound_b is not None
        outbound_b = db.scalar(
            select(AgentMessage).where(
                AgentMessage.tenant_id == tenant_b_id,
                AgentMessage.conversation_id == inbound_b.conversation_id,
                AgentMessage.direction == "outbound",
            )
        )
        delivered_b = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.idempotency_key
                == "whatsapp:" + hashlib.sha256(f"{event_b.id}\0agent-reply".encode()).hexdigest(),
            )
        )
        assert outbound_b is not None and delivered_b is not None
        assert delivered_b.tenant_id == tenant_b_id
        assert delivered_b.phone_e164 == "+2348012345679"
        assert delivered_b.direction == "outbound"
        assert delivered_b.text_body == outbound_b.content
        assert private_response not in delivered_b.text_body


def test_whatsapp_unknown_sender_and_opt_out(client: TestClient) -> None:
    unknown = _webhook_payload("wa-test-unknown", "2348111111111", "Hello")
    response = client.post(
        "/webhooks/whatsapp", content=unknown, headers={"x-hub-signature-256": _signature(unknown)}
    )
    assert response.json()["status"] == "rejected_unknown_sender"
    stop = _webhook_payload("wa-test-stop", "2348012345678", "STOP")
    assert (
        client.post(
            "/webhooks/whatsapp", content=stop, headers={"x-hub-signature-256": _signature(stop)}
        ).json()["status"]
        == "opted_out"
    )
    start = _webhook_payload("wa-test-start", "2348012345678", "START")
    assert (
        client.post(
            "/webhooks/whatsapp", content=start, headers={"x-hub-signature-256": _signature(start)}
        ).json()["status"]
        == "opted_in"
    )
    with SessionLocal() as db:
        tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "demo-store"))
        assert tenant_id is not None
        opted_out = db.scalar(
            select(ResearchEvent).where(
                ResearchEvent.idempotency_key
                == research_event_key("user_opted_out", tenant_id, "wa-test-stop")
            )
        )
        opted_in = db.scalar(
            select(ResearchEvent).where(
                ResearchEvent.idempotency_key
                == research_event_key("user_opted_in", tenant_id, "wa-test-start")
            )
        )
        assert opted_out is not None and opted_out.business_outcome["opt_out"] is True
        assert opted_in is not None and opted_in.business_outcome["opt_out"] is False


def test_whatsapp_delivery_status_is_recorded(client: TestClient) -> None:
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {
                                        "id": "local-outbound",
                                        "status": "delivered",
                                        "timestamp": "42",
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    response = client.post(
        "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": _signature(body)}
    )
    assert response.status_code == 200
    assert response.json()["event_type"] == "delivery_status"
    assert (
        client.post(
            "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": _signature(body)}
        ).json()["status"]
        == "duplicate"
    )


def test_failed_webhook_event_remains_retriable(client: TestClient, monkeypatch) -> None:
    from app.services import whatsapp

    body = _webhook_payload("wa-retry-1", "2348012345678", "Retry safely")
    original = whatsapp.handle_chat
    monkeypatch.setattr(
        whatsapp,
        "handle_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    failed = client.post(
        "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": _signature(body)}
    )
    assert failed.status_code == 503
    monkeypatch.setattr(whatsapp, "handle_chat", original)
    retried = client.post(
        "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": _signature(body)}
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "accepted"
