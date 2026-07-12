from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.ids import normalize_id
from app.core.logging import JsonFormatter
from app.core.security import (
    create_access_token,
    decode_access_token,
    issue_otp,
    normalize_phone,
    revoke_token,
    verify_otp,
)
from app.core.time import utcnow
from app.db.models import AuthSession, OtpSession, User
from app.db.session import SessionLocal
from app.main import app


def test_root_environment_aliases_and_production_guards() -> None:
    local = Settings(
        app_env="test",
        cors_allowed_origins="https://one.test, https://two.test",
        cors_origins=["https://ignored.test"],
        dev_fixed_otp="135790",
        session_cookie_domain=".example.test",
        session_cookie_secure=True,
    )
    assert local.effective_cors_origins == ["https://one.test", "https://two.test"]
    assert local.effective_local_otp_code == "135790"
    assert local.cookie_secure is True
    assert local.cookie_domain == ".example.test"
    assert len(local.jwt_secret) >= 32

    with pytest.raises(ValidationError, match="Production secrets"):
        Settings(
            app_env="production",
            jwt_secret="local-only-test-jwt-secret",
            otp_secret="local-only-test-otp-secret",
            field_encryption_key="local-only-test-field-key",
            expose_local_otp=False,
            seed_demo_data=False,
        )

    production = Settings(
        app_env="production",
        jwt_secret="j" * 40,
        otp_secret="o" * 40,
        field_encryption_key="f" * 40,
        expose_local_otp=False,
        seed_demo_data=False,
        session_cookie_secure=False,
    )
    assert production.cookie_secure is True

    with pytest.raises(ValidationError, match="Meta WhatsApp configuration is incomplete"):
        Settings(
            app_env="production",
            jwt_secret="j" * 40,
            otp_secret="o" * 40,
            field_encryption_key="f" * 40,
            expose_local_otp=False,
            seed_demo_data=False,
            whatsapp_backend="meta",
        )
    configured_meta = Settings(
        app_env="production",
        jwt_secret="j" * 40,
        otp_secret="o" * 40,
        field_encryption_key="f" * 40,
        expose_local_otp=False,
        seed_demo_data=False,
        whatsapp_backend="meta",
        meta_app_secret="realistic-app-secret",  # noqa: S106 - non-secret fixture
        meta_phone_number_id="phone-number-id",
        meta_system_user_access_token="system-user-access-token",  # noqa: S106 - fixture
    )
    assert configured_meta.whatsapp_backend == "meta"


def test_configurable_secure_cookie_is_emitted(client: TestClient) -> None:
    configured = Settings(
        app_env="test",
        database_url=get_settings().database_url,
        session_cookie_secure=True,
        session_cookie_domain=".example.test",
        dev_fixed_otp="246810",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    try:
        requested = client.post("/v1/auth/request-otp", json={"phone_e164": "+2348099990000"})
        verified = client.post(
            "/v1/auth/verify-otp",
            json={"phone_e164": "+2348099990000", "code": requested.json()["dev_code"]},
        )
        cookie = verified.headers["set-cookie"]
        assert "Secure" in cookie
        assert "Domain=.example.test" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_security_invalid_inputs_lockout_token_revocation_and_helpers() -> None:
    settings = get_settings()
    with pytest.raises(HTTPException) as invalid_phone:
        normalize_phone("080 123")
    assert invalid_phone.value.status_code == 422

    with SessionLocal() as db:
        phone = "+2348222222222"
        user = User(name="Security Test", primary_phone_e164=phone)
        db.add(user)
        db.commit()
        otp, code = issue_otp(db, phone, settings)
        with pytest.raises(HTTPException) as cooldown:
            issue_otp(db, phone, settings)
        assert cooldown.value.status_code == 429
        for attempt in range(settings.otp_max_attempts):
            with pytest.raises(HTTPException) as wrong:
                verify_otp(db, phone, "000000", settings)
            assert wrong.value.status_code == (
                423 if attempt == settings.otp_max_attempts - 1 else 401
            )
        with pytest.raises(HTTPException) as locked:
            verify_otp(db, phone, code, settings)
        assert locked.value.status_code == 423

        fresh = OtpSession(
            phone_e164=phone,
            code_hash=otp.code_hash,
            purpose="login",
            expires_at=utcnow() + timedelta(minutes=5),
        )
        db.add(fresh)
        db.commit()
        verified_user = verify_otp(db, phone, code, settings)
        token, session = create_access_token(db, verified_user, settings)
        assert decode_access_token(db, token, settings).id == user.id
        revoke_token(db, token, settings)
        assert (
            db.scalar(select(AuthSession).where(AuthSession.id == session.id)).revoked_at
            is not None
        )
        with pytest.raises(HTTPException):
            decode_access_token(db, token, settings)
        revoke_token(db, "not-a-token", settings)

    assert (
        normalize_id("12345678-1234-5678-1234-567812345678")
        == "12345678-1234-5678-1234-567812345678"
    )
    with pytest.raises(ValueError, match="Unsupported ciphertext version"):
        FieldCipher("key").decrypt("v2.invalid")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    assert '"message": "hello"' in JsonFormatter().format(record)


def test_initial_migration_is_explicit_and_enables_postgres_rls() -> None:
    migrations = list(
        (Path(__file__).parents[1] / "alembic" / "versions").glob("*_explicit_schema.py")
    )
    assert len(migrations) == 1
    source = migrations[0].read_text()
    assert "op.create_table" in source
    assert "op.drop_table" in source
    assert "create_all" not in source
    assert "drop_all" not in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('app.current_tenant_id', true)" in source
