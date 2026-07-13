from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.logging import JsonFormatter
from app.db.models import PlatformRole, User, WebhookEvent, WhatsappMessage
from app.db.session import SessionLocal
from app.jobs.runtime import PermanentJobError
from app.main import app
from app.providers.meta import MetaProviderError, MetaWhatsAppClient


def _ensure_active_user(phone_e164: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.primary_phone_e164 == phone_e164))
        if user is None:
            user = User(name="Meta OTP test user", primary_phone_e164=phone_e164)
            db.add(user)
            db.flush()
        if (
            db.scalar(
                select(PlatformRole.id).where(
                    PlatformRole.user_id == user.id,
                    PlatformRole.role == "operator",
                )
            )
            is None
        ):
            db.add(PlatformRole(user_id=user.id, role="operator"))
        db.commit()


def _success_response(request: httpx.Request) -> httpx.Response:
    assert request.url == "https://graph.facebook.com/v23.0/3234567890/messages"
    assert request.headers["authorization"] == "Bearer " + "t" * 40
    return httpx.Response(
        200,
        headers={"x-fb-request-id": "request-1"},
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"wa_id": "2348000000000"}],
            "messages": [{"id": "wamid.test-message-1"}],
        },
    )


def _client(
    handler: Any = _success_response, *, max_response_bytes: int = 262_144
) -> MetaWhatsAppClient:
    return MetaWhatsAppClient(
        graph_version="v23.0",
        phone_number_id="3234567890",
        access_token="t" * 40,
        timeout_seconds=5,
        max_response_bytes=max_response_bytes,
        transport=httpx.MockTransport(handler),
    )


def test_meta_text_and_otp_template_requests_are_bounded_and_typed() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _success_response(request)

    provider = _client(handler)
    assert provider.send_text("+2348000000000", "Hello") == "wamid.test-message-1"
    assert provider.send_otp("+2348000000000", "123456") == "wamid.test-message-1"
    assert requests[0] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "2348000000000",
        "type": "text",
        "text": {"preview_url": False, "body": "Hello"},
    }
    assert requests[1]["type"] == "template"
    assert requests[1]["template"]["name"] == "bb_otp_login"
    assert requests[1]["template"]["components"][0]["parameters"][0]["text"] == "123456"

    with pytest.raises(ValueError, match="E.164"):
        provider.send_text("08000000000", "Hello")
    with pytest.raises(ValueError, match="exceeds"):
        provider.send_text("+2348000000000", "x" * 4001)
    with pytest.raises(ValueError, match="OTP"):
        provider.send_otp("+2348000000000", "not-code")


def test_meta_errors_are_sanitized_and_classified() -> None:
    secret_marker = "customer-phone-and-provider-secret"

    def rate_limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "12", "x-fb-request-id": "request-rate"},
            json={"error": {"message": secret_marker, "code": 4}},
        )

    with pytest.raises(MetaProviderError) as raised:
        _client(rate_limited).send_text("+2348000000000", "Hello")
    error = raised.value
    assert error.category == "rate_limited"
    assert error.retryable is True
    assert error.http_status == 429
    assert error.provider_code == "4"
    assert error.request_id_hash == hashlib.sha256(b"request-rate").hexdigest()
    assert error.retry_after_seconds == 12
    assert secret_marker not in str(error)
    assert "t" * 40 not in str(error)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(secret_marker, request=request)

    with pytest.raises(MetaProviderError) as timed_out:
        _client(timeout).send_text("+2348000000000", "Hello")
    assert timed_out.value.category == "timeout"
    assert secret_marker not in str(timed_out.value)


def test_meta_rejects_oversized_and_malformed_success_responses() -> None:
    def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4097)

    with pytest.raises(MetaProviderError, match="invalid_response"):
        _client(oversized, max_response_bytes=4096).send_text("+2348000000000", "Hello")

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": []})

    with pytest.raises(MetaProviderError, match="invalid_response"):
        _client(malformed).send_text("+2348000000000", "Hello")


