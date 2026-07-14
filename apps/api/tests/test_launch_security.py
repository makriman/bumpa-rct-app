from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from types import FunctionType
from typing import cast
from uuid import RFC_4122, UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import Settings, get_settings
from app.core.dependencies import enforce_cookie_origin
from app.core.logging import JsonFormatter, configure_logging
from app.core.rate_limit import (
    RateLimitExceeded,
    RateLimitUnavailable,
    consume_operation_rate_limit,
    enforce_auth_rate_limit,
)
from app.main import app
from app.providers.bumpa import BumpaClient
from tests.conftest import auth_headers


def hardened_settings() -> Settings:
    base = get_settings()
    return Settings(
        app_env="staging",
        database_url=base.database_url,
        artifact_root=base.artifact_root,
        jwt_secret=base.jwt_secret,
        otp_secret=base.otp_secret,
        field_encryption_key=base.field_encryption_key,
        auth_rate_limit_enabled=True,
        operation_rate_limit_enabled=True,
        expose_local_otp=False,
        seed_demo_data=False,
        whatsapp_backend="disabled",
        bumpa_backend="disabled",
        agent_backend="disabled",
        cors_origins=["https://app.example.test"],
    )


def test_production_cannot_disable_auth_rate_limiting() -> None:
    with pytest.raises(ValidationError, match="rate limiting cannot be disabled"):
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
            auth_rate_limit_enabled=False,
        )

    with pytest.raises(ValidationError, match="operation rate limiting cannot be disabled"):
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
            operation_rate_limit_enabled=False,
        )


def request(
    *,
    method: str = "POST",
    client: str = "8.8.8.8",
    origin: str | None = None,
) -> Request:
    headers = [] if origin is None else [(b"origin", origin.encode())]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": "/v1/settings/profile",
            "raw_path": b"/v1/settings/profile",
            "query_string": b"",
            "headers": headers,
            "client": (client, 44321),
            "server": ("api.example.test", 443),
        }
    )


def test_cookie_origin_policy_rejects_cross_site_and_supports_internal_bff() -> None:
    settings = hardened_settings()
    enforce_cookie_origin(request(origin="https://app.example.test/path"), settings)
    enforce_cookie_origin(request(client="172.20.0.12"), settings)
    enforce_cookie_origin(request(method="GET", origin="https://attacker.test"), settings)

    with pytest.raises(HTTPException) as cross_site:
        enforce_cookie_origin(request(origin="https://attacker.test"), settings)
    assert cross_site.value.status_code == 403
    with pytest.raises(HTTPException) as missing_origin:
        enforce_cookie_origin(request(), settings)
    assert missing_origin.value.status_code == 403


def test_cors_preflight_allows_revision_preconditions(client: TestClient) -> None:
    origin = get_settings().effective_cors_origins[0]
    response = client.options(
        "/v1/admin/onboarding/test-id/owner",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization,Content-Type,If-Match",
        },
    )

    assert response.status_code == 200
    allowed_headers = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert {"authorization", "content-type", "if-match"} <= allowed_headers


def test_cors_preflight_allows_audited_platform_access_put(client: TestClient) -> None:
    origin = get_settings().effective_cors_origins[0]
    response = client.options(
        "/v1/admin/platform-access/test-user/operator",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": ("Authorization,Content-Type,X-Access-Reason"),
        },
    )

    assert response.status_code == 200
    allowed_methods = {
        method.strip().lower()
        for method in response.headers["access-control-allow-methods"].split(",")
    }
    allowed_headers = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert "put" in allowed_methods
    assert {"authorization", "content-type", "x-access-reason"} <= allowed_headers


def test_cookie_auth_mutation_enforces_origin_but_bearer_is_not_csrf_scoped(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "+2348012345678")
    token = headers["Authorization"].removeprefix("Bearer ")
    original_name = client.get("/v1/auth/me", headers=headers).json()["user"]["name"]
    configured = hardened_settings()
    app.dependency_overrides[get_settings] = lambda: configured
    try:
        blocked = client.patch(
            "/v1/settings/profile",
            headers={"Cookie": f"bb_session={token}", "Origin": "https://attacker.test"},
            json={"name": original_name},
        )
        assert blocked.status_code == 403

        allowed = client.patch(
            "/v1/settings/profile",
            headers={
                "Cookie": f"bb_session={token}",
                "Origin": "https://app.example.test",
            },
            json={"name": original_name},
        )
        assert allowed.status_code == 200

        bearer = client.patch(
            "/v1/settings/profile",
            headers={**headers, "Origin": "https://attacker.test"},
            json={"name": original_name},
        )
        assert bearer.status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)


