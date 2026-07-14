from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.core.config import Settings, get_settings
from app.core.crypto import secret_hash
from app.core.time import utcnow
from app.db.models import (
    AuditLog,
    AuthSession,
    OtpSession,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal
from app.main import app

PIN = "123456"


def production_settings_values() -> dict[str, object]:
    return {
        "app_env": "production",
        "jwt_secret": "j" * 40,
        "otp_secret": "o" * 40,
        "field_encryption_key": "f" * 40,
        "research_pseudonym_key": "p" * 40,
        "onboarding_integrity_key": "i" * 40,
        "expose_local_otp": False,
        "seed_demo_data": False,
        "auth_login_mode": "temporary_static_pin",
        "temporary_web_pin_expires_at": utcnow() + timedelta(days=7),
        "whatsapp_backend": "disabled",
        "bumpa_backend": "disabled",
        "agent_backend": "disabled",
    }


def temporary_pin_settings(*, expires_in: timedelta = timedelta(hours=1)) -> Settings:
    base = get_settings()
    return Settings(
        app_env="test",
        database_url=base.database_url,
        jwt_secret=base.jwt_secret,
        otp_secret=base.otp_secret,
        auth_login_mode="temporary_static_pin",
        temporary_web_pin_verifier=secret_hash(
            f"web-login-pin:{PIN}",
            base.otp_secret,
        ),
        temporary_web_pin_expires_at=utcnow() + expires_in,
        whatsapp_backend="disabled",
        auth_rate_limit_enabled=False,
        expose_local_otp=False,
    )


@contextmanager
def configured(settings: Settings) -> Iterator[None]:
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


def clear_challenges(phone: str) -> None:
    with SessionLocal() as db:
        db.execute(
            delete(OtpSession).where(
                OtpSession.phone_e164 == phone,
                OtpSession.purpose == "temporary_web_pin",
            )
        )
        db.commit()


def test_production_web_pin_configuration_requires_scoped_verifier_and_expiry(
    tmp_path: Path,
) -> None:
    verifier = secret_hash(f"web-login-pin:{PIN}", "o" * 40)
    verifier_file = tmp_path / "temporary-web-pin-verifier"
    verifier_file.write_text(verifier + "\n", encoding="utf-8")
    values = production_settings_values()

    configured = Settings(
        **values,
        temporary_web_pin_verifier_file=verifier_file,
    )
    assert configured.effective_temporary_web_pin_verifier == verifier
    assert configured.temporary_web_pin_expires_at is not None

    with pytest.raises(ValidationError, match="must use a secret file"):
        Settings(**values, temporary_web_pin_verifier=verifier)
    with pytest.raises(ValidationError, match="VERIFIER_FILE is required"):
        Settings(**values)

    verifier_file.write_text("123456\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="lowercase SHA-256 HMAC"):
        Settings(**values, temporary_web_pin_verifier_file=verifier_file)

    verifier_file.write_text(verifier + "\n", encoding="utf-8")
    expired_values = {
        **values,
        "temporary_web_pin_expires_at": utcnow() - timedelta(seconds=1),
    }
    expired = Settings(**expired_values, temporary_web_pin_verifier_file=verifier_file)
    assert expired.temporary_web_pin_expires_at is not None
    assert expired.temporary_web_pin_expires_at < utcnow()

    naive_values = {
        **values,
        "temporary_web_pin_expires_at": (utcnow() + timedelta(days=7)).replace(tzinfo=None),
    }
    with pytest.raises(ValidationError, match="must include a timezone"):
        Settings(**naive_values, temporary_web_pin_verifier_file=verifier_file)

    meta_values = {**values, "whatsapp_backend": "meta"}
    with pytest.raises(ValidationError, match="requires WHATSAPP_BACKEND=disabled"):
        Settings(**meta_values, temporary_web_pin_verifier_file=verifier_file)


def test_web_pin_request_is_generic_provider_free_and_typed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapped_phone = "+2348012345678"
    unknown_phone = "+447700900987"
    clear_challenges(mapped_phone)

    def unexpected_send(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("temporary web PIN mode must not contact a messaging provider")

    monkeypatch.setattr("app.routes.auth.LocalMessagingProvider.send_otp", unexpected_send)
    monkeypatch.setattr("app.routes.auth.MetaWhatsAppClient.send_otp", unexpected_send)
    try:
        with configured(temporary_pin_settings()):
            mapped = client.post("/v1/auth/request-otp", json={"phone_e164": mapped_phone})
            unknown = client.post("/v1/auth/request-otp", json={"phone_e164": unknown_phone})

        expected = {
            "status": "accepted",
            "expires_in_seconds": 600,
            "delivery": "web_pin",
            "dev_code": None,
        }
        assert mapped.status_code == unknown.status_code == 202
        assert mapped.json() == unknown.json() == expected
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(OtpSession)
                    .where(
                        OtpSession.phone_e164 == mapped_phone,
                        OtpSession.purpose == "temporary_web_pin",
                    )
                )
                == 1
            )
            assert (
                db.scalar(
                    select(OtpSession.id).where(
                        OtpSession.phone_e164 == unknown_phone,
                        OtpSession.purpose == "temporary_web_pin",
                    )
                )
                is None
            )
    finally:
        clear_challenges(mapped_phone)


def test_web_pin_authenticates_only_mapped_identity_and_audits_without_pii(
    client: TestClient,
) -> None:
    phone = "+2348012345678"
    clear_challenges(phone)
    try:
        settings = temporary_pin_settings()
        with configured(settings):
            assert (
                client.post("/v1/auth/request-otp", json={"phone_e164": phone}).status_code == 202
            )
            verified = client.post(
                "/v1/auth/verify-otp",
                json={"phone_e164": phone, "code": PIN},
            )
            assert verified.status_code == 200
            me = client.get(
                "/v1/auth/me",
                headers={"Authorization": f"Bearer {verified.json()['access_token']}"},
            )
            assert me.status_code == 200
            assert me.json()["memberships"][0]["role"] == "owner"

        reference = secret_hash(f"auth-audit:phone:{phone}", settings.otp_secret)
        with SessionLocal() as db:
            record = db.scalar(
                select(AuditLog)
                .where(
                    AuditLog.action == "auth.temporary_static_pin.verified",
                    AuditLog.resource_id == reference,
                )
                .order_by(AuditLog.created_at.desc())
            )
            assert record is not None
            serialized = json.dumps(
                {"resource_id": record.resource_id, "before": record.before, "after": record.after}
            )
            assert record.actor_user_id is not None
            assert record.after == {"outcome": "success"}
            assert phone not in serialized
            assert PIN not in serialized
    finally:
        clear_challenges(phone)


def test_web_pin_denial_is_generic_for_wrong_pin_and_unmapped_platform_role(
    client: TestClient,
) -> None:
    mapped_phone = "+2348012345678"
    unmapped_role_phone = "+2348099990002"
    clear_challenges(mapped_phone)
    try:
        settings = temporary_pin_settings()
        with configured(settings):
            assert (
                client.post("/v1/auth/request-otp", json={"phone_e164": mapped_phone}).status_code
                == 202
            )
            wrong = client.post(
                "/v1/auth/verify-otp",
                json={"phone_e164": mapped_phone, "code": "654321"},
            )
            assert (
                client.post(
                    "/v1/auth/request-otp", json={"phone_e164": unmapped_role_phone}
                ).status_code
                == 202
            )
            unmapped = client.post(
                "/v1/auth/verify-otp",
                json={"phone_e164": unmapped_role_phone, "code": PIN},
            )

        assert wrong.status_code == unmapped.status_code == 401
        assert wrong.json() == unmapped.json() == {"detail": "Invalid or expired code"}
        with SessionLocal() as db:
            role = db.scalar(
                select(PlatformRole)
                .join(User, User.id == PlatformRole.user_id)
                .where(User.primary_phone_e164 == unmapped_role_phone)
            )
            assert role is not None
            denial = db.scalar(
                select(AuditLog)
                .where(AuditLog.action == "auth.temporary_static_pin.denied")
                .order_by(AuditLog.created_at.desc())
            )
            assert denial is not None
            assert denial.actor_user_id is None
            assert denial.after == {"outcome": "invalid_credentials"}
    finally:
        clear_challenges(mapped_phone)


def test_web_pin_session_is_revoked_when_platform_role_mapping_opts_out(
    client: TestClient,
) -> None:
    phone = "+2348099990001"
    clear_challenges(phone)
    created_membership = False
    created_identity = False
    original_membership_status: str | None = None
    original_identity_status: str | None = None
    original_identity_opt_out: bool | None = None
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "demo-store"))
        assert user is not None and tenant is not None
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        if identity is None:
            membership_tenant_id = tenant.id
        else:
            assert identity.user_id == user.id
            membership_tenant_id = identity.tenant_id
            original_identity_status = identity.status
            original_identity_opt_out = identity.opt_out

        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == membership_tenant_id,
                TenantMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = TenantMembership(
                tenant_id=membership_tenant_id,
                user_id=user.id,
                role="admin",
                status="active",
            )
            db.add(membership)
            created_membership = True
        else:
            original_membership_status = membership.status
            membership.status = "active"

        if identity is None:
            identity = PhoneIdentity(
                tenant_id=membership_tenant_id,
                user_id=user.id,
                phone_e164=phone,
                status="approved",
                opt_out=False,
            )
            db.add(identity)
            created_identity = True
        else:
            identity.status = "approved"
            identity.opt_out = False

        db.commit()
        membership_id = membership.id
        identity_id = identity.id
        user_id = user.id
        existing_auth_session_ids = set(
            db.scalars(select(AuthSession.id).where(AuthSession.user_id == user_id)).all()
        )

    try:
        with configured(temporary_pin_settings()):
            assert (
                client.post("/v1/auth/request-otp", json={"phone_e164": phone}).status_code == 202
            )
            verified = client.post(
                "/v1/auth/verify-otp",
                json={"phone_e164": phone, "code": PIN},
            )
            assert verified.status_code == 200
            headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
            assert client.get("/v1/auth/me", headers=headers).status_code == 200

            with SessionLocal() as db:
                mapped = db.get(PhoneIdentity, identity_id)
                assert mapped is not None
                mapped.opt_out = True
                db.commit()

            assert client.get("/v1/auth/me", headers=headers).status_code == 401
            with SessionLocal() as db:
                assert (
                    db.scalar(
                        select(PlatformRole).where(
                            PlatformRole.user_id == user_id,
                            PlatformRole.role == "operator",
                        )
                    )
                    is not None
                )
    finally:
        with SessionLocal() as db:
            new_session_filter = AuthSession.user_id == user_id
            if existing_auth_session_ids:
                new_session_filter &= AuthSession.id.not_in(existing_auth_session_ids)
            db.execute(delete(AuthSession).where(new_session_filter))
            if created_identity:
                db.execute(delete(PhoneIdentity).where(PhoneIdentity.id == identity_id))
            else:
                identity = db.get(PhoneIdentity, identity_id)
                assert identity is not None
                assert original_identity_status is not None
                assert original_identity_opt_out is not None
                identity.status = original_identity_status
                identity.opt_out = original_identity_opt_out
            if created_membership:
                db.execute(delete(TenantMembership).where(TenantMembership.id == membership_id))
            else:
                membership = db.get(TenantMembership, membership_id)
                assert membership is not None
                assert original_membership_status is not None
                membership.status = original_membership_status
            db.commit()
        clear_challenges(phone)


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef"])
def test_web_pin_code_schema_requires_exactly_six_digits(
    client: TestClient,
    code: str,
) -> None:
    with configured(temporary_pin_settings()):
        response = client.post(
            "/v1/auth/verify-otp",
            json={"phone_e164": "+2348012345678", "code": code},
        )
    assert response.status_code == 422


