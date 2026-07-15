from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    BumpaConnection,
    BumpaSyncRun,
    PhoneIdentity,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal, set_security_context
from tests.conftest import auth_headers


def test_tenant_profile_consent_and_settings_lifecycle(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    current = client.get("/v1/tenants/current", headers=owner)
    assert current.status_code == 200
    tenant_id = current.json()["id"]
    updated = client.patch(
        "/v1/tenants/current",
        headers=owner,
        json={"city": "Ikeja", "timezone": "Africa/Lagos", "status": "suspended"},
    )
    assert updated.status_code == 200
    assert updated.json()["city"] == "Ikeja"
    assert updated.json()["status"] == "active"  # SME route cannot suspend itself.

    original_profile_response = client.get("/v1/settings/profile", headers=owner)
    assert original_profile_response.status_code == 200
    original_profile = original_profile_response.json()
    profile = client.patch(
        "/v1/settings/profile",
        headers=owner,
        json={"name": "Ada Demo Owner", "email": "ada.updated@example.com"},
    )
    assert profile.status_code == 200
    assert profile.json()["name"] == "Ada Demo Owner"

    withdrawal = client.post(
        "/v1/tenants/current/research-consent",
        headers=owner,
        json={"status": "withdrawn", "policy_version": "v2"},
    )
    assert withdrawal.json()["status"] == "withdrawn"
    grant = client.post(
        "/v1/tenants/current/research-consent",
        headers=owner,
        json={"status": "granted", "policy_version": "v2"},
    )
    assert grant.json()["status"] == "granted"

    team = client.get("/v1/settings/team", headers=owner)
    assert team.status_code == 200 and team.json()
    owner_membership = next(row for row in team.json() if row["role"] == "owner")
    member = client.post(
        "/v1/settings/team",
        headers=owner,
        json={
            "name": "Team Member",
            "phone_e164": "+2348333333333",
            "email": "member@example.com",
            "role": "member",
        },
    )
    assert member.status_code == 201
    duplicate = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Team Member", "phone_e164": "+2348333333333", "role": "member"},
    )
    assert duplicate.status_code == 409
    phone = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=owner,
        json={
            "user_id": member.json()["user_id"],
            "phone_e164": "+2348333333333",
            "label": "Sales",
        },
    )
    assert phone.status_code == 201
    numbers = client.get("/v1/settings/whatsapp-numbers", headers=owner)
    assert any(row["label"] == "Sales" for row in numbers.json())
    owner_identity = next(
        row for row in numbers.json() if row["user_id"] == owner_membership["user_id"]
    )
    assert (
        client.delete(
            f"/v1/settings/whatsapp-numbers/{owner_identity['id']}", headers=owner
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/v1/settings/whatsapp-numbers/{phone.json()['id']}", headers=owner
        ).status_code
        == 204
    )
    removed = client.delete(f"/v1/settings/team/{member.json()['membership_id']}", headers=owner)
    assert removed.status_code == 204

    cannot_remove_owner = client.delete(
        f"/v1/settings/team/{owner_membership['membership_id']}", headers=owner
    )
    assert cannot_remove_owner.status_code == 409
    assert client.delete("/v1/settings/team/not-found", headers=owner).status_code == 404

    bumpa = client.get("/v1/settings/bumpa", headers=owner)
    assert bumpa.status_code == 200 and bumpa.json()["provider"] == "local"
    assert bumpa.json()["store_timezone"] == "Africa/Lagos"
    assert bumpa.json()["store_currency"] == "NGN"
    assert client.get("/v1/hermes/profile", headers=owner).json()["provider"] == "local"
    assert len(client.get("/v1/mcp/registry", headers=owner).json()) == 5
    connection = client.post(
        "/v1/settings/mcp-connections",
        headers=owner,
        json={"provider": "google_sheets"},
    )
    assert connection.status_code == 201
    sheet_connection = next(
        row
        for row in client.get("/v1/settings/mcp-connections", headers=owner).json()
        if row["provider"] == "google_sheets"
    )
    assert sheet_connection["scopes"] == ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    assert current.json()["id"] == tenant_id
    restored_profile = client.patch(
        "/v1/settings/profile",
        headers=owner,
        json={"name": original_profile["name"]},
    )
    assert restored_profile.status_code == 200
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        restored_user = db.get(User, original_profile["id"])
        assert restored_user is not None
        restored_user.email = original_profile["email"]
        db.commit()