def test_production_meta_secrets_can_be_loaded_from_absolute_files(tmp_path: Path) -> None:
    app_secret = tmp_path / "app-secret"
    access_token = tmp_path / "access-token"
    verify_token = tmp_path / "verify-token"
    app_secret.write_text("s" * 32 + "\n")
    access_token.write_text("t" * 40 + "\n")
    verify_token.write_text("v" * 32 + "\n")
    for path in (app_secret, access_token, verify_token):
        path.chmod(0o600)

    configured = Settings(
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
        meta_phone_number_id="3234567890",
        meta_app_secret_file=app_secret,
        meta_system_user_access_token_file=access_token,
        meta_webhook_verify_token_file=verify_token,
    )
    assert configured.effective_meta_app_secret == "s" * 32
    assert configured.effective_meta_system_user_access_token == "t" * 40
    assert configured.effective_meta_webhook_verify_token == "v" * 32

    with pytest.raises(ValueError, match="either META_APP_SECRET"):
        configured._provider_secret("META_APP_SECRET", "inline", app_secret)


class RecordingMessagingProvider:
    def __init__(self, error: MetaProviderError | None = None) -> None:
        self.error = error
        self.text_attempts: list[tuple[str, str]] = []
        self.texts: list[tuple[str, str]] = []
        self.otps: list[tuple[str, str]] = []

    def send_text(self, phone_e164: str, body: str) -> str:
        self.text_attempts.append((phone_e164, body))
        if self.error:
            raise self.error
        self.texts.append((phone_e164, body))
        return f"wamid.recorded-{phone_e164[-4:]}-{len(self.texts)}"

    def send_otp(self, phone_e164: str, code: str) -> str:
        if self.error:
            raise self.error
        self.otps.append((phone_e164, code))
        return f"wamid.otp-{len(self.otps)}"


class PartialRetryMessagingProvider:
    def __init__(self) -> None:
        self.fail_second_attempt = True
        self.attempts: list[tuple[str, str]] = []

    def send_text(self, phone_e164: str, body: str) -> str:
        self.attempts.append((phone_e164, body))
        if self.fail_second_attempt and len(self.attempts) == 2:
            raise MetaProviderError("rate_limited", retryable=True, http_status=429)
        return f"wamid.chunk-{len(self.attempts)}"

    def send_otp(self, phone_e164: str, code: str) -> str:
        return self.send_text(phone_e164, code)


class InvalidTextMessagingProvider:
    def __init__(self) -> None:
        self.attempts = 0

    def send_text(self, _phone_e164: str, _body: str) -> str:
        self.attempts += 1
        raise ValueError("deterministic invalid text")

    def send_otp(self, phone_e164: str, code: str) -> str:
        return self.send_text(phone_e164, code)


def _meta_test_settings() -> Settings:
    base = get_settings()
    return Settings(
        app_env="test",
        database_url=base.database_url,
        artifact_root=base.artifact_root,
        whatsapp_backend="meta",
        meta_app_secret="s" * 32,
        meta_waba_id="2234567890",
        meta_phone_number_id="3234567890",
        meta_system_user_access_token="t" * 40,
        expose_local_otp=True,
        seed_demo_data=True,
    )


def _verification_sender_settings() -> Settings:
    base = _meta_test_settings()
    return Settings(
        app_env="test",
        database_url=base.database_url,
        artifact_root=base.artifact_root,
        whatsapp_backend="meta",
        meta_app_secret="s" * 32,
        meta_waba_id="2234567890",
        meta_phone_number_id="3234567890",
        meta_test_sender_verification_mode="inbound_replies_only",
        meta_test_sender_waba_id="423456789012345",
        meta_test_sender_phone_number_id="523456789012345",
        meta_test_sender_display_phone_e164="+15550102030",
        meta_system_user_access_token="t" * 40,
        expose_local_otp=True,
        seed_demo_data=True,
    )


def test_meta_verification_sender_is_reply_only_and_primary_remains_default() -> None:
    settings = _verification_sender_settings()

    primary = MetaWhatsAppClient.from_settings(settings)
    assert primary.phone_number_id == "3234567890"
    assert primary.supports_otp is True
    primary_reply = MetaWhatsAppClient.for_inbound_reply(
        settings,
        waba_id="2234567890",
        phone_number_id="3234567890",
    )
    assert primary_reply.phone_number_id == primary.phone_number_id
    assert primary_reply.supports_otp is True

    verification = MetaWhatsAppClient.for_inbound_reply(
        settings,
        waba_id="423456789012345",
        phone_number_id="523456789012345",
    )
    assert verification.phone_number_id == "523456789012345"
    assert verification.supports_otp is False
    with pytest.raises(ValueError, match="approved AUTHENTICATION template"):
        verification.send_otp("+15550123456", "123456")

    disabled = _meta_test_settings()
    with pytest.raises(ValueError, match="not configured"):
        MetaWhatsAppClient.for_inbound_reply(
            disabled,
            waba_id="423456789012345",
            phone_number_id="523456789012345",
        )


