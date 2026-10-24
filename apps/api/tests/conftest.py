from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_ROOT = Path(__file__).parent / ".runtime"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
DB_PATH = TEST_ROOT / "test.db"
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{DB_PATH}",
        "ARTIFACT_ROOT": str(TEST_ROOT / "exports"),
        "SEED_DEMO_DATA": "true",
        "EXPOSE_LOCAL_OTP": "true",
        "AUTH_LOGIN_MODE": "whatsapp_otp",
        "LOCAL_OTP_CODE": "246810",
    }
)

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


def auth_headers(client: TestClient, phone: str, tenant_id: str | None = None) -> dict[str, str]:
    requested = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    assert requested.status_code == 202, requested.text
    verified = client.post(
        "/v1/auth/verify-otp", json={"phone_e164": phone, "code": requested.json()["dev_code"]}
    )
    assert verified.status_code == 200, verified.text
    headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    return headers


@contextmanager
def approved_login_mapping(phone: str) -> Generator[None, None, None]:
    """Give a synthetic privileged test user the existing production pilot mapping."""
    from sqlalchemy import select

    from app.db.models import PhoneIdentity, Tenant, TenantMembership, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
        tenant = db.scalar(select(Tenant).where(Tenant.status == "active").limit(1))
        assert user is not None and tenant is not None
        membership = TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role="member", status="active"
        )
        identity = PhoneIdentity(
            tenant_id=tenant.id, user_id=user.id, phone_e164=phone, status="approved"
        )
        db.add_all([membership, identity])
        db.commit()
        membership_id, identity_id = membership.id, identity.id
    try:
        yield
    finally:
        with SessionLocal() as db:
            identity = db.get(PhoneIdentity, identity_id)
            membership = db.get(TenantMembership, membership_id)
            assert identity is not None and membership is not None
            db.delete(identity)
            db.delete(membership)
            db.commit()