class FakeRateLimitRedis:
    def __init__(self, result: list[int] | None = None, error: Exception | None = None) -> None:
        self.result = result or [1, 0]
        self.error = error
        self.arguments: tuple[str, ...] = ()

    def eval(self, _script: str, key_count: int, *arguments: str) -> list[int]:
        assert key_count == 2
        if self.error:
            raise self.error
        self.arguments = arguments
        return self.result


def test_auth_rate_limit_uses_private_digests_and_returns_retry_after() -> None:
    settings = hardened_settings()
    allowed = FakeRateLimitRedis()
    enforce_auth_rate_limit(
        request(),
        phone_e164="+2348000000000",
        operation="request",
        settings=settings,
        client=cast(Redis, allowed),
    )
    assert all("+2348000000000" not in argument for argument in allowed.arguments)
    assert allowed.arguments[-3:] == (
        str(settings.auth_rate_limit_window_seconds),
        str(settings.auth_request_phone_limit),
        str(settings.auth_request_ip_limit),
    )

    blocked = FakeRateLimitRedis([0, 27])
    with pytest.raises(HTTPException) as throttled:
        enforce_auth_rate_limit(
            request(),
            phone_e164="+2348000000000",
            operation="verify",
            settings=settings,
            client=cast(Redis, blocked),
        )
    assert throttled.value.status_code == 429
    assert throttled.value.headers == {"Retry-After": "27"}

    unavailable = FakeRateLimitRedis(error=RedisError("unavailable"))
    with pytest.raises(HTTPException) as failed_closed:
        enforce_auth_rate_limit(
            request(),
            phone_e164="+2348000000000",
            operation="verify",
            settings=settings,
            client=cast(Redis, unavailable),
        )
    assert failed_closed.value.status_code == 503


def test_costly_operation_limits_are_private_and_fail_closed() -> None:
    settings = hardened_settings()
    allowed = FakeRateLimitRedis()
    consume_operation_rate_limit(
        settings,
        operation="chat",
        scopes={"tenant": "tenant-sensitive", "user": "user-sensitive"},
        limit=7,
        window_seconds=60,
        client=cast(Redis, allowed),
    )
    assert all("sensitive" not in argument for argument in allowed.arguments)
    assert allowed.arguments[-3:] == ("60", "7", "7")

    with pytest.raises(RateLimitExceeded) as throttled:
        consume_operation_rate_limit(
            settings,
            operation="chat",
            scopes={"tenant": "tenant-sensitive", "user": "user-sensitive"},
            limit=7,
            window_seconds=60,
            client=cast(Redis, FakeRateLimitRedis([0, 19])),
        )
    assert throttled.value.retry_after == 19

    with pytest.raises(RateLimitUnavailable):
        consume_operation_rate_limit(
            settings,
            operation="chat",
            scopes={"tenant": "tenant-sensitive", "user": "user-sensitive"},
            limit=7,
            window_seconds=60,
            client=cast(Redis, FakeRateLimitRedis(error=RedisError("unavailable"))),
        )


def test_json_formatter_emits_allowlisted_job_context_only() -> None:
    record = logging.LogRecord("worker", logging.INFO, __file__, 1, "finished", (), None)
    record.job_id = "job-123"
    record.job_kind = "whatsapp.process_webhook"
    record.attempt = 2
    record.api_key = "must-never-appear"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["job_id"] == "job-123"
    assert payload["job_kind"] == "whatsapp.process_webhook"
    assert payload["attempt"] == 2
    assert "api_key" not in payload
    assert "must-never-appear" not in json.dumps(payload)


