from __future__ import annotations

import json
import logging
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import Settings, get_settings
from app.core.dependencies import enforce_cookie_origin
from app.core.logging import JsonFormatter
from app.core.rate_limit import (
    RateLimitExceeded,
    RateLimitUnavailable,
    consume_operation_rate_limit,
    enforce_auth_rate_limit,
)
from app.main import app
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


def test_request_logging_uses_route_template_and_never_query_values(client: TestClient) -> None:
    class ImmediateJsonCapture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.lines.append(JsonFormatter().format(record))

    query_secret = "query-secret-must-never-reach-logs"
    capture = ImmediateJsonCapture()
    request_logger = logging.getLogger("bumpabestie.http")
    request_logger.addHandler(capture)
    try:
        response = client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": query_secret,
                "hub.challenge": "safe-challenge",
            },
            headers={"X-Correlation-ID": "safe-request-log-canary"},
        )
    finally:
        request_logger.removeHandler(capture)

    assert response.status_code == 403
    serialized = "\n".join(capture.lines)
    assert query_secret not in serialized
    completed = next(
        json.loads(line) for line in capture.lines if '"message": "request_completed"' in line
    )
    assert completed == {
        "level": "INFO",
        "logger": "bumpabestie.http",
        "message": "request_completed",
        "correlation_id": "safe-request-log-canary",
        "duration_ms": completed["duration_ms"],
        "method": "GET",
        "path": "/webhooks/whatsapp",
        "status_code": 403,
    }


class FakeHealthProbe:
    snapshot: dict[str, object] = {}

    def __init__(self, _config: object) -> None:
        pass

    def health_snapshot(self) -> dict[str, object]:
        return self.snapshot


def test_readiness_requires_redis_worker_and_scheduler_heartbeats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASYNC_RUNTIME_ENABLED", "true")
    monkeypatch.setattr("app.main.RedisHealthProbe", FakeHealthProbe)

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
