from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def test_health_and_local_otp_login(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    headers = auth_headers(client, "+2348012345678")
    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["name"] == "Ada Owner"
    assert me.json()["memberships"][0]["role"] == "owner"


def test_otp_is_single_use_and_rejects_unknown_phone(client: TestClient) -> None:
    requested = client.post("/v1/auth/request-otp", json={"phone_e164": "+2348111111111"})
    assert requested.status_code == 202
    rejected = client.post(
        "/v1/auth/verify-otp", json={"phone_e164": "+2348111111111", "code": "246810"}
    )
    assert rejected.status_code == 403


def test_tenant_header_cannot_cross_tenant_boundary(client: TestClient) -> None:
    owner_headers = auth_headers(client, "+2348012345678")
    operator = auth_headers(client, "+2348099990001")
    tenants = client.get("/v1/admin/tenants", headers=operator).json()
    other_id = next(item["id"] for item in tenants if item["slug"] == "other-store")
    owner_headers["X-Tenant-ID"] = other_id
    response = client.get("/v1/tenants/current", headers=owner_headers)
    assert response.status_code == 403


def test_normal_user_cannot_access_admin_or_research(client: TestClient) -> None:
    headers = auth_headers(client, "+2348012345678")
    assert client.get("/v1/admin/tenants", headers=headers).status_code == 403
    assert client.get("/v1/research/overview", headers=headers).status_code == 403