def test_json_formatter_revalidates_provider_diagnostics_at_serialization() -> None:
    raw_secret = "+2348000000000 123456 bearer-token raw-response-body"
    record = logging.LogRecord("provider", logging.WARNING, __file__, 1, "failed", (), None)
    record.provider = "meta"
    record.provider_operation = "otp_delivery"
    record.provider_category = "provider"
    record.provider_retryable = False
    record.provider_http_status = raw_secret
    record.provider_code = raw_secret
    record.provider_request_id_hash = raw_secret
    record.retry_after_seconds = raw_secret
    record.sync_run_id = raw_secret

    payload = json.loads(JsonFormatter().format(record))

    assert payload == {
        "level": "WARNING",
        "logger": "provider",
        "message": "failed",
        "correlation_id": None,
        "provider": "meta",
        "provider_operation": "otp_delivery",
        "provider_category": "provider",
        "provider_retryable": False,
    }
    assert raw_secret not in json.dumps(payload)


def _raise_observability_canaries() -> None:
    local_canary = "+2348000000000 123456 bearer-token-must-never-escape"
    try:
        raise ValueError("cause-api-key-must-never-escape")
    except ValueError as cause:
        raise RuntimeError(f"source-line-raw-response-must-never-escape {local_canary}") from cause


def test_json_formatter_emits_bounded_frames_without_exception_secrets() -> None:
    source_path_canary = "source-path-api-key-must-never-escape"
    synthetic_code = _raise_observability_canaries.__code__.replace(
        co_filename=f"/sensitive/{source_path_canary}.py",
        co_name="synthetic_provider_failure",
        co_qualname="synthetic_provider_failure",
    )
    trigger = cast(
        Callable[[], None],
        FunctionType(synthetic_code, {"__name__": "tests.synthetic_provider"}),
    )

    def recurse(depth: int) -> None:
        if depth:
            recurse(depth - 1)
        else:
            trigger()

    try:
        recurse(12)
    except RuntimeError as exc:
        record = logging.LogRecord("worker", logging.ERROR, __file__, 1, "cycle_failed", (), None)
        record.exc_info = (type(exc), exc, exc.__traceback__)
        innermost_traceback = exc.__traceback__
        assert innermost_traceback is not None
        while innermost_traceback.tb_next is not None:
            innermost_traceback = innermost_traceback.tb_next
        expected_innermost_line = innermost_traceback.tb_lineno

    payload = json.loads(JsonFormatter().format(record))
    serialized = json.dumps(payload)
    frames = payload.pop("exception_frames")

    assert payload == {
        "level": "ERROR",
        "logger": "worker",
        "message": "cycle_failed",
        "correlation_id": None,
        "exception_type": "RuntimeError",
    }
    assert len(frames) == 8
    assert frames[-1] == {
        "module": "tests.synthetic_provider",
        "function": "synthetic_provider_failure",
        "line": expected_innermost_line,
    }
    for secret in (
        source_path_canary,
        "+2348000000000",
        "123456",
        "bearer-token-must-never-escape",
        "cause-api-key-must-never-escape",
        "source-line-raw-response-must-never-escape",
    ):
        assert secret not in serialized
    assert "exception" not in payload


def test_configured_logging_suppresses_httpx_provider_query_strings(capsys) -> None:
    scope_secret = "business-scope-secret-must-never-reach-logs"

    def success(request: httpx.Request) -> httpx.Response:
        assert request.url.params["business_id"] == scope_secret
        return httpx.Response(200, json={"data": {"value": "1"}})

    configure_logging()
    with BumpaClient(
        "api-key-must-never-reach-logs",
        "business_id",
        scope_secret,
        client=httpx.Client(
            base_url="https://api.getbumpa.com/api",
            transport=httpx.MockTransport(success),
        ),
    ) as provider:
        status, _payload, _headers = provider.get_analytics(
            "sales",
            "overview",
            date(2026, 1, 1),
            date(2026, 1, 2),
        )

    assert status == 200
    serialized = capsys.readouterr().err
    assert scope_secret not in serialized
    assert "business_id" not in serialized
    assert "dataset=overview" not in serialized


