from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.db.models import AgentMessage, BumpaSyncRun, OtpSession
from app.db.session import SessionLocal
from app.main import app
from tests.conftest import auth_headers


def test_nonlocal_disabled_providers_fail_closed_without_side_effects(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    operator = auth_headers(client, "+2348099990001")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    with SessionLocal() as db:
        before = {
            "otp": db.scalar(select(func.count()).select_from(OtpSession)),
            "messages": db.scalar(select(func.count()).select_from(AgentMessage)),
            "syncs": db.scalar(select(func.count()).select_from(BumpaSyncRun)),
        }

    base = get_settings()
    disabled = Settings(
        app_env="staging",
        database_url=base.database_url,
        artifact_root=base.artifact_root,
        jwt_secret=base.jwt_secret,
        otp_secret=base.otp_secret,
        field_encryption_key=base.field_encryption_key,
        expose_local_otp=False,
        seed_demo_data=False,
        whatsapp_backend="disabled",
        bumpa_backend="disabled",
        agent_backend="disabled",
    )
    app.dependency_overrides[get_settings] = lambda: disabled
    try:
        otp = client.post("/v1/auth/request-otp", json={"phone_e164": "+2348555555555"})
        assert otp.status_code == 503
        assert "not configured" in otp.json()["detail"]

        webhook = client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "anything",
                "hub.challenge": "challenge",
            },
        )
        assert webhook.status_code == 503

        chat = client.post("/v1/chat/web", headers=owner, json={"message": "Show sales"})
        assert chat.status_code == 503
        assert "Hermes" in chat.json()["detail"]

        sync = client.post("/v1/bumpa/sync/latest", headers=owner)
        assert sync.status_code == 503
        assert "Bumpa" in sync.json()["detail"]

        profile = client.post(f"/v1/admin/tenants/{tenant_id}/hermes-profile", headers=operator)
        assert profile.status_code == 503

        local_connection = client.post(
            f"/v1/admin/tenants/{tenant_id}/bumpa",
            headers=operator,
            json={
                "api_key": "must-not-be-stored",
                "scope_type": "business_id",
                "scope_id": "disabled-business",
                "store_timezone": "Africa/Lagos",
                "store_currency": "NGN",
                "provider": "local",
            },
        )
        assert local_connection.status_code == 422

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["providers"] == {
            "whatsapp": "disabled",
            "bumpa": "disabled",
            "agent": "disabled",
        }
    finally:
        app.dependency_overrides.pop(get_settings, None)

    with SessionLocal() as db:
        after = {
            "otp": db.scalar(select(func.count()).select_from(OtpSession)),
            "messages": db.scalar(select(func.count()).select_from(AgentMessage)),
            "syncs": db.scalar(select(func.count()).select_from(BumpaSyncRun)),
        }
    assert after == before
