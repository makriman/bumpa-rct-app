import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import OtpSession, PhoneIdentity, Tenant, User
from app.db.session import SessionLocal
from tests.conftest import auth_headers


def test_health_and_local_otp_login(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    headers = auth_headers(client, "+2348012345678")
    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["name"] == "Ada Owner"
    assert me.json()["memberships"][0]["role"] == "owner"


@pytest.mark.parametrize(
    ("phone", "user_status"),
    [
        ("+2348111111111", None),
        ("+2348111111112", "inactive"),
        ("+2348111111113", "active"),
    ],
)
def test_otp_request_is_generic_and_side_effect_free_for_unapproved_phones(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    phone: str,
    user_status: str | None,
) -> None:
    if user_status is not None:
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
            if user is None:
                user = User(
                    name="Unapproved login",
                    primary_phone_e164=phone,
                    status=user_status,
                )
                db.add(user)
            else:
                user.status = user_status
            db.commit()

    sends: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.routes.auth.LocalMessagingProvider.send_otp",
        lambda _self, destination, code: sends.append((destination, code)),
    )

    requested = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    assert requested.status_code == 202
    assert requested.json() == {
        "status": "sent",
        "expires_in_seconds": 600,
        "dev_code": None,
    }
    assert sends == []
    with SessionLocal() as db:
        assert db.scalar(select(OtpSession.id).where(OtpSession.phone_e164 == phone)) is None

    rejected = client.post(
        "/v1/auth/verify-otp",
        json={"phone_e164": phone, "code": "246810"},
    )
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid or expired code"}


def test_otp_request_respects_whatsapp_opt_out(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phone = "+2348012345678"
    sends: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.routes.auth.LocalMessagingProvider.send_otp",
        lambda _self, destination, code: sends.append((destination, code)),
    )
    with SessionLocal() as db:
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        assert identity is not None
        identity.opt_out = True
        before = db.scalar(
            select(func.count()).select_from(OtpSession).where(OtpSession.phone_e164 == phone)
        )
        db.commit()
    try:
        requested = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
        assert requested.status_code == 202
        assert requested.json()["dev_code"] is None
        assert sends == []
        with SessionLocal() as db:
            after = db.scalar(
                select(func.count()).select_from(OtpSession).where(OtpSession.phone_e164 == phone)
            )
        assert after == before
    finally:
        with SessionLocal() as db:
            identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
            assert identity is not None
            identity.opt_out = False
            db.commit()


def test_otp_request_rejects_identity_whose_only_tenant_is_suspended(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phone = "+2348012345678"
    sends: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.routes.auth.LocalMessagingProvider.send_otp",
        lambda _self, destination, code: sends.append((destination, code)),
    )
    with SessionLocal() as db:
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        assert identity is not None
        tenant = db.get(Tenant, identity.tenant_id)
        assert tenant is not None
        tenant.status = "suspended"
        before = db.scalar(
            select(func.count()).select_from(OtpSession).where(OtpSession.phone_e164 == phone)
        )
        db.commit()
    try:
        requested = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
        assert requested.status_code == 202
        assert requested.json()["dev_code"] is None
        assert sends == []
        with SessionLocal() as db:
            after = db.scalar(
                select(func.count()).select_from(OtpSession).where(OtpSession.phone_e164 == phone)
            )
        assert after == before
    finally:
        with SessionLocal() as db:
            identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
            assert identity is not None
            tenant = db.get(Tenant, identity.tenant_id)
            assert tenant is not None
            tenant.status = "active"
            db.commit()


def test_revoked_identity_invalidates_pending_otp_and_existing_session(
    client: TestClient,
) -> None:
    phone = "+2348012345678"
    existing_session = auth_headers(client, phone)
    requested = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    assert requested.status_code == 202
    code = requested.json()["dev_code"]
    assert code == "246810"

    with SessionLocal() as db:
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        assert identity is not None
        replacement = {
            "tenant_id": identity.tenant_id,
            "user_id": identity.user_id,
            "phone_e164": identity.phone_e164,
            "whatsapp_wa_id": identity.whatsapp_wa_id,
            "label": identity.label,
            "status": identity.status,
            "opt_out": identity.opt_out,
        }
        db.delete(identity)
        db.commit()
    try:
        rejected = client.post(
            "/v1/auth/verify-otp",
            json={"phone_e164": phone, "code": code},
        )
        assert rejected.status_code == 403
        assert rejected.json() == {"detail": "This phone number is not approved"}
        assert client.get("/v1/tenants/current", headers=existing_session).status_code == 401
    finally:
        with SessionLocal() as db:
            db.add(PhoneIdentity(**replacement))
            db.commit()


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