def test_long_whatsapp_reply_is_bounded_lossless_and_idempotent_across_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    provider = PartialRetryMessagingProvider()
    monkeypatch.setattr(whatsapp.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    body = "A" * 3_990 + " split-one " + "B" * 3_990 + " split-two " + "C" * 180
    with SessionLocal() as db:
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="chunk-retry-event",
            signature_valid=True,
            payload={},
        )
        db.add(event)
        db.commit()

        with pytest.raises(MetaProviderError, match="rate_limited"):
            whatsapp._deliver_text_chunks(
                db,
                event=event,
                purpose="agent-reply",
                phone="+2348000000000",
                body=body,
                settings=_meta_test_settings(),
            )

        provider.fail_second_attempt = False
        message_ids = whatsapp._deliver_text_chunks(
            db,
            event=event,
            purpose="agent-reply",
            phone="+2348000000000",
            body=body,
            settings=_meta_test_settings(),
        )
        rows = list(
            db.scalars(
                select(WhatsappMessage).where(WhatsappMessage.idempotency_key.like("whatsapp:%"))
            ).all()
        )
        rows = sorted(
            (row for row in rows if row.payload.get("purpose", "").startswith("agent-reply:chunk")),
            key=lambda row: str(row.payload["purpose"]),
        )

    assert len(message_ids) == 3
    assert len(rows) == 3
    assert all(row.status == "sent" for row in rows)
    assert all(row.text_body is not None and len(row.text_body) <= 4_000 for row in rows)
    assert "".join(row.text_body or "" for row in rows) == body
    # First part was already sent, so the retry resumes at part two instead of duplicating it.
    assert [attempt[1] for attempt in provider.attempts].count(rows[0].text_body or "") == 1


def test_deterministic_whatsapp_value_error_becomes_terminal_without_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    provider = InvalidTextMessagingProvider()
    monkeypatch.setattr(whatsapp.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    with SessionLocal() as db:
        event = WebhookEvent(
            provider="whatsapp",
            external_event_id="invalid-text-event",
            signature_valid=True,
            payload={},
        )
        db.add(event)
        db.commit()
        for _ in range(2):
            with pytest.raises(PermanentJobError):
                whatsapp._deliver_text_chunks(
                    db,
                    event=event,
                    purpose="agent-reply-invalid",
                    phone="+2348000000000",
                    body="invalid",
                    settings=_meta_test_settings(),
                )
        row = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.payload["purpose"].as_string() == "agent-reply-invalid"
            )
        )

    assert provider.attempts == 1
    assert row is not None and row.status == "rejected"


def test_meta_otp_route_uses_live_adapter_without_local_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routes import auth

    provider = RecordingMessagingProvider()
    selected_phone_number_ids: list[str | None] = []

    def provider_from_settings(
        _settings: Settings,
        *,
        phone_number_id: str | None = None,
    ) -> RecordingMessagingProvider:
        selected_phone_number_ids.append(phone_number_id)
        return provider

    phone = "+2348666666666"
    _ensure_active_user(phone)
    settings = _verification_sender_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(auth.MetaWhatsAppClient, "from_settings", provider_from_settings)
    try:
        response = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 202
    assert selected_phone_number_ids == [None]
    assert provider.otps == [(phone, response.json()["dev_code"])]


