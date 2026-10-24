from collections.abc import Generator
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import secret_hash
from app.core.security import issue_otp, verify_otp
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import OtpSession, PhoneIdentity, PlatformRole, Tenant, TenantMembership, User
from app.db.session import SessionLocal
from app.main import app


@pytest.fixture()
def otp_db() -> Generator[tuple[Session, str], None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        phone = "+2348000000711"
        user = User(name="Synthetic OTP operator", primary_phone_e164=phone)
        db.add(user)
        db.flush()
        db.add(PlatformRole(user_id=user.id, role="operator"))
        db.commit()
        yield db, phone
    engine.dispose()


def test_resend_replaces_old_code_and_consumed_latest_never_falls_back(
    otp_db: tuple[Session, str],
) -> None:
    db, phone = otp_db
    first = get_settings().model_copy(
        update={"local_otp_code": "135790", "otp_request_cooldown_seconds": 0}
    )
    old, old_code = issue_otp(db, phone, first)
    second = first.model_copy(update={"local_otp_code": "246802"})
    latest, latest_code = issue_otp(db, phone, second)
    assert old.consumed_at is not None
    assert latest.consumed_at is None
    with pytest.raises(HTTPException) as denied:
        verify_otp(db, phone, old_code, second)
    assert denied.value.status_code == 401
    verify_otp(db, phone, latest_code, second)
    # Simulate an older active row retained from the prior implementation.
    old.consumed_at = None
    db.commit()
    for code in (old_code, latest_code):
        with pytest.raises(HTTPException) as replay:
            verify_otp(db, phone, code, second)
        assert replay.value.status_code == 401


def test_otp_verification_and_cooldown_ignore_other_challenge_purposes(
    otp_db: tuple[Session, str],
) -> None:
    db, phone = otp_db
    settings = get_settings()
    now = utcnow()
    db.add(
        OtpSession(
            phone_e164=phone,
            purpose="invite",
            code_hash=secret_hash(f"{phone}:111111", settings.otp_secret),
            expires_at=now + timedelta(minutes=5),
        )
    )
    db.commit()
    login, code = issue_otp(db, phone, settings)
    db.add(
        OtpSession(
            phone_e164=phone,
            purpose="phone_verify",
            code_hash=secret_hash(f"{phone}:222222", settings.otp_secret),
            expires_at=now + timedelta(minutes=5),
            created_at=now + timedelta(seconds=1),
        )
    )
    db.commit()
    assert verify_otp(db, phone, code, settings).primary_phone_e164 == phone
    assert login.consumed_at is not None
    with pytest.raises(HTTPException):
        verify_otp(db, phone, "222222", settings)


def test_production_otp_does_not_expand_pin_pilot_to_unmapped_platform_users(
    otp_db: tuple[Session, str],
) -> None:
    db, phone = otp_db
    settings = get_settings().model_copy(update={"app_env": "production"})
    challenge, code = issue_otp(db, phone, settings)
    with pytest.raises(HTTPException) as unmapped:
        verify_otp(db, phone, code, settings)
    assert unmapped.value.status_code == 403
    assert challenge.consumed_at is not None


def test_otp_expiry_is_exclusive_and_wrong_attempt_limit_remains_bounded(
    otp_db: tuple[Session, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, phone = otp_db
    settings = get_settings()
    challenge, code = issue_otp(db, phone, settings)
    instant = utcnow()
    challenge.expires_at = instant
    db.commit()
    monkeypatch.setattr("app.core.security.utcnow", lambda: instant)
    with pytest.raises(HTTPException) as expired:
        verify_otp(db, phone, code, settings)
    assert expired.value.status_code == 401


def test_duplicate_otp_request_has_same_public_shape_as_unknown_without_another_send(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends: list[str] = []
    monkeypatch.setattr(
        "app.routes.auth.LocalMessagingProvider.send_otp",
        lambda _self, phone, _code: sends.append(phone),
    )
    phone = "+2348000000712"
    with SessionLocal() as db:
        user = User(name="Synthetic resend operator", primary_phone_e164=phone)
        db.add(user)
        db.flush()
        db.add(PlatformRole(user_id=user.id, role="operator"))
        db.commit()
    first = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    second = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    unknown = client.post("/v1/auth/request-otp", json={"phone_e164": "+2348000000990"})
    assert first.status_code == second.status_code == unknown.status_code == 202
    assert second.json() == unknown.json()
    assert sends == [phone]
    assert second.json()["resend_after_seconds"] == get_settings().otp_request_cooldown_seconds


def test_production_otp_keeps_mapped_identity_scope_and_secure_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapped, unmapped = "+2348000000713", "+2348000000714"
    with SessionLocal() as db:
        tenant = Tenant(slug="synthetic-otp-mapped", name="Synthetic OTP business")
        owner = User(name="Synthetic OTP owner", primary_phone_e164=mapped)
        operator = User(name="Synthetic unmapped operator", primary_phone_e164=unmapped)
        db.add_all([tenant, owner, operator])
        db.flush()
        db.add_all(
            [
                TenantMembership(
                    tenant_id=tenant.id, user_id=owner.id, role="owner", status="active"
                ),
                PhoneIdentity(
                    tenant_id=tenant.id, user_id=owner.id, phone_e164=mapped, status="approved"
                ),
                PlatformRole(user_id=operator.id, role="operator"),
            ]
        )
        db.commit()
    sends: list[tuple[str, str]] = []

    class SyntheticMeta:
        def send_otp(self, phone: str, code: str) -> None:
            sends.append((phone, code))

    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "whatsapp_backend": "meta",
            "auth_rate_limit_enabled": False,
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        "app.routes.auth.MetaWhatsAppClient.from_settings", lambda _settings: SyntheticMeta()
    )
    monkeypatch.setattr("app.core.security.secrets.randbelow", lambda limit: 12)
    try:
        unknown = client.post("/v1/auth/request-otp", json={"phone_e164": unmapped})
        assert unknown.status_code == 202 and unknown.json()["dev_code"] is None
        assert sends == []
        requested = client.post("/v1/auth/request-otp", json={"phone_e164": mapped})
        assert requested.status_code == 202 and requested.json()["dev_code"] is None
        assert sends == [(mapped, "000012")]
        verified = client.post(
            "/v1/auth/verify-otp", json={"phone_e164": mapped, "code": sends[0][1]}
        )
        assert verified.status_code == 200
        cookie = verified.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "Domain=" not in cookie
        replay = client.post(
            "/v1/auth/verify-otp", json={"phone_e164": mapped, "code": sends[0][1]}
        )
        assert replay.status_code == 401
    finally:
        app.dependency_overrides.pop(get_settings, None)
