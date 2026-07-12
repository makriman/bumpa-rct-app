from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import HermesProfile, Tenant, UsageEvent
from app.db.session import SessionLocal
from app.main import app
from app.providers.hermes import (
    HermesAuthenticationError,
    HermesCircuitBreaker,
    HermesCircuitOpen,
    HermesClient,
    HermesEndpoint,
    HermesInvalidResponse,
    HermesProfileError,
    HermesRateLimited,
    HermesReadiness,
    HermesResult,
    HermesUnavailable,
    provision_profile,
)
from tests.conftest import auth_headers


def _settings(tmp_path: Path | None = None, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "database_url": get_settings().database_url,
        "artifact_root": get_settings().artifact_root,
        "field_encryption_key": "hermes-test-field-key",
        "agent_backend": "hermes",
        "hermes_profile_root": tmp_path or Path(".data/hermes-test"),
        "hermes_profile_port_start": 8700,
        "hermes_profile_port_end": 8705,
    }
    values.update(overrides)
    return Settings(**values)


def _completion(content: str, *, input_tokens: int = 11, output_tokens: int = 7) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def test_authenticated_bounded_client_and_cross_profile_isolation(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        token = request.headers["authorization"]
        payload = json.loads(request.content)
        serialized = json.dumps(payload)
        if token == "Bearer tenant-a-key":
            assert "tenant B private marker" not in serialized
            return httpx.Response(200, json=_completion("tenant A answer"))
        if token == "Bearer tenant-b-key":
            assert "tenant A private marker" not in serialized
            return httpx.Response(200, json=_completion("tenant B answer"))
        return httpx.Response(401)

    client = HermesClient(_settings(tmp_path), transport=httpx.MockTransport(handler))
    first = client.respond(
        HermesEndpoint("tenant_a", "http://hermes:8700/v1", "tenant-a-key"),
        message="Question A",
        business_context="tenant A private marker",
    )
    second = client.respond(
        HermesEndpoint("tenant_b", "http://hermes:8701/v1", "tenant-b-key"),
        message="Question B",
        business_context="tenant B private marker",
    )

    assert first.content == "tenant A answer"
    assert second.content == "tenant B answer"
    assert [request.url.port for request in requests] == [8700, 8701]
    assert all(request.url.path == "/v1/chat/completions" for request in requests)


def test_client_circuit_breaker_rejects_repeated_profile_failure(tmp_path: Path) -> None:
    calls = 0
    now = [0.0]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="upstream details must never escape")

    breaker = HermesCircuitBreaker(threshold=2, recovery_seconds=30)
    client = HermesClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        clock=lambda: now[0],
        breaker=breaker,
    )
    endpoint = HermesEndpoint("tenant_a", "http://hermes:8700/v1", "private-key")
    for _ in range(2):
        with pytest.raises(HermesUnavailable, match="unavailable") as error:
            client.respond(endpoint, message="question", business_context="safe summary")
        assert "upstream details" not in str(error.value)
    with pytest.raises(HermesCircuitOpen):
        client.respond(endpoint, message="question", business_context="safe summary")
    assert calls == 2

    now[0] = 31
    with pytest.raises(HermesUnavailable):
        client.respond(endpoint, message="question", business_context="safe summary")
    assert calls == 3


def test_client_timeout_maps_to_unavailable_and_opens_circuit_without_leaking(
    tmp_path: Path,
) -> None:
    calls = 0
    private_detail = "private-timeout-detail-must-not-escape"

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(private_detail, request=request)

    client = HermesClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(timeout),
        clock=lambda: 10.0,
        breaker=HermesCircuitBreaker(threshold=2, recovery_seconds=30),
    )
    endpoint = HermesEndpoint("tenant_a", "http://hermes:8700/v1", "private-key")

    for _ in range(2):
        with pytest.raises(HermesUnavailable, match="unreachable") as raised:
            client.respond(endpoint, message="question", business_context="safe summary")
        assert raised.value.retryable is True
        assert private_detail not in str(raised.value)
        assert "private-key" not in str(raised.value)
        assert isinstance(raised.value.__cause__, httpx.ReadTimeout)

    with pytest.raises(HermesCircuitOpen, match="circuit is open") as opened:
        client.respond(endpoint, message="question", business_context="safe summary")

    assert opened.value.retryable is True
    assert calls == 2


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    (
        (401, HermesAuthenticationError),
        (429, HermesRateLimited),
        (503, HermesUnavailable),
    ),
)
def test_client_maps_upstream_failures_without_exposing_response(
    tmp_path: Path,
    status_code: int,
    expected_error: type[Exception],
) -> None:
    client = HermesClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, text="credential=must-not-escape")
        ),
        breaker=HermesCircuitBreaker(threshold=20, recovery_seconds=30),
    )
    with pytest.raises(expected_error) as error:
        client.respond(
            HermesEndpoint("tenant_a", "http://hermes:8700/v1", "private-key"),
            message="question",
            business_context="summary",
        )
    assert "must-not-escape" not in str(error.value)


def test_client_enforces_response_size_and_production_config_boundary(tmp_path: Path) -> None:
    oversized = "x" * 5000
    client = HermesClient(
        _settings(tmp_path, hermes_max_response_bytes=4096),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=_completion(oversized))
        ),
    )
    with pytest.raises(HermesInvalidResponse, match="configured limit"):
        client.respond(
            HermesEndpoint("tenant_a", "http://hermes:8700/v1", "private-key"),
            message="question",
            business_context="summary",
        )

    production_values = {
        "app_env": "production",
        "jwt_secret": "j" * 40,
        "otp_secret": "o" * 40,
        "field_encryption_key": "f" * 40,
        "expose_local_otp": False,
        "seed_demo_data": False,
        "whatsapp_backend": "disabled",
        "bumpa_backend": "disabled",
        "agent_backend": "hermes",
    }
    with pytest.raises(ValidationError, match="HERMES_PROFILE_ROOT"):
        Settings(**production_values)
    configured = Settings(
        **production_values,
        hermes_profile_root="/data/hermes/profiles",
    )
    assert configured.hermes_base_internal_host == "http://hermes"
    assert all("anthropic" not in field.lower() for field in type(configured).model_fields)