def test_meta_otp_route_returns_only_sanitized_provider_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routes import auth

    provider = RecordingMessagingProvider(
        MetaProviderError("provider", retryable=False, http_status=400, provider_code="100")
    )
    phone = "+2348777777777"
    _ensure_active_user(phone)
    settings = _meta_test_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(auth.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    try:
        response = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 502
    assert response.json() == {"detail": "WhatsApp rejected OTP delivery"}


def test_meta_otp_failure_log_is_typed_and_contains_no_request_secrets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routes import auth

    phone = "+2348787878787"
    _ensure_active_user(phone)
    access_token = "meta-access-token-must-never-be-logged"
    raw_body_marker = "raw-meta-response-body-must-never-be-logged"
    caller_correlation = "123456-caller-token-must-never-be-logged"
    raw_request_id = "meta-request-A1B2C3D4"
    provider_error = MetaProviderError(
        "rate_limited",
        retryable=True,
        http_status=429,
        provider_code=131047,
        request_id=raw_request_id,
        retry_after_seconds=12,
    )
    provider_error.__cause__ = RuntimeError(raw_body_marker)

    class FailingOtpProvider:
        def __init__(self) -> None:
            self.attempt: tuple[str, str] | None = None

        def send_otp(self, phone_e164: str, code: str) -> str:
            self.attempt = (phone_e164, code)
            raise provider_error

    class JsonCapture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.lines.append(JsonFormatter().format(record))

    provider = FailingOtpProvider()
    base = _meta_test_settings()
    settings = Settings(
        app_env="test",
        database_url=base.database_url,
        artifact_root=base.artifact_root,
        whatsapp_backend="meta",
        meta_app_secret="s" * 32,
        meta_phone_number_id="3234567890",
        meta_system_user_access_token=access_token,
        expose_local_otp=True,
        seed_demo_data=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(auth.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    capture = JsonCapture()
    provider_logger = logging.getLogger("bumpabestie.providers")
    previous_disabled = provider_logger.disabled
    previous_level = provider_logger.level
    provider_logger.disabled = False
    provider_logger.setLevel(logging.WARNING)
    provider_logger.addHandler(capture)
    try:
        response = client.post(
            "/v1/auth/request-otp",
            json={"phone_e164": phone},
            headers={"X-Correlation-ID": caller_correlation},
        )
    finally:
        provider_logger.removeHandler(capture)
        provider_logger.setLevel(previous_level)
        provider_logger.disabled = previous_disabled
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    assert provider.attempt is not None
    attempted_phone, attempted_otp = provider.attempt
    assert attempted_phone == phone
    assert len(capture.lines) == 1
    payload = json.loads(capture.lines[0])
    assert payload == {
        "level": "WARNING",
        "logger": "bumpabestie.providers",
        "message": "meta_otp_delivery_failed",
        "correlation_id": payload["correlation_id"],
        "provider": "meta",
        "provider_operation": "otp_delivery",
        "provider_category": "rate_limited",
        "provider_retryable": True,
        "provider_http_status": 429,
        "provider_code": "131047",
        "provider_request_id_hash": hashlib.sha256(raw_request_id.encode()).hexdigest(),
        "retry_after_seconds": 12,
    }
    serialized = capture.lines[0]
    for secret in (
        phone,
        attempted_otp,
        access_token,
        raw_body_marker,
        raw_request_id,
        caller_correlation,
    ):
        assert secret not in serialized
    assert "exception" not in payload


def test_meta_diagnostics_drop_arbitrary_codes_and_oversized_request_ids() -> None:
    error = MetaProviderError(
        "provider",
        retryable=False,
        provider_code="100-private-response-detail",
        request_id="x" * 513,
    )

    assert error.provider_code is None
    assert error.request_id_hash is None


@pytest.mark.parametrize(
    "request_id",
    [
        "123456",
        "2348787878787",
        "+2348787878787",
        "request-2348787878787",
        "short",
        "request-id-with-a-newline\nprivate",
    ],
)
def test_meta_diagnostics_drop_low_entropy_or_phone_shaped_request_ids(
    request_id: str,
) -> None:
    error = MetaProviderError("provider", retryable=False, request_id=request_id)

    assert error.request_id_hash is None


def test_meta_webhook_routes_unknown_sender_through_live_adapter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    provider = RecordingMessagingProvider()
    settings = _meta_test_settings()
    body = json.dumps(
        {
            "entry": [
                {
                    "id": "2234567890",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "3234567890"},
                                "messages": [
                                    {
                                        "id": "wamid.meta-route-unknown",
                                        "from": "2348888888888",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(whatsapp.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    try:
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 200
    assert response.json() == {"status": "rejected_unknown_sender"}
    assert provider.texts == [
        (
            "+2348888888888",
            "This number is not approved for Bumpa Bestie. Ask your store owner to add it.",
        )
    ]


def test_meta_verification_webhook_replies_from_actual_allowed_test_sender(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    provider = RecordingMessagingProvider()
    selected_senders: list[tuple[str, str]] = []

    def provider_for_inbound_reply(
        _settings: Settings,
        *,
        waba_id: str,
        phone_number_id: str,
    ) -> RecordingMessagingProvider:
        selected_senders.append((waba_id, phone_number_id))
        return provider

    body = json.dumps(
        {
            "entry": [
                {
                    "id": "423456789012345",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "523456789012345"},
                                "messages": [
                                    {
                                        "id": "wamid.meta-verification-sender",
                                        "from": "15550123456",
                                        "type": "text",
                                        "text": {"body": "Verification hello"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    app.dependency_overrides[get_settings] = _verification_sender_settings
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient,
        "for_inbound_reply",
        provider_for_inbound_reply,
    )
    try:
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert response.json() == {"status": "rejected_unknown_sender"}
    assert selected_senders == [("423456789012345", "523456789012345")]
    assert provider.texts == [
        (
            "+15550123456",
            "This number is not approved for Bumpa Bestie. Ask your store owner to add it.",
        )
    ]
    with SessionLocal() as db:
        outbound = db.scalar(
            select(WhatsappMessage).where(
                WhatsappMessage.meta_message_id == "wamid.recorded-3456-1"
            )
        )
        assert outbound is not None
        assert outbound.payload["sender_waba_id"] == "423456789012345"
        assert outbound.payload["sender_phone_number_id"] == "523456789012345"


@pytest.mark.parametrize(
    ("mode", "waba_id", "phone_number_id", "message_id"),
    [
        ("disabled", "423456789012345", "523456789012345", "disabled"),
        (
            "inbound_replies_only",
            "000002355278341",
            "523456789012345",
            "wrong-waba",
        ),
        ("inbound_replies_only", "423456789012345", None, "missing-phone"),
    ],
)
def test_meta_webhook_rejects_disabled_mismatched_or_missing_reply_sender(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    waba_id: str,
    phone_number_id: str | None,
    message_id: str,
) -> None:
    from app.services import whatsapp

    settings = _meta_test_settings() if mode == "disabled" else _verification_sender_settings()
    value: dict[str, Any] = {
        "messages": [
            {
                "id": f"wamid.meta-rejected-{message_id}",
                "from": "15550999999",
                "type": "text",
                "text": {"body": "Do not route"},
            }
        ]
    }
    if phone_number_id is not None:
        value["metadata"] = {"phone_number_id": phone_number_id}
    body = json.dumps({"entry": [{"id": waba_id, "changes": [{"value": value}]}]}).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()

    def unexpected_provider(*_args: object, **_kwargs: object) -> RecordingMessagingProvider:
        raise AssertionError("unconfigured sender must not construct a provider")

    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(whatsapp.MetaWhatsAppClient, "from_settings", unexpected_provider)
    try:
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert response.json() == {"status": "rejected_unconfigured_sender"}


def test_meta_webhook_failure_is_sanitized_and_durable_event_can_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    provider = RecordingMessagingProvider(
        MetaProviderError("rate_limited", retryable=True, request_id="private-request-id")
    )
    settings = _meta_test_settings()
    body = json.dumps(
        {
            "entry": [
                {
                    "id": "2234567890",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "3234567890"},
                                "messages": [
                                    {
                                        "id": "wamid.meta-route-retry",
                                        "from": "2348999999999",
                                        "type": "text",
                                        "text": {"body": "Retry me"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(whatsapp.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    try:
        failed = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
        provider.error = None
        retried = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert failed.status_code == 503
    assert failed.json() == {"detail": "Webhook processing failed; retry is safe"}
    assert "private-request-id" not in failed.text
    assert retried.status_code == 200
    assert retried.json() == {"status": "rejected_unknown_sender"}
    assert len(provider.text_attempts) == 2


def test_meta_webhook_does_not_blindly_retry_an_ambiguous_delivery(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import whatsapp

    provider = RecordingMessagingProvider(
        MetaProviderError("timeout", retryable=True, request_id="private-request-id")
    )
    settings = _meta_test_settings()
    body = json.dumps(
        {
            "entry": [
                {
                    "id": "2234567890",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "3234567890"},
                                "messages": [
                                    {
                                        "id": "wamid.meta-route-ambiguous",
                                        "from": "2348555555555",
                                        "type": "text",
                                        "text": {"body": "Do not duplicate"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(whatsapp.MetaWhatsAppClient, "from_settings", lambda _settings: provider)
    try:
        first = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
        provider.error = None
        second = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert first.status_code == 503
    assert second.status_code == 503
    assert len(provider.text_attempts) == 1
