from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.db.models import (
    AuditLog,
    HermesProfile,
    PhoneIdentity,
    SystemError,
    Tenant,
    WhatsappDeliveryEvent,
    WhatsappMessage,
)
from app.db.session import SessionLocal, set_security_context
from app.main import app
from app.providers.hermes_control import HermesControlClient
from tests.conftest import auth_headers


def test_operator_phone_mapping_duplicate_and_concurrent_conflicts_are_stable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    suffix = uuid4().hex[:8]
    phone = "+23486" + suffix.translate(str.maketrans("abcdef", "123456"))[:8]
    tenant = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json={"slug": f"phone-map-{suffix}", "name": "Phone Mapping"},
    )
    user = client.post(
        f"/v1/admin/tenants/{tenant.json()['id']}/users",
        headers=operator,
        json={"name": "Phone Owner", "phone_e164": phone, "role": "owner"},
    )
    endpoint = f"/v1/admin/tenants/{tenant.json()['id']}/phones"

    duplicate = client.post(
        endpoint,
        headers=operator,
        json={"user_id": user.json()["user_id"], "phone_e164": "+2348012345678"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "WhatsApp number is already approved"}

    original_flush = Session.flush
    conflict_raised = False

    def conflict_on_phone(self: Session, *args, **kwargs) -> None:
        nonlocal conflict_raised
        if not conflict_raised and any(isinstance(row, PhoneIdentity) for row in self.new):
            conflict_raised = True
            raise IntegrityError("INSERT phone_identities", {}, RuntimeError("unique race"))
        original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", conflict_on_phone)
    raced = client.post(
        endpoint,
        headers=operator,
        json={"user_id": user.json()["user_id"], "phone_e164": phone},
    )
    assert conflict_raised
    assert raced.status_code == 409
    assert raced.json() == {"detail": "WhatsApp number is already approved"}

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        assert db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone)) is None


def test_tenant_operations_is_operator_only_and_never_exposes_secrets_or_raw_phones(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    owner = auth_headers(client, "+2348012345678")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]

    forbidden = client.get(f"/v1/admin/tenants/{tenant_id}/operations", headers=owner)
    assert forbidden.status_code == 403
    response = client.get(f"/v1/admin/tenants/{tenant_id}/operations", headers=operator)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tenant_id"] == tenant_id
    assert payload["people"]
    assert payload["people"][0]["phone_masked"].endswith("5678")
    assert payload["phones"][0]["phone_masked"].endswith("5678")
    assert payload["bumpa"]["scope_id_last4"]
    assert payload["hermes"]["status"]
    serialized = response.text.lower()
    assert "+2348012345678" not in serialized
    assert "encrypted_api_key" not in serialized
    assert "api_internal_url" not in serialized
    assert "profile_path" not in serialized