def test_profile_email_can_be_cleared_and_other_sessions_revoked(client: TestClient) -> None:
    current = auth_headers(client, "+2348012345678")
    other = auth_headers(client, "+2348012345678")
    original_email = client.get("/v1/settings/profile", headers=current).json()["email"]
    try:
        cleared = client.patch(
            "/v1/settings/profile",
            headers=current,
            json={"email": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["email"] is None
        revoked = client.post("/v1/auth/logout-others", headers=current)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked_sessions"] >= 1
        assert client.get("/v1/auth/me", headers=current).status_code == 200
        assert client.get("/v1/auth/me", headers=other).status_code == 401
    finally:
        client.patch(
            "/v1/settings/profile",
            headers=current,
            json={"email": original_email},
        )


def test_bumpa_settings_freshness_is_latest_tenant_scoped_typed_usable_run(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    other_owner = auth_headers(client, "+2348012345679")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    other_tenant_id = client.get("/v1/tenants/current", headers=other_owner).json()["id"]
    run_ids = {
        "settings-typed",
        "settings-accepted-partial",
        "settings-legacy",
        "settings-old-boundary",
        "settings-other",
    }

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        connection = db.scalar(
            select(BumpaConnection).where(BumpaConnection.tenant_id == tenant_id)
        )
        other_connection = db.scalar(
            select(BumpaConnection).where(BumpaConnection.tenant_id == other_tenant_id)
        )
        assert connection is not None and other_connection is not None
        original_connection_freshness = connection.last_successful_sync_at
        original_boundary_revision = connection.boundary_revision
        connection.boundary_revision += 1
        current_boundary_revision = connection.boundary_revision
        connection.last_successful_sync_at = datetime(2029, 1, 1, tzinfo=UTC)
        db.add_all(
            [
                BumpaSyncRun(
                    id="settings-typed",
                    tenant_id=tenant_id,
                    bumpa_connection_id=connection.id,
                    boundary_revision=current_boundary_revision,
                    status="success",
                    completion_quality="complete",
                    requested_from=date(2028, 1, 1),
                    requested_to=date(2028, 1, 1),
                    started_at=datetime(2028, 1, 1, tzinfo=UTC),
                    finished_at=datetime(2028, 1, 1, 0, 0, 5, tzinfo=UTC),
                    orders_availability="available",
                    orders_count=1,
                ),
                BumpaSyncRun(
                    id="settings-accepted-partial",
                    tenant_id=tenant_id,
                    bumpa_connection_id=connection.id,
                    boundary_revision=current_boundary_revision,
                    status="partial",
                    completion_quality="accepted_partial",
                    partial_reason="profit_not_calculable",
                    requested_from=date(2028, 6, 1),
                    requested_to=date(2028, 6, 1),
                    started_at=datetime(2028, 6, 1, tzinfo=UTC),
                    finished_at=datetime(2028, 6, 1, 0, 0, 5, tzinfo=UTC),
                    orders_availability="available",
                    orders_count=1,
                ),
                BumpaSyncRun(
                    id="settings-legacy",
                    tenant_id=tenant_id,
                    bumpa_connection_id=connection.id,
                    boundary_revision=current_boundary_revision,
                    status="success",
                    completion_quality="legacy",
                    requested_from=date(2029, 1, 1),
                    requested_to=date(2029, 1, 1),
                    started_at=datetime(2029, 1, 1, tzinfo=UTC),
                    finished_at=datetime(2029, 1, 1, 0, 0, 5, tzinfo=UTC),
                ),
                BumpaSyncRun(
                    id="settings-old-boundary",
                    tenant_id=tenant_id,
                    bumpa_connection_id=connection.id,
                    boundary_revision=original_boundary_revision,
                    status="success",
                    completion_quality="complete",
                    requested_from=date(2031, 1, 1),
                    requested_to=date(2031, 1, 1),
                    started_at=datetime(2031, 1, 1, tzinfo=UTC),
                    finished_at=datetime(2031, 1, 1, 0, 0, 5, tzinfo=UTC),
                    orders_availability="available",
                    orders_count=1,
                ),
                BumpaSyncRun(
                    id="settings-other",
                    tenant_id=other_tenant_id,
                    bumpa_connection_id=other_connection.id,
                    status="success",
                    completion_quality="complete",
                    requested_from=date(2030, 1, 1),
                    requested_to=date(2030, 1, 1),
                    started_at=datetime(2030, 1, 1, tzinfo=UTC),
                    finished_at=datetime(2030, 1, 1, 0, 0, 5, tzinfo=UTC),
                    orders_availability="available",
                    orders_count=1,
                ),
            ]
        )
        db.commit()

    try:
        response = client.get("/v1/settings/bumpa", headers=owner)
        assert response.status_code == 200
        freshness = response.json()["last_successful_sync_at"]
        assert freshness.startswith("2028-06-01T00:00:05")

        other_response = client.get("/v1/settings/bumpa", headers=other_owner)
        assert other_response.status_code == 200
        other_freshness = other_response.json()["last_successful_sync_at"]
        assert other_freshness.startswith("2030-01-01T00:00:05")
    finally:
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            db.query(BumpaSyncRun).filter(BumpaSyncRun.id.in_(run_ids)).delete(
                synchronize_session=False
            )
            connection = db.scalar(
                select(BumpaConnection).where(BumpaConnection.tenant_id == tenant_id)
            )
            assert connection is not None
            connection.last_successful_sync_at = original_connection_freshness
            connection.boundary_revision = original_boundary_revision
            db.commit()


def test_member_cannot_mutate_owner_settings(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    created = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Restricted Member", "phone_e164": "+2348555555555", "role": "member"},
    )
    assert created.status_code == 201
    approved = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=owner,
        json={
            "user_id": created.json()["user_id"],
            "phone_e164": "+2348555555555",
            "label": "Restricted member",
        },
    )
    assert approved.status_code == 201
    member = auth_headers(client, "+2348555555555")
    assert (
        client.patch("/v1/tenants/current", headers=member, json={"city": "Abuja"}).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/settings/mcp-connections", headers=member, json={"provider": "gmail"}
        ).status_code
        == 403
    )


def test_concurrent_team_membership_creation_returns_stable_conflict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = auth_headers(client, "+2348012345678")
    original_flush = Session.flush
    conflict_raised = False

    def conflict_on_membership(self: Session, *args, **kwargs) -> None:
        nonlocal conflict_raised
        if not conflict_raised and any(isinstance(row, TenantMembership) for row in self.new):
            conflict_raised = True
            raise IntegrityError("INSERT tenant_memberships", {}, RuntimeError("unique race"))
        original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", conflict_on_membership)
    response = client.post(
        "/v1/settings/team",
        headers=owner,
        json={
            "name": "Concurrent Member",
            "phone_e164": "+2348666100005",
            "role": "member",
        },
    )

    assert conflict_raised
    assert response.status_code == 409
    assert response.json() == {"detail": "Team member conflict"}


def test_inactive_user_cannot_start_or_reach_whatsapp_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    owner = auth_headers(client, "+2348012345678")
    phone = "+2348666100006"
    created = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Disabled Member", "phone_e164": phone, "role": "member"},
    )
    assert created.status_code == 201
    approved = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=owner,
        json={"user_id": created.json()["user_id"], "phone_e164": phone},
    )
    assert approved.status_code == 201

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        user = db.get(User, created.json()["user_id"])
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        assert user is not None and identity is not None
        user.status = "inactive"
        identity.opt_out = True
        db.commit()

    monkeypatch.setattr(
        whatsapp,
        "handle_chat",
        lambda *_args, **_kwargs: pytest.fail("inactive user reached WhatsApp chat"),
    )
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.inactive-user-start",
                                        "from": phone.lstrip("+"),
                                        "type": "text",
                                        "text": {"body": "START"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"local-meta-app-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "rejected_unknown_sender"}
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        assert identity is not None and identity.opt_out is True


def test_team_reactivation_phone_approval_and_revocation_are_tenant_safe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    owner = auth_headers(client, "+2348012345678")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]

    # A tenant administrator can never create another non-revocable owner.
    crafted_owner = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Crafted Owner", "phone_e164": "+2348666100001", "role": "owner"},
    )
    assert crafted_owner.status_code == 422
    owner_rewrite = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Ada Owner", "phone_e164": "+2348012345678", "role": "admin"},
    )
    assert owner_rewrite.status_code == 409

    member_phone = "+2348666100002"
    created = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Revocable Member", "phone_e164": member_phone, "role": "member"},
    )
    assert created.status_code == 201
    membership_id = created.json()["membership_id"]
    user_id = created.json()["user_id"]
    approved = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=owner,
        json={"user_id": user_id, "phone_e164": member_phone, "label": "Operations"},
    )
    assert approved.status_code == 201

    member_headers = auth_headers(client, member_phone)
    assert client.get("/v1/settings/profile", headers=member_headers).status_code == 200
    assert client.delete(f"/v1/settings/team/{membership_id}", headers=owner).status_code == 204

    # Login eligibility is resolved on every authenticated request, so a token
    # issued before membership revocation becomes unauthorized immediately.
    assert client.get("/v1/settings/profile", headers=member_headers).status_code == 401

    # An approved phone is likewise insufficient after its tenant membership is
    # revoked. The message takes the non-authorized path and never reaches chat.
    monkeypatch.setattr(
        whatsapp,
        "handle_chat",
        lambda *_args, **_kwargs: pytest.fail("revoked member reached WhatsApp chat"),
    )
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.revoked-membership-routing",
                                        "from": member_phone.lstrip("+"),
                                        "type": "text",
                                        "text": {"body": "Show me sales"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"local-meta-app-secret", body, hashlib.sha256).hexdigest()
    rejected = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": signature},
    )
    assert rejected.status_code == 200
    assert rejected.json() == {"status": "rejected_unknown_sender"}

    inactive_phone = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=owner,
        json={"user_id": user_id, "phone_e164": "+2348666100003"},
    )
    assert inactive_phone.status_code == 422

    # Re-adding a revoked membership reuses the stable membership row and can
    # intentionally change it to either tenant-managed role.
    reactivated = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Revocable Member", "phone_e164": member_phone, "role": "admin"},
    )
    assert reactivated.status_code == 201
    assert reactivated.json() == {
        "membership_id": membership_id,
        "user_id": user_id,
        "role": "admin",
    }
    assert client.get("/v1/settings/team", headers=member_headers).status_code == 200

    # An existing number can neither be duplicated in this tenant nor silently
    # reassigned to a user in another tenant.
    same_tenant_duplicate = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=owner,
        json={"user_id": user_id, "phone_e164": member_phone},
    )
    assert same_tenant_duplicate.status_code == 409

    other_owner = auth_headers(client, "+2348012345679")
    other_member = client.post(
        "/v1/settings/team",
        headers=other_owner,
        json={
            "name": "Other Tenant Member",
            "phone_e164": "+2348666100004",
            "role": "member",
        },
    )
    assert other_member.status_code == 201
    cross_tenant_duplicate = client.post(
        "/v1/settings/whatsapp-numbers",
        headers=other_owner,
        json={"user_id": other_member.json()["user_id"], "phone_e164": member_phone},
    )
    assert cross_tenant_duplicate.status_code == 409

    with SessionLocal() as db:
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == member_phone))
        assert identity is not None
        assert identity.tenant_id == tenant_id
        assert identity.user_id == user_id

    assert client.delete(f"/v1/settings/team/{membership_id}", headers=owner).status_code == 204
    reactivated_member = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Revocable Member", "phone_e164": member_phone, "role": "member"},
    )
    assert reactivated_member.status_code == 201
    assert reactivated_member.json()["membership_id"] == membership_id
    assert reactivated_member.json()["role"] == "member"
    assert client.get("/v1/settings/profile", headers=member_headers).status_code == 200
