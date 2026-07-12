from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import ResearchEvent, Tenant
from app.db.session import SessionLocal
from app.providers.redaction import pseudonymize, redact_order_payload, redact_text
from tests.conftest import auth_headers

RAW_PII = (
    "Customer name: Ada Lovelace; shipping address: Plot 12 Admiralty Way, Lekki; "
    "email ada.private@example.com; call +234 800 111 2222; "
    "proof https://payments.example/proof/secret; "
    "WhatsApp message ID: wamid.HBgMNTU1MjM0NTY3ODkwFQIAERgSQUJDREVGR0g=; "
    "order number BB-12345"
)


def test_text_and_structured_redaction_remove_identifiable_pii_without_mutation() -> None:
    redacted = redact_text(RAW_PII)
    for sensitive in (
        "Ada Lovelace",
        "Plot 12 Admiralty Way",
        "ada.private@example.com",
        "+234 800 111 2222",
        "payments.example",
        "wamid.",
        "BB-12345",
    ):
        assert sensitive not in redacted
    assert {"[NAME]", "[ADDRESS]", "[EMAIL]", "[PHONE]", "[URL]"} <= set(
        part.rstrip(";.") for part in redacted.split()
    )
    assert "[WHATSAPP_ID]" in redacted
    assert "[ORDER_ID]" in redacted
    assert redact_text("Help me address: falling sales this month") == (
        "Help me address: falling sales this month"
    )
    assert redact_text("Compare 2026-07-12 with order count 123456") == (
        "Compare 2026-07-12 with order count 123456"
    )

    payload = {
        "customer_details": {"name": "Ada Lovelace", "phone": "+2348001112222"},
        "items": [
            {
                "product_name": "Email marketing workshop",
                "note": "Send the receipt to ada.private@example.com",
            }
        ],
        "metadata": {
            "delivery_address": "Plot 12 Admiralty Way, Lekki",
            "whatsapp_message_id": "wamid.HBgMNTU1MjM0NTY3ODkw",
        },
        "total": "12500.00",
    }
    original = deepcopy(payload)
    safe = redact_order_payload(payload)
    assert payload == original
    assert safe["customer_details"] == "[REDACTED]"
    assert safe["metadata"] == {
        "delivery_address": "[REDACTED]",
        "whatsapp_message_id": "[REDACTED]",
    }
    assert safe["items"][0]["product_name"] == "Email marketing workshop"
    assert safe["items"][0]["note"] == "Send the receipt to [EMAIL]"
    assert safe["total"] == "12500.00"


def test_pseudonyms_are_keyed_deterministic_and_domain_separated() -> None:
    identifier = "56d7ff9d-5e0a-4d17-a927-f0a84176c59e"
    tenant = pseudonymize(identifier, "research-key-a", namespace="tenant")
    assert tenant == pseudonymize(identifier, "research-key-a", namespace="tenant")
    assert tenant.startswith("SME-")
    assert identifier not in tenant and identifier[:8] not in tenant
    assert tenant != pseudonymize(identifier, "research-key-b", namespace="tenant")
    assert tenant != pseudonymize(identifier, "research-key-a", namespace="user")


def test_research_reads_and_exports_reredact_legacy_rows_and_hide_identity_ids(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    chat = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": "Create a private follow-up", "client_message_id": "privacy-hardening"},
    )
    assert chat.status_code == 200, chat.text

    inbound_message_id = chat.json()["inbound_message_id"]
    with SessionLocal() as db:
        event = db.scalar(
            select(ResearchEvent).where(ResearchEvent.agent_message_id == inbound_message_id)
        )
        assert event is not None
        event_id = event.id
        tenant_id = event.tenant_id
        user_id = event.user_id
        previous_text = event.redacted_text
        event.redacted_text = RAW_PII
        db.commit()

    settings = get_settings()
    expected_event = pseudonymize(event_id, settings.field_encryption_key, namespace="event")
    expected_tenant = pseudonymize(tenant_id, settings.field_encryption_key, namespace="tenant")
    expected_user = pseudonymize(user_id, settings.field_encryption_key, namespace="user")
    researcher = auth_headers(client, "+2348099990002")
    try:
        response = client.get(
            "/v1/research/events", headers=researcher, params={"tenant_id": tenant_id}
        )
        assert response.status_code == 200, response.text
        row = next(item for item in response.json() if item["id"] == expected_event)
        assert row["tenant_pseudonym"] == expected_tenant
        assert row["user_pseudonym"] == expected_user
        serialized = str(row)
        for sensitive in (event_id, tenant_id, user_id, "Ada Lovelace", "ada.private@example.com"):
            assert sensitive not in serialized

        created = client.post(
            "/v1/research/exports",
            headers=researcher,
            json={
                "report_type": "sme_usage",
                "filters": {"tenant_id": tenant_id},
                "formats": ["csv", "jsonl"],
            },
        )
        assert created.status_code == 200, created.text
        report_id = created.json()["id"]
        for artifact_format in ("csv", "jsonl"):
            artifact = client.get(
                f"/v1/research/reports/{report_id}/download/{artifact_format}",
                headers=researcher,
            )
            assert artifact.status_code == 200, artifact.text
            exported = artifact.text
            assert expected_event in exported
            assert expected_tenant in exported
            assert expected_user in exported
            for sensitive in (
                event_id,
                tenant_id,
                user_id,
                "Ada Lovelace",
                "Plot 12 Admiralty Way",
                "ada.private@example.com",
                "+234 800 111 2222",
                "payments.example",
                "wamid.",
                "BB-12345",
            ):
                assert sensitive not in exported

        with SessionLocal() as db:
            tenant = db.get(Tenant, tenant_id)
            assert tenant is not None
            tenant.research_consent_status = "withdrawn"
            db.commit()
        withdrawn = client.get(
            "/v1/research/events", headers=researcher, params={"tenant_id": tenant_id}
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json() == []
    finally:
        with SessionLocal() as db:
            tenant = db.get(Tenant, tenant_id)
            if tenant is not None:
                tenant.research_consent_status = "granted"
            event = db.get(ResearchEvent, event_id)
            if event is not None:
                event.redacted_text = previous_text
            db.commit()