def test_operator_sync_requires_confirmation_and_idempotency_and_is_audited(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    owner = auth_headers(client, "+2348012345678")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    body = {
        "date_from": "2026-06-01",
        "date_to": "2026-06-30",
        "reason": "operator_requested_refresh",
        "confirmation": "trigger_bumpa_sync",
    }

    assert (
        client.post(
            f"/v1/admin/tenants/{tenant_id}/bumpa/sync",
            headers=operator,
            json=body,
        ).status_code
        == 422
    )
    key = f"admin-test-{uuid4()}"
    headers = {**operator, "Idempotency-Key": key}
    queued = client.post(
        f"/v1/admin/tenants/{tenant_id}/bumpa/sync",
        headers=headers,
        json=body,
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["duplicate"] is False
    duplicate = client.post(
        f"/v1/admin/tenants/{tenant_id}/bumpa/sync",
        headers=headers,
        json=body,
    )
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["job_id"] == queued.json()["job_id"]
    assert duplicate.json()["duplicate"] is True

    conflicting = client.post(
        f"/v1/admin/tenants/{tenant_id}/bumpa/sync",
        headers=headers,
        json={**body, "date_from": "2026-05-01"},
    )
    assert conflicting.status_code == 409
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        audits = db.scalars(
            select(AuditLog).where(
                AuditLog.action == "tenant.bumpa_sync.requested",
                AuditLog.resource_id == queued.json()["job_id"],
            )
        ).all()
        assert len(audits) == 2
        assert all(key not in str(row.after) for row in audits)
        assert all(row.after and row.after["reason"] == body["reason"] for row in audits)


def test_provider_failure_views_are_bounded_and_filterable(client: TestClient) -> None:
    operator = auth_headers(client, "+2348099990001")
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "demo-store"))
        assert tenant is not None
        message = WhatsappMessage(
            tenant_id=tenant.id,
            idempotency_key=f"admin-failure-{uuid4()}",
            meta_message_id=f"wamid.private.{uuid4()}",
            phone_e164="+2348012345678",
            direction="outbound",
            message_type="text",
            text_body="private message must not escape",
            payload={"access_token": "private"},
            status="failed",
        )
        db.add(message)
        db.flush()
        delivery = WhatsappDeliveryEvent(
            whatsapp_message_id=message.id,
            meta_message_id=message.meta_message_id or "",
            status="failed",
            event_timestamp=str(uuid4()),
            payload={
                "errors": [
                    {
                        "code": 131000,
                        "title": "Message failed to send",
                        "error_data": {"details": "+2348012345678 private"},
                    }
                ]
            },
        )
        hermes_error = SystemError(
            tenant_id=tenant.id,
            service="hermes",
            severity="warning",
            message="Hermes call failed: private upstream detail",
            stack="secret stack",
            error_metadata={
                "category": "hermes_unavailable",
                "retryable": True,
                "profile_id": str(uuid4()),
                "prompt": "private prompt",
            },
        )
        db.add_all((delivery, hermes_error))
        db.commit()

    whatsapp = client.get(
        f"/v1/admin/system/whatsapp-delivery-failures?tenant_id={tenant.id}",
        headers=operator,
    )
    assert whatsapp.status_code == 200, whatsapp.text
    whatsapp_row = next(row for row in whatsapp.json() if row["id"] == delivery.id)
    assert whatsapp_row["provider_error_code"] == "131000"
    assert whatsapp_row["provider_error_title"] == "Message failed to send"
    assert whatsapp_row["phone_masked"].endswith("5678")
    assert "wamid.private" not in whatsapp.text
    assert "private message" not in whatsapp.text
    assert "+2348012345678" not in whatsapp.text

    hermes = client.get(
        f"/v1/admin/system/hermes-call-errors?tenant_id={tenant.id}",
        headers=operator,
    )
    assert hermes.status_code == 200, hermes.text
    hermes_row = next(row for row in hermes.json() if row["id"] == hermes_error.id)
    assert hermes_row["category"] == "hermes_unavailable"
    assert hermes_row["retryable"] is True
    assert len(hermes_row["profile_reference"]) == 12
    assert "private upstream" not in hermes.text
    assert "private prompt" not in hermes.text


def test_admin_export_is_server_generated_csv_safe_and_audited(client: TestClient) -> None:
    operator = auth_headers(client, "+2348099990001")
    suffix = uuid4().hex[:8]
    created = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json={"slug": f"export-{suffix}", "name": '=HYPERLINK("unsafe")'},
    )
    assert created.status_code == 201, created.text

    exported = client.post(
        "/v1/admin/exports",
        headers=operator,
        json={
            "scope": "tenant_operations",
            "format": "csv",
            "confirmation": "generate_admin_export",
        },
    )
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload["filename"].endswith(".csv")
    assert payload["content_type"] == "text/csv"
    assert payload["row_count"] >= 1
    assert "'=HYPERLINK" in payload["content"]
    assert "encrypted_api_key" not in payload["content"]
    assert "+2348012345678" not in payload["content"]

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        row = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "admin.export.generated",
                AuditLog.resource_id == payload["export_id"],
            )
        )
        assert row is not None
        assert row.after == {
            "format": "csv",
            "scope": "tenant_operations",
            "row_count": payload["row_count"],
            "checksum_sha256": payload["checksum_sha256"],
        }