def test_request_context_preserves_canonical_uuid4_across_service_hops(
    client: TestClient,
) -> None:
    correlation_id = "550e8400-e29b-41d4-a716-446655440000"

    response = client.post(
        "/v1/auth/verify-otp",
        json={},
        headers={"X-Correlation-ID": correlation_id},
    )

    assert response.status_code == 422
    assert response.headers["x-correlation-id"] == correlation_id
    assert response.json()["error"]["correlation_id"] == correlation_id


@pytest.mark.parametrize(
    "untrusted_correlation_id",
    [
        "not-a-uuid",
        "+2348000000000-123456-bearer-token",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "550E8400-E29B-41D4-A716-446655440000",
        "550e8400e29b41d4a716446655440000",
        "{550e8400-e29b-41d4-a716-446655440000}",
        "550e8400-e29b-41d4-7716-446655440000",
    ],
)
def test_request_context_replaces_untrusted_correlation_ids(
    client: TestClient,
    untrusted_correlation_id: str,
) -> None:
    response = client.get(
        "/health/live",
        headers={"X-Correlation-ID": untrusted_correlation_id},
    )

    assert response.status_code == 200
    replacement = response.headers["x-correlation-id"]
    parsed = UUID(replacement)
    assert replacement != untrusted_correlation_id
    assert str(parsed) == replacement
    assert parsed.version == 4
    assert parsed.variant == RFC_4122


def test_request_logging_uses_route_template_and_never_query_values(client: TestClient) -> None:
    class ImmediateJsonCapture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.lines.append(JsonFormatter().format(record))

    query_secret = "query-secret-must-never-reach-logs"
    caller_correlation_secret = "+2348000000000-123456-bearer-secret"
    capture = ImmediateJsonCapture()
    request_logger = logging.getLogger("bumpabestie.http")
    previous_disabled = request_logger.disabled
    previous_level = request_logger.level
    request_logger.disabled = False
    request_logger.setLevel(logging.INFO)
    request_logger.addHandler(capture)
    try:
        response = client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": query_secret,
                "hub.challenge": "safe-challenge",
            },
            headers={"X-Correlation-ID": caller_correlation_secret},
        )
    finally:
        request_logger.removeHandler(capture)
        request_logger.setLevel(previous_level)
        request_logger.disabled = previous_disabled

    assert response.status_code == 403
    serialized = "\n".join(capture.lines)
    assert query_secret not in serialized
    assert caller_correlation_secret not in serialized
    completed = next(
        json.loads(line) for line in capture.lines if '"message": "request_completed"' in line
    )
    correlation_id = completed["correlation_id"]
    assert isinstance(correlation_id, str)
    assert str(UUID(correlation_id)) == correlation_id
    assert response.headers["x-correlation-id"] == correlation_id
    assert completed == {
        "level": "INFO",
        "logger": "bumpabestie.http",
        "message": "request_completed",
        "correlation_id": correlation_id,
        "duration_ms": completed["duration_ms"],
        "method": "GET",
        "path": "/webhooks/whatsapp",
        "status_code": 403,
    }


class FakeHealthProbe:
    snapshot: dict[str, object] = {}
    instances = 0

    def __init__(self, _config: object) -> None:
        type(self).instances += 1

    def health_snapshot(self) -> dict[str, object]:
        return self.snapshot


def test_readiness_requires_redis_worker_and_scheduler_heartbeats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASYNC_RUNTIME_ENABLED", "true")
    monkeypatch.setattr("app.main.RedisHealthProbe", FakeHealthProbe)
    from app.main import _redis_health_probe

    _redis_health_probe.cache_clear()
    FakeHealthProbe.instances = 0
    try:
        FakeHealthProbe.snapshot = {
            "redis": "ok",
            "worker": "stale",
            "scheduler": "ok",
            "queued_wakeups": 0,
        }
        unavailable = client.get("/health/ready")
        assert unavailable.status_code == 503
        assert unavailable.json()["status"] == "not_ready"
        assert unavailable.json()["async_runtime"]["worker"] == "stale"

        FakeHealthProbe.snapshot = {
            "redis": "ok",
            "worker": "ok",
            "scheduler": "ok",
            "queued_wakeups": 0,
        }
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert FakeHealthProbe.instances == 1
    finally:
        _redis_health_probe.cache_clear()
