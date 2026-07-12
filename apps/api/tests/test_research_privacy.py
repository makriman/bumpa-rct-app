from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.time import utcnow
from app.db.models import (
    Artifact,
    AsyncJob,
    AuditLog,
    JobOutbox,
    PlatformRole,
    ResearchEvent,
    ResearchReport,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.jobs.handlers import research_report_handler
from app.main import app
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
        assert tenant_id is not None and user_id is not None
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


def test_research_conversations_group_redacted_events_behind_pseudonymous_ids(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    first = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": "Compare sales this week", "client_message_id": "research-conv-1"},
    )
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]
    second = client.post(
        "/v1/chat/web",
        headers=owner,
        json={
            "message": "What should I restock next?",
            "conversation_id": conversation_id,
            "client_message_id": "research-conv-2",
        },
    )
    assert second.status_code == 200, second.text

    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.conversation_id == conversation_id)
                .order_by(ResearchEvent.created_at)
            ).all()
        )
        assert len(events) == 2
        tenant_id = events[0].tenant_id
        user_id = events[0].user_id
        assert tenant_id is not None and user_id is not None
        event_ids = [event.id for event in events]
        previous_text = events[0].redacted_text
        events[0].redacted_text = RAW_PII
        db.commit()

    settings = get_settings()
    expected_conversation = pseudonymize(
        conversation_id, settings.field_encryption_key, namespace="conversation"
    )
    expected_tenant = pseudonymize(tenant_id, settings.field_encryption_key, namespace="tenant")
    expected_user = pseudonymize(user_id, settings.field_encryption_key, namespace="user")
    researcher = auth_headers(client, "+2348099990002")
    try:
        denied = client.get("/v1/research/conversations", headers=owner)
        assert denied.status_code == 403

        listed = client.get("/v1/research/conversations", headers=researcher)
        assert listed.status_code == 200, listed.text
        summary = next(row for row in listed.json() if row["id"] == expected_conversation)
        assert summary["tenant_pseudonym"] == expected_tenant
        assert summary["participant_pseudonyms"] == [expected_user]
        assert summary["event_count"] == 2
        assert summary["latest_redacted_text"] == "What should I restock next?"

        detail = client.get(
            f"/v1/research/conversations/{expected_conversation}", headers=researcher
        )
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["id"] == expected_conversation
        assert len(payload["events"]) == 2
        assert payload["events"][0]["redacted_text"] != RAW_PII
        serialized = str(payload)
        for sensitive in (
            conversation_id,
            tenant_id,
            user_id,
            *event_ids,
            "Ada Lovelace",
            "ada.private@example.com",
            "+234 800 111 2222",
            "payments.example",
            "wamid.",
            "BB-12345",
        ):
            assert sensitive not in serialized

        raw_identifier = client.get(
            f"/v1/research/conversations/{conversation_id}", headers=researcher
        )
        assert raw_identifier.status_code == 404

        with SessionLocal() as db:
            tenant = db.get(Tenant, tenant_id)
            assert tenant is not None
            tenant.research_consent_status = "withdrawn"
            db.commit()
        withdrawn_list = client.get("/v1/research/conversations", headers=researcher)
        assert withdrawn_list.status_code == 200
        assert expected_conversation not in {row["id"] for row in withdrawn_list.json()}
        withdrawn_detail = client.get(
            f"/v1/research/conversations/{expected_conversation}", headers=researcher
        )
        assert withdrawn_detail.status_code == 404
    finally:
        with SessionLocal() as db:
            tenant = db.get(Tenant, tenant_id)
            if tenant is not None:
                tenant.research_consent_status = "granted"
            event = db.get(ResearchEvent, event_ids[0])
            if event is not None:
                event.redacted_text = previous_text
            db.commit()


def test_raw_question_access_is_superadmin_only_reason_gated_non_cacheable_and_audited(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    chat = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": "Private launch review question", "client_message_id": "raw-audit"},
    )
    assert chat.status_code == 200, chat.text
    inbound_id = chat.json()["inbound_message_id"]
    with SessionLocal() as db:
        event = db.scalar(select(ResearchEvent).where(ResearchEvent.agent_message_id == inbound_id))
        assert event is not None
        event_id = event.id
        tenant_id = event.tenant_id

    researcher = auth_headers(client, "+2348099990002")
    denied = client.get(
        f"/v1/research/events/{event_id}/raw",
        headers={**researcher, "X-Access-Reason": "Investigate approved study anomaly"},
    )
    assert denied.status_code == 403

    superadmin = auth_headers(client, "+2348099990000")
    assert client.get(f"/v1/research/events/{event_id}/raw", headers=superadmin).status_code == 422
    unsafe_reason = client.get(
        f"/v1/research/events/{event_id}/raw",
        headers={**superadmin, "X-Access-Reason": "Contact reviewer@example.com about anomaly"},
    )
    assert unsafe_reason.status_code == 422

    disclosed = client.get(
        f"/v1/research/events/{event_id}/raw",
        headers={**superadmin, "X-Access-Reason": "Investigate approved study anomaly"},
    )
    assert disclosed.status_code == 200, disclosed.text
    assert disclosed.json()["raw_question"] == "Private launch review question"
    assert disclosed.headers["cache-control"] == "no-store, max-age=0"
    with SessionLocal() as db:
        record = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "research.raw_event.accessed",
                AuditLog.resource_id == event_id,
            )
            .order_by(AuditLog.created_at.desc())
        )
        assert record is not None
        assert record.tenant_id == tenant_id
        assert record.after == {
            "reason": "Investigate approved study anomaly",
            "fields": ["raw_question"],
            "request_scoped": True,
        }


