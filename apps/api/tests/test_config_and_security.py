from __future__ import annotations

import json
import logging
import subprocess
import sys
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
from app.db.models import AuthSession, OtpSession, PlatformRole, User
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
            jwt_secret="local-only-test-jwt-secret",  # noqa: S106 - weak fixture
            otp_secret="local-only-test-otp-secret",  # noqa: S106 - weak fixture
            field_encryption_key="local-only-test-field-key",  # noqa: S106 - weak fixture
            expose_local_otp=False,
            seed_demo_data=False,
        )

    with pytest.raises(ValidationError, match="host-only"):
        Settings(
            app_env="production",
            jwt_secret="j" * 40,
            otp_secret="o" * 40,
            field_encryption_key="f" * 40,
            research_pseudonym_key="p" * 40,
            onboarding_integrity_key="i" * 40,
            expose_local_otp=False,
            seed_demo_data=False,
            whatsapp_backend="disabled",
            bumpa_backend="disabled",
            agent_backend="disabled",
            session_cookie_domain=".bumpabestie.com",
        )

    production = Settings(
        app_env="production",
        jwt_secret="j" * 40,
        otp_secret="o" * 40,
        field_encryption_key="f" * 40,
        research_pseudonym_key="p" * 40,
        onboarding_integrity_key="i" * 40,
        expose_local_otp=False,
        seed_demo_data=False,
        whatsapp_backend="disabled",
        bumpa_backend="disabled",
        agent_backend="disabled",
        session_cookie_secure=False,
        meta_test_sender_waba_id="",
        meta_test_sender_phone_number_id="",
        meta_test_sender_display_phone_e164="",
        meta_webhook_verify_token_file="",
        meta_app_secret_file="",
        meta_system_user_access_token_file="",
        ops_alert_hmac_secret_file="",
        google_oauth_client_secret_file="",
        meta_ads_oauth_client_secret_file="",
    )
    assert production.cookie_secure is True
    assert production.meta_test_sender_waba_id is None
    assert production.meta_test_sender_phone_number_id is None
    assert production.meta_test_sender_display_phone_e164 is None
    assert production.meta_webhook_verify_token_file is None
    assert production.meta_app_secret_file is None
    assert production.meta_system_user_access_token_file is None
    assert production.ops_alert_hmac_secret_file is None
    assert production.google_oauth_client_secret_file is None
    assert production.meta_ads_oauth_client_secret_file is None

    with pytest.raises(ValidationError, match="Local OTP controls"):
        Settings(
            app_env="production",
            jwt_secret="j" * 40,
            otp_secret="o" * 40,
            field_encryption_key="f" * 40,
            research_pseudonym_key="p" * 40,
            onboarding_integrity_key="i" * 40,
            expose_local_otp=False,
            seed_demo_data=False,
            dev_fixed_otp="246810",
            whatsapp_backend="disabled",
            bumpa_backend="disabled",
            agent_backend="disabled",
        )

    with pytest.raises(ValidationError, match="Meta WhatsApp configuration is incomplete"):
        Settings(
            app_env="production",
            jwt_secret="j" * 40,
            otp_secret="o" * 40,
            field_encryption_key="f" * 40,
            research_pseudonym_key="p" * 40,
            onboarding_integrity_key="i" * 40,
            expose_local_otp=False,
            seed_demo_data=False,
            whatsapp_backend="meta",
            bumpa_backend="disabled",
            agent_backend="disabled",
        )
    configured_meta = Settings(
        app_env="production",
        jwt_secret="j" * 40,
        otp_secret="o" * 40,
        field_encryption_key="f" * 40,
        research_pseudonym_key="p" * 40,
        onboarding_integrity_key="i" * 40,
        expose_local_otp=False,
        seed_demo_data=False,
        whatsapp_backend="meta",
        bumpa_backend="disabled",
        agent_backend="disabled",
        meta_app_id="1234567890",
        meta_waba_id="2234567890",
        meta_webhook_verify_token="v" * 32,
        meta_webhook_verify_token_file="",
        meta_app_secret="s" * 32,
        meta_app_secret_file="",
        meta_phone_number_id="3234567890",
        meta_system_user_access_token="t" * 40,
        meta_system_user_access_token_file="",
    )
    assert configured_meta.whatsapp_backend == "meta"
    assert configured_meta.effective_meta_webhook_verify_token == "v" * 32
    assert configured_meta.effective_meta_app_secret == "s" * 32
    assert configured_meta.effective_meta_system_user_access_token == "t" * 40

    with pytest.raises(ValidationError, match="Production cannot use mock providers"):
        Settings(
            app_env="production",
            jwt_secret="j" * 40,
            otp_secret="o" * 40,
            field_encryption_key="f" * 40,
            research_pseudonym_key="p" * 40,
            onboarding_integrity_key="i" * 40,
            expose_local_otp=False,
            seed_demo_data=False,
            whatsapp_backend="mock",
            bumpa_backend="disabled",
            agent_backend="disabled",
        )