def test_web_pin_is_rate_limited_and_expires_fail_closed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def rejected_rate_limit(
        _request: object,
        *,
        phone_e164: str,
        operation: str,
        settings: Settings,
    ) -> None:
        del phone_e164, settings
        calls.append(operation)
        raise HTTPException(status_code=429, detail="rate limited")

    monkeypatch.setattr("app.routes.auth.enforce_auth_rate_limit", rejected_rate_limit)
    with configured(temporary_pin_settings()):
        limited = client.post(
            "/v1/auth/verify-otp",
            json={"phone_e164": "+2348012345678", "code": PIN},
        )
    assert limited.status_code == 429
    assert calls == ["verify"]

    monkeypatch.undo()
    with configured(temporary_pin_settings(expires_in=timedelta(seconds=-1))):
        request = client.post(
            "/v1/auth/request-otp",
            json={"phone_e164": "+2348012345678"},
        )
        verify = client.post(
            "/v1/auth/verify-otp",
            json={"phone_e164": "+2348012345678", "code": PIN},
        )
    assert request.status_code == verify.status_code == 503
    assert (
        request.json() == verify.json() == {"detail": "Temporary web authentication is unavailable"}
    )


def test_auth_mode_defaults_to_disabled_and_fails_closed(client: TestClient) -> None:
    settings = Settings(
        app_env="test",
        database_url=get_settings().database_url,
        auth_login_mode="disabled",
    )
    with configured(settings):
        request = client.post(
            "/v1/auth/request-otp",
            json={"phone_e164": "+2348012345678"},
        )
        verify = client.post(
            "/v1/auth/verify-otp",
            json={"phone_e164": "+2348012345678", "code": PIN},
        )
    assert request.status_code == verify.status_code == 503
