from __future__ import annotations

from fastapi.testclient import TestClient

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

    assert client.get("/v1/settings/profile", headers=owner).status_code == 200
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
    removed = client.delete(f"/v1/settings/team/{member.json()['membership_id']}", headers=owner)
    assert removed.status_code == 204

    owner_membership = next(row for row in team.json() if row["role"] == "owner")
    cannot_remove_owner = client.delete(
        f"/v1/settings/team/{owner_membership['membership_id']}", headers=owner
    )
    assert cannot_remove_owner.status_code == 409
    assert client.delete("/v1/settings/team/not-found", headers=owner).status_code == 404

    bumpa = client.get("/v1/settings/bumpa", headers=owner)
    assert bumpa.status_code == 200 and bumpa.json()["provider"] == "local"
    assert client.get("/v1/hermes/profile", headers=owner).json()["provider"] == "local"
    assert len(client.get("/v1/mcp/registry", headers=owner).json()) == 5
    connection = client.post(
        "/v1/settings/mcp-connections",
        headers=owner,
        json={"provider": "google_sheets", "scopes": ["spreadsheets.readonly"]},
    )
    assert connection.status_code == 201
    assert any(
        row["provider"] == "google_sheets"
        for row in client.get("/v1/settings/mcp-connections", headers=owner).json()
    )
    assert current.json()["id"] == tenant_id


def test_member_cannot_mutate_owner_settings(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    created = client.post(
        "/v1/settings/team",
        headers=owner,
        json={"name": "Restricted Member", "phone_e164": "+2348555555555", "role": "member"},
    )
    assert created.status_code == 201
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