def test_report_filters_ownership_expiry_deletion_and_consent_revalidation(
    client: TestClient,
) -> None:
    researcher = auth_headers(client, "+2348099990002")
    invalid = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "sme_usage", "filters": {"include_raw": "true"}},
    )
    assert invalid.status_code == 422
    empty_formats = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "sme_usage", "formats": []},
    )
    assert empty_formats.status_code == 422

    with SessionLocal() as db:
        tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "demo-store"))
        operator = db.scalar(select(User).where(User.primary_phone_e164 == "+2348099990001"))
        assert tenant_id and operator
        secondary_role = PlatformRole(user_id=operator.id, role="researcher")
        db.add(secondary_role)
        db.commit()
        secondary_role_id = secondary_role.id

    created = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={
            "report_type": "sme_usage",
            "filters": {"tenant_id": tenant_id},
            "formats": ["csv"],
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    try:
        second_researcher = auth_headers(client, "+2348099990001")
        assert (
            client.get(f"/v1/research/reports/{report_id}", headers=second_researcher).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/research/reports/{report_id}/download/csv", headers=second_researcher
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/v1/research/reports/{report_id}", headers=second_researcher
            ).status_code
            == 404
        )

        metadata = client.get(f"/v1/research/reports/{report_id}", headers=researcher)
        assert metadata.status_code == 200
        assert metadata.json()["expired"] is False
        assert metadata.json()["expires_at"]

        owner = auth_headers(client, "+2348012345678")
        withdrawn = client.post(
            "/v1/tenants/current/research-consent",
            headers=owner,
            json={"status": "withdrawn", "policy_version": "privacy-test-v1"},
        )
        assert withdrawn.status_code == 200
        revoked = client.get(f"/v1/research/reports/{report_id}/download/csv", headers=researcher)
        assert revoked.status_code == 410
        assert (
            client.post(
                "/v1/tenants/current/research-consent",
                headers=owner,
                json={"status": "granted", "policy_version": "privacy-test-v1"},
            ).status_code
            == 200
        )
        # A later re-grant must not resurrect bytes captured before withdrawal.
        assert (
            client.get(
                f"/v1/research/reports/{report_id}/download/csv", headers=researcher
            ).status_code
            == 410
        )
    finally:
        with SessionLocal() as db:
            role = db.get(PlatformRole, secondary_role_id)
            if role:
                db.delete(role)
            db.commit()

    expiring = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "sme_usage", "formats": ["csv"]},
    )
    assert expiring.status_code == 201
    expiring_id = expiring.json()["id"]
    with SessionLocal() as db:
        report = db.get(ResearchReport, expiring_id)
        assert report is not None
        report.created_at = utcnow() - timedelta(days=2)
        artifact = db.scalar(select(Artifact).where(Artifact.report_id == expiring_id))
        assert artifact is not None
        artifact_path = get_settings().artifact_root / artifact.storage_key
        assert artifact_path.exists()
        db.commit()
    expired = client.get(f"/v1/research/reports/{expiring_id}/download/csv", headers=researcher)
    assert expired.status_code == 410
    assert not artifact_path.exists()
    with SessionLocal() as db:
        assert db.scalar(select(Artifact).where(Artifact.report_id == expiring_id)) is None

    removable = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "sme_usage", "formats": ["csv"]},
    )
    removable_id = removable.json()["id"]
    assert (
        client.delete(f"/v1/research/reports/{removable_id}", headers=researcher).status_code == 204
    )
    with SessionLocal() as db:
        assert db.get(ResearchReport, removable_id) is None


def test_production_report_request_is_transactionally_enqueued(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    researcher = auth_headers(client, "+2348099990002")
    production_settings = get_settings().model_copy(update={"app_env": "production"})
    app.dependency_overrides[get_settings] = lambda: production_settings
    monkeypatch.setattr("app.routes.research.enforce_operation_rate_limit", lambda *a, **k: None)
    try:
        monkeypatch.setenv("ASYNC_RUNTIME_ENABLED", "false")
        unavailable = client.post(
            "/v1/research/reports",
            headers=researcher,
            json={"report_type": "question_taxonomy", "formats": ["csv"]},
        )
        assert unavailable.status_code == 503
        monkeypatch.setenv("ASYNC_RUNTIME_ENABLED", "true")
        created = client.post(
            "/v1/research/reports",
            headers=researcher,
            json={"report_type": "question_taxonomy", "formats": ["jsonl", "csv"]},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "queued"
    report_id = created.json()["id"]
    with SessionLocal() as db:
        job = db.scalar(
            select(AsyncJob).where(
                AsyncJob.kind == "research.generate_report",
                AsyncJob.idempotency_key == f"research-report:{report_id}",
            )
        )
        assert job is not None
        assert job.payload == {"report_id": report_id, "formats": ["csv", "jsonl"]}
        assert job.max_attempts == 3
        outbox = db.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox is not None and outbox.status == "pending"
        result = research_report_handler(db, job)
        assert result == {
            "report_id": report_id,
            "status": "success",
            "formats": ["csv", "jsonl"],
        }
        assert len(list(db.scalars(select(Artifact).where(Artifact.report_id == report_id)))) == 2