def test_hermes_restart_uses_profile_auth_only_and_audits(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    suffix = uuid4().hex[:8]
    created = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json={"slug": f"restart-{suffix}", "name": "Restart Test"},
    )
    tenant_id = created.json()["id"]
    field_key = "admin-hermes-field-key-with-at-least-32-characters"
    profile_key = "profile-control-key-that-must-never-be-returned"
    settings = Settings(
        app_env="test",
        agent_backend="hermes",
        field_encryption_key=field_key,
        hermes_profile_root=tmp_path,
    )
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        used_ports = set(db.scalars(select(HermesProfile.api_port)).all())
        api_port = next(port for port in range(8700, 9000) if port not in used_ports)
        profile = HermesProfile(
            tenant_id=tenant_id,
            profile_name=f"tenant_restart_{suffix}",
            profile_path=str(tmp_path / f"tenant_restart_{suffix}"),
            provider="hermes",
            api_internal_url=f"http://hermes:{api_port}/v1",
            api_port=api_port,
            encrypted_api_key=FieldCipher(field_key).encrypt(profile_key),
            status="degraded",
        )
        db.add(profile)
        db.commit()
        profile_id = profile.id

    captured: dict[str, str] = {}

    class FakeControlClient:
        def __init__(self, supplied: Settings) -> None:
            assert supplied is settings

        def restart(self, *, profile_name: str, api_key: str) -> object:
            captured.update({"profile_name": profile_name, "api_key": api_key})
            return object()

    monkeypatch.setattr("app.routes.admin.HermesControlClient", FakeControlClient)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = client.post(
            f"/v1/admin/tenants/{tenant_id}/hermes-profile/restart",
            headers=operator,
            json={
                "reason": "profile_unresponsive",
                "confirmation": "restart_hermes_profile",
            },
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 200, response.text
    assert response.json()["control_status"] == "restarted"
    assert captured["api_key"] == profile_key
    assert profile_key not in response.text

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        profile = db.get(HermesProfile, profile_id)
        assert profile is not None and profile.status == "active"
        row = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "hermes.profile.restarted",
                AuditLog.resource_id == profile_id,
            )
        )
        assert row is not None
        assert row.after == {"status": "active", "reason": "profile_unresponsive"}


def test_hermes_control_client_rejects_non_private_and_invalid_responses() -> None:
    settings = Settings(
        app_env="test",
        hermes_base_internal_host="http://hermes",
        hermes_control_port=8699,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = "activated" if request.url.path.endswith("/activate") else "restarted"
        return httpx.Response(200, json={"status": status})

    control = HermesControlClient(
        settings,
        transport=httpx.MockTransport(handler),
    )
    result = control.restart(profile_name="tenant_safe", api_key="profile-key")
    activated = control.activate(profile_name="tenant_safe", api_key="profile-key")
    assert result.status == "restarted"
    assert activated.status == "activated"
    assert seen[0].url == "http://hermes:8699/v1/profiles/tenant_safe/restart"
    assert seen[0].headers["authorization"] == "Bearer profile-key"
    assert seen[0].content == b'{"confirmation":"restart"}'
    assert seen[1].url == "http://hermes:8699/v1/profiles/tenant_safe/activate"
    assert seen[1].headers["authorization"] == "Bearer profile-key"
    assert seen[1].content == b'{"confirmation":"activate"}'

    invalid = Settings(app_env="test", hermes_base_internal_host="http://localhost")
    with pytest.raises(Exception, match="private runtime boundary"):
        HermesControlClient(invalid).restart(profile_name="tenant_safe", api_key="profile-key")
