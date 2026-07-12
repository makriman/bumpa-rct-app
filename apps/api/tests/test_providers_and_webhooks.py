import hashlib
import hmac
import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.core.crypto import FieldCipher
from app.providers.redaction import parse_money, redact_order_payload, redact_text


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
    from app.routes import whatsapp

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