def test_meta_test_sender_is_typed_scoped_and_disabled_by_default() -> None:
    identifiers = {
        "meta_waba_id": "2234567890",
        "meta_phone_number_id": "3234567890",
        "meta_test_sender_waba_id": "423456789012345",
        "meta_test_sender_phone_number_id": "523456789012345",
        "meta_test_sender_display_phone_e164": "+15550102030",
    }
    disabled = Settings(app_env="test", whatsapp_backend="meta", **identifiers)
    assert disabled.meta_test_sender_verification_mode == "disabled"
    assert disabled.allowed_meta_inbound_reply_senders == frozenset({("2234567890", "3234567890")})

    enabled = Settings(
        app_env="test",
        whatsapp_backend="meta",
        meta_test_sender_verification_mode="inbound_replies_only",
        **identifiers,
    )
    assert enabled.allowed_meta_inbound_reply_senders == frozenset(
        {
            ("2234567890", "3234567890"),
            ("423456789012345", "523456789012345"),
        }
    )

    with pytest.raises(ValidationError, match="Meta test-sender verification is incomplete"):
        Settings(
            app_env="test",
            whatsapp_backend="meta",
            meta_waba_id="2234567890",
            meta_phone_number_id="3234567890",
            meta_test_sender_verification_mode="inbound_replies_only",
            meta_test_sender_waba_id=None,
            meta_test_sender_phone_number_id=None,
            meta_test_sender_display_phone_e164=None,
        )
    with pytest.raises(ValidationError, match="requires the Meta WhatsApp backend"):
        Settings(
            app_env="test",
            whatsapp_backend="mock",
            meta_test_sender_verification_mode="inbound_replies_only",
            **identifiers,
        )
    with pytest.raises(ValidationError, match="must differ"):
        Settings(
            app_env="test",
            whatsapp_backend="meta",
            meta_test_sender_verification_mode="inbound_replies_only",
            **{**identifiers, "meta_test_sender_phone_number_id": "3234567890"},
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            whatsapp_backend="meta",
            meta_test_sender_verification_mode="send_everything",
            **identifiers,
        )


def test_temporary_pin_can_coexist_only_with_the_reply_only_meta_test_lane() -> None:
    values = {
        "app_env": "test",
        "auth_login_mode": "temporary_static_pin",
        "temporary_web_pin_verifier": "a" * 64,
        "temporary_web_pin_expires_at": utcnow() + timedelta(hours=1),
        "whatsapp_backend": "meta",
        "meta_waba_id": "2234567890",
        "meta_phone_number_id": "3234567890",
        "meta_primary_sender_enabled": False,
        "meta_test_sender_verification_mode": "inbound_replies_only",
        "meta_test_sender_waba_id": "423456789012345",
        "meta_test_sender_phone_number_id": "523456789012345",
        "meta_test_sender_display_phone_e164": "+15550102030",
    }
    configured = Settings(**values)

    assert configured.allowed_meta_inbound_reply_senders == frozenset(
        {("423456789012345", "523456789012345")}
    )

    with pytest.raises(ValidationError, match="reply-only test sender"):
        Settings(
            **{
                **values,
                "meta_test_sender_verification_mode": "disabled",
            }
        )

    with pytest.raises(ValidationError, match="Meta primary sender"):
        Settings(
            **{
                **values,
                "proactive_insights_enabled": True,
                "daily_insights_enabled": True,
            }
        )


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

        logged_out = client.post(
            "/v1/auth/logout",
            headers={"Authorization": f"Bearer {verified.json()['access_token']}"},
        )
        deleted_cookie = logged_out.headers["set-cookie"]
        assert logged_out.status_code == 200
        assert "bb_session=" in deleted_cookie
        assert "Max-Age=0" in deleted_cookie
        assert "Secure" in deleted_cookie
        assert "Domain=.example.test" in deleted_cookie
        assert "HttpOnly" in deleted_cookie
        assert "SameSite=lax" in deleted_cookie
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
        db.flush()
        db.add(PlatformRole(user_id=user.id, role="operator"))
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
        FieldCipher("key").decrypt("v3.invalid")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    assert '"message": "hello"' in JsonFormatter().format(record)


def test_json_formatter_preserves_known_methods_and_rejects_caller_text() -> None:
    valid = logging.LogRecord("http", logging.INFO, __file__, 1, "request", (), None)
    valid.method = "POST"
    assert json.loads(JsonFormatter().format(valid))["method"] == "POST"

    canary = "OTP123456BEARERTOKEN"
    attacker_controlled = logging.LogRecord("http", logging.INFO, __file__, 1, "request", (), None)
    attacker_controlled.method = canary
    serialized = JsonFormatter().format(attacker_controlled)

    assert json.loads(serialized)["method"] == "OTHER"
    assert canary not in serialized


def test_uvicorn_configured_before_app_import_cannot_emit_exception_secrets() -> None:
    exception_canary = "OTP123456-BEARER-EXCEPTION-MUST-NOT-LOG"
    cause_canary = "RAW-CAUSE-AND-RESPONSE-MUST-NOT-LOG"
    query_canary = "QUERY-API-KEY-MUST-NOT-LOG"
    script = f"""
import logging
import uvicorn

uvicorn.Config("app.main:app", access_log=True).configure_logging()
import app.main  # noqa: F401,E402

try:
    try:
        raise ValueError({cause_canary!r})
    except ValueError as cause:
        raise RuntimeError({exception_canary!r}) from cause
except RuntimeError:
    logging.getLogger("uvicorn.error").exception("uvicorn_unhandled_exception")

logging.getLogger("uvicorn.access").info("GET /?token={query_canary} HTTP/1.1")
"""
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and in-test script.
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    lines = completed.stderr.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["logger"] == "uvicorn.error"
    assert payload["message"] == "uvicorn_unhandled_exception"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["exception_frames"]
    for canary in (exception_canary, cause_canary, query_canary):
        assert canary not in completed.stderr


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