def test_profile_provisioning_allocates_unique_ports_and_private_files(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings(tmp_path)

    with factory() as db:
        first_tenant = Tenant(slug="isolation-a", name="Isolation A")
        second_tenant = Tenant(slug="isolation-b", name="Isolation B")
        db.add_all((first_tenant, second_tenant))
        db.flush()
        first = provision_profile(db, first_tenant, settings)
        second = provision_profile(db, second_tenant, settings)
        assert (first.api_port, second.api_port) == (8700, 8701)
        assert first.status == second.status == "provisioning"
        first_key = FieldCipher(settings.field_encryption_key).decrypt(first.encrypted_api_key)
        second_key = FieldCipher(settings.field_encryption_key).decrypt(second.encrypted_api_key)
        assert first_key != second_key

        for profile, key in ((first, first_key), (second, second_key)):
            profile_path = Path(profile.profile_path or "")
            assert stat.S_IMODE(profile_path.stat().st_mode) == 0o700
            assert stat.S_IMODE((profile_path / ".env").stat().st_mode) == 0o600
            env_text = (profile_path / ".env").read_text()
            assert f"API_SERVER_PORT={profile.api_port}" in env_text
            assert f"API_SERVER_KEY={key}" in env_text
            assert "ANTHROPIC_API_KEY" not in env_text
            config_text = (profile_path / "config.yaml").read_text()
            assert "disabled_toolsets" in config_text
            assert "hard_stop_enabled: true" in config_text


def test_admin_provisions_and_web_chat_uses_redacted_hermes_boundary(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex[:8]
    phone = "+23487" + suffix.translate(str.maketrans("abcdef", "123456"))[:8]
    configured = _settings(tmp_path)
    captured: dict[str, str] = {}

    class FakeHermesClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        def respond(
            self,
            endpoint: HermesEndpoint,
            *,
            message: str,
            business_context: str,
        ) -> HermesResult:
            captured.update(
                {
                    "profile": endpoint.profile_name,
                    "message": message,
                    "context": business_context,
                    "api_key": endpoint.api_key,
                }
            )
            return HermesResult("Hermes live answer", 20, 9, 29, 41)

        def readiness(self, _endpoint: HermesEndpoint) -> HermesReadiness:
            return HermesReadiness(True, "ready", 12)

    monkeypatch.setattr("app.services.chat.HermesClient", FakeHermesClient)
    monkeypatch.setattr("app.routes.hermes.HermesClient", FakeHermesClient)
    app.dependency_overrides[get_settings] = lambda: configured
    try:
        operator = auth_headers(client, "+2348099990001")
        created_tenant = client.post(
            "/v1/admin/tenants",
            headers=operator,
            json={"slug": f"hermes-{suffix}", "name": "Hermes Integration"},
        )
        assert created_tenant.status_code == 201, created_tenant.text
        tenant_id = created_tenant.json()["id"]
        created_user = client.post(
            f"/v1/admin/tenants/{tenant_id}/users",
            headers=operator,
            json={"name": "Hermes Owner", "phone_e164": phone, "role": "owner"},
        )
        assert created_user.status_code == 201, created_user.text
        provisioned = client.post(
            f"/v1/admin/tenants/{tenant_id}/hermes-profile",
            headers=operator,
        )
        assert provisioned.status_code == 200, provisioned.text
        assert provisioned.json()["status"] == "provisioning"
        assert "api_key" not in provisioned.text.lower()

        with SessionLocal() as db:
            profile = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == tenant_id))
            assert profile is not None
            profile.status = "active"
            encrypted_key = profile.encrypted_api_key
            db.commit()

        owner = auth_headers(client, phone, tenant_id)
        response = client.post(
            "/v1/chat/web",
            headers=owner,
            json={
                "message": "My email is owner@example.com. Show sales.",
                "client_message_id": f"client-{suffix}",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == "Hermes live answer"
        assert captured["message"] == "My email is [EMAIL]. Show sales."
        assert "API" not in captured["context"].upper()
        assert captured["api_key"] == FieldCipher(configured.field_encryption_key).decrypt(
            encrypted_key
        )

        readiness = client.get("/v1/hermes/profile/readiness", headers=owner)
        assert readiness.status_code == 200, readiness.text
        assert readiness.json() == {"status": "ready", "provider": "hermes", "latency_ms": 12}
        with SessionLocal() as db:
            usage = db.scalar(
                select(UsageEvent)
                .where(UsageEvent.tenant_id == tenant_id)
                .order_by(UsageEvent.created_at.desc())
            )
            assert usage is not None
            assert usage.event_metadata["total_tokens"] == 29
            assert usage.event_metadata["provider"] == "hermes"
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_hermes_profile_url_cannot_escape_private_runtime(tmp_path: Path) -> None:
    client = HermesClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=_completion("x"))),
    )
    with pytest.raises(HermesProfileError, match="private runtime boundary"):
        client.respond(
            HermesEndpoint("tenant", "http://169.254.169.254:8700/v1", "valid-key"),
            message="question",
            business_context="summary",
        )
