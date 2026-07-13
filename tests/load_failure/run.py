#!/usr/bin/env python3
"""Deterministic Compose load and dependency-restart drill.

The isolated Compose project uses synthetic WhatsApp payloads and local provider
adapters only. PostgreSQL is authoritative; Redis is exercised strictly as the
wake-up transport used by the production architecture.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import http.client
import importlib.util
import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, TypeVar
from unittest.mock import patch

EVENT_COUNT = 50
CHAT_ATTEMPTS = 8
CHAT_LIMIT = 4
SYNC_ATTEMPTS = 4
SYNC_LIMIT = 2
FIXTURE_SECRET = b"load-failure-fixture-app-secret"
PROJECT_NAME = "bumpabestie-load-failure"
OUTAGE_READINESS_HTTP_TIMEOUT_SECONDS = 7
ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (ROOT / "compose.yaml", Path(__file__).with_name("compose.yaml"))
DISK_CHECK_SCRIPT = ROOT / "scripts" / "check_disk_usage.py"
ALERT_SCRIPT = ROOT / "scripts" / "send_ops_alert.py"
T = TypeVar("T")

HOST_ENV_ALLOWLIST = frozenset(
    {
        "BUILDKIT_PROGRESS",
        "CI",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_DEFAULT_PLATFORM",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "TERM",
        "TMPDIR",
        "USER",
        "XDG_CONFIG_HOME",
    }
)
SYNTHETIC_COMPOSE_ENV = {
    "APP_ENV": "staging",
    "APP_POSTGRES_PASSWORD": "load-failure-app-postgres-only",
    "BUMPA_BACKEND": "mock",
    "COOKIE_SECRET": "load-failure-cookie-secret-at-least-32-characters",
    "DATABASE_URL": (
        "postgresql+psycopg://bumpabestie_app:load-failure-app-postgres-only"
        "@postgres:5432/bumpabestie"
    ),
    "FIELD_ENCRYPTION_KEY": "load-failure-field-key-at-least-32-characters",
    "GOOGLE_OAUTH_CLIENT_ID": "",
    "GOOGLE_OAUTH_CLIENT_SECRET": "",
    "GOOGLE_OAUTH_CLIENT_SECRET_FILE": "",
    "IMAGE_TAG": "local",
    "INTERNAL_SERVICE_TOKEN": "load-failure-internal-token-at-least-32-characters",
    "JWT_SECRET": "load-failure-jwt-secret-at-least-32-characters",
    "LOAD_FAILURE_FIXTURE_MODE": "true",
    "META_ADS_OAUTH_CLIENT_ID": "",
    "META_ADS_OAUTH_CLIENT_SECRET": "",
    "META_ADS_OAUTH_CLIENT_SECRET_FILE": "",
    "META_APP_ID": "",
    "META_APP_SECRET": FIXTURE_SECRET.decode(),
    "META_APP_SECRET_FILE": "",
    "META_BUSINESS_ID": "",
    "META_PHONE_NUMBER": "",
    "META_PHONE_NUMBER_ID": "",
    "META_SYSTEM_USER_ACCESS_TOKEN": "",
    "META_SYSTEM_USER_ACCESS_TOKEN_FILE": "",
    "META_TEST_SENDER_DISPLAY_PHONE_E164": "+15555550123",
    "META_TEST_SENDER_PHONE_NUMBER_ID": "12345",
    "META_TEST_SENDER_VERIFICATION_MODE": "disabled",
    "META_TEST_SENDER_WABA_ID": "12345",
    "META_WABA_ID": "",
    "META_WEBHOOK_VERIFY_TOKEN": "",
    "META_WEBHOOK_VERIFY_TOKEN_FILE": "",
    "MIGRATION_DATABASE_URL": (
        "postgresql+psycopg://bumpabestie:load-failure-postgres-only@postgres:5432/bumpabestie"
    ),
    "OPS_ALERTS_ENABLED": "false",
    "OPS_ALERT_HMAC_SECRET_FILE": "",
    "OPS_ALERT_WEBHOOK_URL": "",
    "OTP_SECRET": "load-failure-otp-secret-at-least-32-characters",
    "POSTGRES_DB": "bumpabestie",
    "POSTGRES_PASSWORD": "load-failure-postgres-only",
    "POSTGRES_USER": "bumpabestie",
    "REDIS_URL": "redis://redis:6379/0",
    "SEED_DEMO_DATA": "false",
    "WHATSAPP_BACKEND": "mock",
    "AGENT_BACKEND": "mock",
}
EMPTY_PROVIDER_VALUES = frozenset(
    {
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_CLIENT_SECRET_FILE",
        "META_ADS_OAUTH_CLIENT_ID",
        "META_ADS_OAUTH_CLIENT_SECRET",
        "META_ADS_OAUTH_CLIENT_SECRET_FILE",
        "META_APP_ID",
        "META_APP_SECRET_FILE",
        "META_BUSINESS_ID",
        "META_PHONE_NUMBER",
        "META_PHONE_NUMBER_ID",
        "META_SYSTEM_USER_ACCESS_TOKEN",
        "META_SYSTEM_USER_ACCESS_TOKEN_FILE",
        "META_WABA_ID",
        "META_WEBHOOK_VERIFY_TOKEN",
        "META_WEBHOOK_VERIFY_TOKEN_FILE",
        "OPS_ALERT_HMAC_SECRET_FILE",
        "OPS_ALERT_WEBHOOK_URL",
    }
)


class DrillFailure(RuntimeError):
    """A sanitized assertion failure from the local drill."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    latency_ms: float
    response: dict[str, Any] | None


@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class LoadMetrics:
    requests: int
    successful: int
    errors: int
    duplicates: int
    wall_time_ms: float
    throughput_per_second: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_max_ms: float


class ComposeProject:
    def __init__(self, *, port: int) -> None:
        self.environment = {
            **{key: os.environ[key] for key in HOST_ENV_ALLOWLIST if key in os.environ},
            **SYNTHETIC_COMPOSE_ENV,
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "COMPOSE_PROJECT_NAME": PROJECT_NAME,
            "LOAD_FAILURE_PORT": str(port),
        }
        self.command = [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "--profile",
            "async",
        ]
        for file_path in COMPOSE_FILES:
            self.command.extend(("-f", str(file_path)))

    def run(
        self,
        *arguments: str,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed Docker executable and controlled argv only
            [*self.command, *arguments],
            cwd=ROOT,
            env=self.environment,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )

    def sql_json(self, query: str, *, event_prefix: str) -> dict[str, Any]:
        command = (
            "printf '%s\\n' \"$1\" | psql -X -v ON_ERROR_STOP=1 "
            '-U "$POSTGRES_USER" -d "$POSTGRES_DB" -v "event_prefix=$2" -At'
        )
        result = self.run(
            "exec",
            "-T",
            "postgres",
            "sh",
            "-eu",
            "-c",
            command,
            "load-failure-query",
            query,
            event_prefix,
            capture=True,
        )
        try:
            decoded = json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise DrillFailure("PostgreSQL returned an invalid drill result") from exc
        if not isinstance(decoded, dict):
            raise DrillFailure("PostgreSQL drill result must be an object")
        return decoded


def validate_rendered_compose(document: object) -> None:
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise DrillFailure("Compose returned an invalid isolated-stack configuration")
    services = document["services"]

    def environment(service_name: str) -> dict[str, str]:
        service = services.get(service_name)
        raw = service.get("environment") if isinstance(service, dict) else None
        if not isinstance(raw, dict):
            raise DrillFailure("An isolated service is missing its sealed environment")
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}

    for service_name in ("api", "worker", "scheduler"):
        values = environment(service_name)
        expected = {
            "AGENT_BACKEND": "mock",
            "APP_ENV": "staging",
            "BUMPA_BACKEND": "mock",
            "DATABASE_URL": SYNTHETIC_COMPOSE_ENV["DATABASE_URL"],
            "LOAD_FAILURE_FIXTURE_MODE": "true",
            "META_APP_SECRET": FIXTURE_SECRET.decode(),
            "REDIS_URL": SYNTHETIC_COMPOSE_ENV["REDIS_URL"],
            "WHATSAPP_BACKEND": "mock",
        }
        if any(values.get(key) != value for key, value in expected.items()):
            raise DrillFailure("An isolated application service escaped its sealed environment")
        if any(values.get(key) for key in EMPTY_PROVIDER_VALUES):
            raise DrillFailure("An isolated application service retained a live provider value")
        if any(value for key, value in values.items() if key.endswith("_FILE")):
            raise DrillFailure("An isolated application service retained a secret-file reference")

    migrate = environment("migrate")
    if migrate.get("MIGRATION_DATABASE_URL") != SYNTHETIC_COMPOSE_ENV["MIGRATION_DATABASE_URL"]:
        raise DrillFailure("The isolated migration service escaped its database boundary")
    postgres = environment("postgres")
    expected_postgres = {
        "APP_POSTGRES_PASSWORD": SYNTHETIC_COMPOSE_ENV["APP_POSTGRES_PASSWORD"],
        "POSTGRES_DB": SYNTHETIC_COMPOSE_ENV["POSTGRES_DB"],
        "POSTGRES_PASSWORD": SYNTHETIC_COMPOSE_ENV["POSTGRES_PASSWORD"],
        "POSTGRES_USER": SYNTHETIC_COMPOSE_ENV["POSTGRES_USER"],
    }
    if any(postgres.get(key) != value for key, value in expected_postgres.items()):
        raise DrillFailure("The isolated PostgreSQL service escaped its credential boundary")


def payload(event_id: str, sender_suffix: int) -> bytes:
    document = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "synthetic-waba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "synthetic-phone-id",
                            },
                            "messages": [
                                {
                                    "from": f"4477009{sender_suffix:06d}",
                                    "id": event_id,
                                    "timestamp": "1783814400",
                                    "text": {"body": "Synthetic load drill"},
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def signature(body: bytes) -> str:
    digest = hmac.new(FIXTURE_SECRET, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _connection(base_url: str, timeout: float) -> http.client.HTTPConnection:
    if not base_url.startswith("http://127.0.0.1:"):
        raise DrillFailure("The load drill may only call an IPv4 loopback HTTP endpoint")
    try:
        port = int(base_url.rsplit(":", 1)[1])
    except ValueError as exc:
        raise DrillFailure("The load drill URL has an invalid port") from exc
    return http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)


def post(base_url: str, body: bytes, *, timeout: float = 10) -> HttpResult:
    started = time.perf_counter()
    connection = _connection(base_url, timeout)
    try:
        connection.request(
            "POST",
            "/webhooks/whatsapp",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Host": "api.bumpabestie.localhost",
                "X-Hub-Signature-256": signature(body),
            },
        )
        response = connection.getresponse()
        raw = response.read()
        try:
            decoded = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            decoded = None
        return HttpResult(
            status=response.status,
            latency_ms=(time.perf_counter() - started) * 1000,
            response=decoded if isinstance(decoded, dict) else None,
        )
    except (TimeoutError, OSError, http.client.HTTPException):
        return HttpResult(
            status=0,
            latency_ms=(time.perf_counter() - started) * 1000,
            response=None,
        )
    finally:
        connection.close()


def request_api(base_url: str, request: ApiRequest, *, timeout: float = 10) -> HttpResult:
    if request.method not in {"GET", "POST"} or not request.path.startswith("/v1/"):
        raise DrillFailure("The pressure drill requested an unsupported API operation")
    encoded = (
        json.dumps(request.body, separators=(",", ":"), sort_keys=True).encode()
        if request.body is not None
        else None
    )
    headers = {
        "Accept": "application/json",
        "Host": "api.bumpabestie.localhost",
        **request.headers,
    }
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    started = time.perf_counter()
    connection = _connection(base_url, timeout)
    try:
        connection.request(request.method, request.path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        try:
            decoded = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            decoded = None
        return HttpResult(
            status=response.status,
            latency_ms=(time.perf_counter() - started) * 1000,
            response=decoded if isinstance(decoded, dict) else None,
        )
    except (TimeoutError, OSError, http.client.HTTPException):
        return HttpResult(
            status=0,
            latency_ms=(time.perf_counter() - started) * 1000,
            response=None,
        )
    finally:
        connection.close()


def get_status(url: str, *, timeout: float = 3) -> int:
    if not url.endswith("/health/ready"):
        raise DrillFailure("Only the readiness endpoint may be polled")
    connection = _connection(url.removesuffix("/health/ready"), timeout)
    try:
        connection.request("GET", "/health/ready")
        response = connection.getresponse()
        response.read()
        return response.status
    except (TimeoutError, OSError, http.client.HTTPException):
        return 0
    finally:
        connection.close()


def wait_until(  # noqa: UP047 - runner supports the host's pre-3.12 Python
    description: str,
    predicate: Callable[[], T | None | bool],
    *,
    timeout: float,
    interval: float = 0.25,
) -> T | bool:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (DrillFailure, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(interval)
    suffix = f" ({type(last_error).__name__})" if last_error else ""
    raise DrillFailure(f"Timed out waiting for {description}{suffix}")


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[rank]


def concurrent_batch(base_url: str, bodies: list[bytes]) -> tuple[list[HttpResult], float]:
    barrier = threading.Barrier(len(bodies) + 1)

    def submit(body: bytes) -> HttpResult:
        barrier.wait(timeout=10)
        return post(base_url, body)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(bodies), thread_name_prefix="webhook-load") as pool:
        futures = [pool.submit(submit, body) for body in bodies]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    return results, (time.perf_counter() - started) * 1000


def concurrent_api_batch(
    base_url: str, requests: list[ApiRequest]
) -> tuple[list[HttpResult], float]:
    if not requests:
        raise DrillFailure("The pressure request batch must not be empty")
    barrier = threading.Barrier(len(requests) + 1)

    def submit(request: ApiRequest) -> HttpResult:
        barrier.wait(timeout=10)
        return request_api(base_url, request)

    started = time.perf_counter()
    with ThreadPoolExecutor(
        max_workers=len(requests), thread_name_prefix="authenticated-pressure"
    ) as pool:
        futures = [pool.submit(submit, request) for request in requests]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    return results, (time.perf_counter() - started) * 1000


def metrics(results: list[HttpResult], wall_time_ms: float) -> LoadMetrics:
    latencies = [result.latency_ms for result in results]
    duplicates = sum(
        result.response is not None and result.response.get("status") == "duplicate"
        for result in results
    )
    successful = sum(result.status == 200 for result in results)
    return LoadMetrics(
        requests=len(results),
        successful=successful,
        errors=len(results) - successful,
        duplicates=duplicates,
        wall_time_ms=round(wall_time_ms, 3),
        throughput_per_second=round(len(results) / (wall_time_ms / 1000), 3),
        latency_p50_ms=round(statistics.median(latencies), 3),
        latency_p95_ms=round(percentile(latencies, 0.95), 3),
        latency_max_ms=round(max(latencies, default=0), 3),
    )


def event_state(project: ComposeProject, event_prefix: str) -> dict[str, Any]:
    return project.sql_json(
        """
        SELECT json_build_object(
          'events', (SELECT count(*) FROM provider_webhook_events
                     WHERE external_event_id LIKE :'event_prefix' || '%'),
          'processed_events', (SELECT count(*) FROM provider_webhook_events
                     WHERE external_event_id LIKE :'event_prefix' || '%' AND processing_status = 'processed'),
          'failed_events', (SELECT count(*) FROM provider_webhook_events
                     WHERE external_event_id LIKE :'event_prefix' || '%' AND processing_status = 'failed'),
          'jobs', (SELECT count(*) FROM async_jobs
                   WHERE idempotency_key LIKE 'meta:' || :'event_prefix' || '%'),
          'succeeded_jobs', (SELECT count(*) FROM async_jobs
                   WHERE idempotency_key LIKE 'meta:' || :'event_prefix' || '%' AND status = 'succeeded'),
          'dead_letter_jobs', (SELECT count(*) FROM async_jobs
                   WHERE idempotency_key LIKE 'meta:' || :'event_prefix' || '%' AND status = 'dead_letter'),
          'job_attempts', (SELECT coalesce(sum(attempts), 0) FROM async_jobs
                   WHERE idempotency_key LIKE 'meta:' || :'event_prefix' || '%'),
          'pending_outboxes', (SELECT count(*) FROM job_outbox o JOIN async_jobs j ON j.id = o.job_id
                   WHERE j.idempotency_key LIKE 'meta:' || :'event_prefix' || '%' AND o.status = 'pending')
        )::text;
        """,
        event_prefix=event_prefix,
    )


def exact_state(expected: dict[str, int]) -> Callable[[dict[str, Any]], bool]:
    return lambda state: all(state.get(key) == value for key, value in expected.items())


def seed_pressure_fixtures(project: ComposeProject, run_id: str) -> dict[str, dict[str, str]]:
    seeded = project.run(
        "exec",
        "-T",
        "api",
        "python",
        "/opt/bumpabestie-fixtures/seed-pressure.py",
        run_id,
        capture=True,
        check=False,
    )
    if seeded.returncode != 0:
        raise DrillFailure("The isolated authenticated pressure fixture could not be created")
    try:
        decoded = json.loads(seeded.stdout)
    except json.JSONDecodeError as exc:
        raise DrillFailure("The authenticated pressure fixture returned invalid data") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"a", "b"}:
        raise DrillFailure("The authenticated pressure fixture returned an invalid schema")
    fixtures: dict[str, dict[str, str]] = {}
    for side in ("a", "b"):
        fixture = decoded.get(side)
        if not isinstance(fixture, dict) or set(fixture) != {"tenant_id", "user_id", "token"}:
            raise DrillFailure("The authenticated pressure fixture returned an invalid schema")
        if not all(isinstance(fixture.get(key), str) and fixture[key] for key in fixture):
            raise DrillFailure("The authenticated pressure fixture returned invalid values")
        expected_tenant = f"lf-pressure-{side}-{run_id}"
        expected_user = f"lf-user-{side}-{run_id}"
        if fixture["tenant_id"] != expected_tenant or fixture["user_id"] != expected_user:
            raise DrillFailure("The authenticated pressure fixture escaped its run boundary")
        fixtures[side] = fixture
    return fixtures


def status_counts(results: list[HttpResult]) -> dict[str, int]:
    return {
        str(status): sum(item.status == status for item in results)
        for status in sorted({item.status for item in results})
    }


def pressure_state(project: ComposeProject, run_id: str) -> dict[str, Any]:
    return project.sql_json(
        """
        WITH fixture AS (
          SELECT
            'lf-pressure-a-' || :'event_prefix' AS tenant_a,
            'lf-pressure-b-' || :'event_prefix' AS tenant_b,
            'lf-user-a-' || :'event_prefix' AS user_a,
            'lf-user-b-' || :'event_prefix' AS user_b
        )
        SELECT json_build_object(
          'fixture_tenants', (SELECT count(*) FROM tenants, fixture
                              WHERE id IN (fixture.tenant_a, fixture.tenant_b)),
          'fixture_users', (SELECT count(*) FROM users, fixture
                            WHERE id IN (fixture.user_a, fixture.user_b)),
          'active_owner_memberships', (SELECT count(*) FROM tenant_memberships, fixture
                            WHERE ((tenant_id = fixture.tenant_a AND user_id = fixture.user_a)
                               OR (tenant_id = fixture.tenant_b AND user_id = fixture.user_b))
                              AND role = 'owner' AND status = 'active'),
          'auth_sessions', (SELECT count(*) FROM auth_sessions, fixture
                            WHERE user_id IN (fixture.user_a, fixture.user_b)),
          'tenant_a_conversations', (SELECT count(*) FROM conversations, fixture
                                     WHERE tenant_id = fixture.tenant_a),
          'tenant_a_messages', (SELECT count(*) FROM agent_messages, fixture
                                WHERE tenant_id = fixture.tenant_a),
          'tenant_a_inbound', (SELECT count(*) FROM agent_messages, fixture
                               WHERE tenant_id = fixture.tenant_a AND direction = 'inbound'),
          'tenant_a_outbound', (SELECT count(*) FROM agent_messages, fixture
                                WHERE tenant_id = fixture.tenant_a AND direction = 'outbound'),
          'tenant_a_external_ids', (SELECT count(DISTINCT external_message_id)
                                    FROM agent_messages, fixture
                                    WHERE tenant_id = fixture.tenant_a
                                      AND direction = 'inbound'),
          'tenant_b_conversations', (SELECT count(*) FROM conversations, fixture
                                     WHERE tenant_id = fixture.tenant_b),
          'tenant_b_messages', (SELECT count(*) FROM agent_messages, fixture
                                WHERE tenant_id = fixture.tenant_b),
          'tenant_b_inbound', (SELECT count(*) FROM agent_messages, fixture
                               WHERE tenant_id = fixture.tenant_b AND direction = 'inbound'),
          'tenant_b_outbound', (SELECT count(*) FROM agent_messages, fixture
                                WHERE tenant_id = fixture.tenant_b AND direction = 'outbound'),
          'tenant_b_external_ids', (SELECT count(DISTINCT external_message_id)
                                    FROM agent_messages, fixture
                                    WHERE tenant_id = fixture.tenant_b
                                      AND direction = 'inbound'),
          'wrong_owner_conversations', (SELECT count(*) FROM conversations, fixture
                                        WHERE (tenant_id = fixture.tenant_a
                                               AND user_id <> fixture.user_a)
                                           OR (tenant_id = fixture.tenant_b
                                               AND user_id <> fixture.user_b)),
          'wrong_owner_messages', (SELECT count(*) FROM agent_messages, fixture
                                   WHERE (tenant_id = fixture.tenant_a
                                          AND user_id IS DISTINCT FROM fixture.user_a)
                                      OR (tenant_id = fixture.tenant_b
                                          AND user_id IS DISTINCT FROM fixture.user_b)),
          'sync_jobs', (SELECT count(*) FROM async_jobs, fixture
                        WHERE tenant_id = fixture.tenant_a AND kind = 'bumpa.sync'
                          AND idempotency_key LIKE 'bumpa:' || fixture.tenant_a
                                                   || ':pressure-' || :'event_prefix' || '-sync-%'),
          'succeeded_sync_jobs', (SELECT count(*) FROM async_jobs, fixture
                        WHERE tenant_id = fixture.tenant_a AND kind = 'bumpa.sync'
                          AND idempotency_key LIKE 'bumpa:' || fixture.tenant_a
                                                   || ':pressure-' || :'event_prefix' || '-sync-%'
                          AND status = 'succeeded'),
          'sync_job_attempts', (SELECT coalesce(sum(attempts), 0) FROM async_jobs, fixture
                        WHERE tenant_id = fixture.tenant_a AND kind = 'bumpa.sync'
                          AND idempotency_key LIKE 'bumpa:' || fixture.tenant_a
                                                   || ':pressure-' || :'event_prefix' || '-sync-%'),
          'dead_sync_jobs', (SELECT count(*) FROM async_jobs, fixture
                        WHERE tenant_id = fixture.tenant_a AND kind = 'bumpa.sync'
                          AND idempotency_key LIKE 'bumpa:' || fixture.tenant_a
                                                   || ':pressure-' || :'event_prefix' || '-sync-%'
                          AND status IN ('dead_letter', 'cancelled', 'retry', 'running')),
          'pending_sync_outboxes', (SELECT count(*) FROM job_outbox o
                        JOIN async_jobs j ON j.id = o.job_id, fixture
                        WHERE j.tenant_id = fixture.tenant_a AND j.kind = 'bumpa.sync'
                          AND j.idempotency_key LIKE 'bumpa:' || fixture.tenant_a
                                                   || ':pressure-' || :'event_prefix' || '-sync-%'
                          AND o.status = 'pending'),
          'sync_runs', (SELECT count(*) FROM bumpa_sync_runs, fixture
                        WHERE tenant_id = fixture.tenant_a),
          'successful_sync_runs', (SELECT count(*) FROM bumpa_sync_runs, fixture
                        WHERE tenant_id = fixture.tenant_a AND status = 'success'
                          AND completion_quality = 'complete'
                          AND orders_availability = 'available' AND orders_count = 6),
          'metric_snapshots', (SELECT count(*) FROM bumpa_metric_snapshots, fixture
                               WHERE tenant_id = fixture.tenant_a),
          'raw_responses', (SELECT count(*) FROM bumpa_raw_responses, fixture
                            WHERE tenant_id = fixture.tenant_a),
          'canonical_orders', (SELECT count(*) FROM bumpa_orders, fixture
                               WHERE tenant_id = fixture.tenant_a),
          'distinct_canonical_orders', (SELECT count(DISTINCT bumpa_order_id)
                                        FROM bumpa_orders, fixture
                                        WHERE tenant_id = fixture.tenant_a),
          'healthy_connection', (SELECT count(*) FROM bumpa_connections, fixture
                                  WHERE tenant_id = fixture.tenant_a
                                    AND last_successful_sync_at IS NOT NULL
                                    AND last_error IS NULL),
          'tenant_b_sync_artifacts', (
            SELECT
              (SELECT count(*) FROM async_jobs, fixture
               WHERE tenant_id = fixture.tenant_b AND kind = 'bumpa.sync')
              + (SELECT count(*) FROM bumpa_sync_runs, fixture
                 WHERE tenant_id = fixture.tenant_b)
              + (SELECT count(*) FROM bumpa_metric_snapshots, fixture
                 WHERE tenant_id = fixture.tenant_b)
              + (SELECT count(*) FROM bumpa_raw_responses, fixture
                 WHERE tenant_id = fixture.tenant_b)
              + (SELECT count(*) FROM bumpa_orders, fixture
                 WHERE tenant_id = fixture.tenant_b)
          ),
          'tenant_mismatches', (
            SELECT
              (SELECT count(*) FROM agent_messages m
               JOIN conversations c ON c.id = m.conversation_id, fixture
               WHERE m.tenant_id IN (fixture.tenant_a, fixture.tenant_b)
                 AND (m.tenant_id <> c.tenant_id OR m.user_id <> c.user_id))
              + (SELECT count(*) FROM bumpa_metric_snapshots m
                 JOIN bumpa_sync_runs r ON r.id = m.sync_run_id, fixture
                 WHERE m.tenant_id = fixture.tenant_a AND m.tenant_id <> r.tenant_id)
              + (SELECT count(*) FROM bumpa_raw_responses b
                 JOIN bumpa_sync_runs r ON r.id = b.sync_run_id, fixture
                 WHERE b.tenant_id = fixture.tenant_a AND b.tenant_id <> r.tenant_id)
          )
        )::text;
        """,
        event_prefix=run_id,
    )


def run_authenticated_pressure_phase(
    project: ComposeProject,
    base_url: str,
    run_id: str,
    fixtures: dict[str, dict[str, str]],
) -> dict[str, Any]:
    fixture_a = fixtures["a"]
    fixture_b = fixtures["b"]
    auth_a = {"Authorization": f"Bearer {fixture_a['token']}"}
    auth_b = {"Authorization": f"Bearer {fixture_b['token']}"}
    chat_requests = [
        ApiRequest(
            method="POST",
            path="/v1/chat/web",
            headers=auth_a,
            body={
                "message": f"Synthetic pressure question {index}",
                "client_message_id": f"pressure-{run_id}-chat-{index}",
            },
        )
        for index in range(CHAT_ATTEMPTS)
    ]
    sync_requests = [
        ApiRequest(
            method="POST",
            path="/v1/bumpa/sync",
            headers={
                **auth_a,
                "Idempotency-Key": f"pressure-{run_id}-sync-{index}",
            },
            body={"date_from": "2026-07-01", "date_to": "2026-07-07"},
        )
        for index in range(SYNC_ATTEMPTS)
    ]
    requests = [*chat_requests, *sync_requests]
    results, wall_time_ms = concurrent_api_batch(base_url, requests)
    chat_results = results[:CHAT_ATTEMPTS]
    sync_results = results[CHAT_ATTEMPTS:]
    expected_chat_statuses = {"200": CHAT_LIMIT, "429": CHAT_ATTEMPTS - CHAT_LIMIT}
    expected_sync_statuses = {"202": SYNC_LIMIT, "429": SYNC_ATTEMPTS - SYNC_LIMIT}
    if status_counts(chat_results) != expected_chat_statuses:
        raise DrillFailure(
            f"Authenticated chat pressure escaped its exact budget; statuses={status_counts(chat_results)}"
        )
    if status_counts(sync_results) != expected_sync_statuses:
        raise DrillFailure(
            f"Bumpa sync pressure escaped its exact budget; statuses={status_counts(sync_results)}"
        )
    if wall_time_ms > 20_000:
        raise DrillFailure("Authenticated chat and sync pressure exceeded its bounded runtime")

    successful_chat = next(
        (
            (request, result)
            for request, result in zip(chat_requests, chat_results)  # noqa: B905 (Python 3.9 host)
            if result.status == 200 and result.response is not None
        ),
        None,
    )
    successful_sync = next(
        (result for result in sync_results if result.status == 202 and result.response is not None),
        None,
    )
    if successful_chat is None or successful_sync is None:
        raise DrillFailure("Pressure responses omitted required correlation data")
    chat_request, chat_result = successful_chat
    conversation_id = chat_result.response.get("conversation_id")
    job_id = successful_sync.response.get("job_id")
    client_message_id = chat_request.body.get("client_message_id") if chat_request.body else None
    if not all(
        isinstance(value, str) and value for value in (conversation_id, job_id, client_message_id)
    ):
        raise DrillFailure("Pressure responses returned invalid correlation data")

    replayed_chat = request_api(base_url, chat_request)
    if replayed_chat.status != 200 or replayed_chat.response != chat_result.response:
        raise DrillFailure("The accepted chat did not replay idempotently after budget saturation")

    tenant_b_chat = request_api(
        base_url,
        ApiRequest(
            method="POST",
            path="/v1/chat/web",
            headers=auth_b,
            body={
                "message": "Synthetic tenant boundary check",
                "client_message_id": client_message_id,
            },
        ),
    )
    override_denied = request_api(
        base_url,
        ApiRequest(
            method="GET",
            path="/v1/chat/conversations",
            headers={**auth_a, "X-Tenant-ID": fixture_b["tenant_id"]},
        ),
    )
    conversation_hidden = request_api(
        base_url,
        ApiRequest(
            method="GET",
            path=f"/v1/chat/conversations/{conversation_id}",
            headers=auth_b,
        ),
    )
    sync_job_hidden = request_api(
        base_url,
        ApiRequest(
            method="GET",
            path=f"/v1/bumpa/sync-jobs/{job_id}",
            headers=auth_b,
        ),
    )
    isolation_statuses = {
        "same_external_id_other_tenant": tenant_b_chat.status,
        "cross_tenant_override": override_denied.status,
        "cross_tenant_conversation": conversation_hidden.status,
        "cross_tenant_sync_job": sync_job_hidden.status,
    }
    if isolation_statuses != {
        "same_external_id_other_tenant": 200,
        "cross_tenant_override": 403,
        "cross_tenant_conversation": 404,
        "cross_tenant_sync_job": 404,
    }:
        raise DrillFailure(f"Authenticated tenant isolation failed; statuses={isolation_statuses}")

    expected_state = {
        "fixture_tenants": 2,
        "fixture_users": 2,
        "active_owner_memberships": 2,
        "auth_sessions": 2,
        "tenant_a_conversations": CHAT_LIMIT,
        "tenant_a_messages": CHAT_LIMIT * 2,
        "tenant_a_inbound": CHAT_LIMIT,
        "tenant_a_outbound": CHAT_LIMIT,
        "tenant_a_external_ids": CHAT_LIMIT,
        "tenant_b_conversations": 1,
        "tenant_b_messages": 2,
        "tenant_b_inbound": 1,
        "tenant_b_outbound": 1,
        "tenant_b_external_ids": 1,
        "wrong_owner_conversations": 0,
        "wrong_owner_messages": 0,
        "sync_jobs": SYNC_LIMIT,
        "succeeded_sync_jobs": SYNC_LIMIT,
        "sync_job_attempts": SYNC_LIMIT,
        "dead_sync_jobs": 0,
        "pending_sync_outboxes": 0,
        "sync_runs": SYNC_LIMIT,
        "successful_sync_runs": SYNC_LIMIT,
        "metric_snapshots": SYNC_LIMIT * 10,
        "raw_responses": SYNC_LIMIT * 16,
        "canonical_orders": 6,
        "distinct_canonical_orders": 6,
        "healthy_connection": 1,
        "tenant_b_sync_artifacts": 0,
        "tenant_mismatches": 0,
    }
    settled = wait_until(
        "authenticated chat and Bumpa sync pressure to settle without corruption",
        lambda: (
            state
            if exact_state(expected_state)(state := pressure_state(project, run_id))
            else False
        ),
        timeout=60,
    )
    return {
        "concurrent_requests": len(requests),
        "wall_time_ms": round(wall_time_ms, 3),
        "latency_max_ms": round(max(item.latency_ms for item in results), 3),
        "chat": {
            "attempts": CHAT_ATTEMPTS,
            "limit": CHAT_LIMIT,
            "http_statuses": status_counts(chat_results),
        },
        "bumpa_sync": {
            "attempts": SYNC_ATTEMPTS,
            "limit": SYNC_LIMIT,
            "http_statuses": status_counts(sync_results),
        },
        "tenant_isolation_http_statuses": isolation_statuses,
        "chat_replay": {
            "http_status": replayed_chat.status,
            "same_response": True,
            "durable_counts_unchanged": True,
        },
        "durable_state": settled,
    }


def run_load_phase(project: ComposeProject, base_url: str, run_id: str) -> dict[str, Any]:
    event_prefix = f"lf-{run_id}-load-"
    bodies = [payload(f"{event_prefix}{index:03d}", index) for index in range(EVENT_COUNT)]
    first_results, first_wall = concurrent_batch(base_url, bodies)
    first_metrics = metrics(first_results, first_wall)
    if first_metrics.successful != EVENT_COUNT or first_metrics.duplicates != 0:
        statuses = {
            status: sum(item.status == status for item in first_results)
            for status in sorted({item.status for item in first_results})
        }
        raise DrillFailure(
            f"The unique 50-event batch was not accepted exactly once; HTTP statuses={statuses}"
        )

    expected = {
        "events": EVENT_COUNT,
        "processed_events": EVENT_COUNT,
        "jobs": EVENT_COUNT,
        "succeeded_jobs": EVENT_COUNT,
        "job_attempts": EVENT_COUNT,
        "dead_letter_jobs": 0,
        "failed_events": 0,
    }
    settled = wait_until(
        "all 50 durable webhook jobs to complete",
        lambda: (
            state if exact_state(expected)(state := event_state(project, event_prefix)) else False
        ),
        timeout=60,
    )

    duplicate_results, duplicate_wall = concurrent_batch(base_url, bodies)
    duplicate_metrics = metrics(duplicate_results, duplicate_wall)
    if duplicate_metrics.successful != EVENT_COUNT or duplicate_metrics.duplicates != EVENT_COUNT:
        raise DrillFailure("The duplicate 50-event batch did not deduplicate cleanly")
    unchanged = event_state(project, event_prefix)
    if not exact_state(expected)(unchanged):
        raise DrillFailure("Duplicate delivery changed durable event or job counts")
    return {
        "event_count": EVENT_COUNT,
        "first_delivery": asdict(first_metrics),
        "duplicate_delivery": asdict(duplicate_metrics),
        "durable_state": settled,
    }


def assert_accepted(result: HttpResult, description: str) -> None:
    if result.status != 200 or result.response is None:
        raise DrillFailure(f"{description} was not accepted")
    if result.response.get("status") != "accepted" or result.response.get("queued") is not True:
        raise DrillFailure(f"{description} did not enter the durable async path")


def run_redis_phase(project: ComposeProject, base_url: str, run_id: str) -> dict[str, Any]:
    event_prefix = f"lf-{run_id}-redis-"
    body = payload(f"{event_prefix}001", 100_001)
    project.run("stop", "worker", "scheduler", "redis")
    unavailable_readiness = wait_until(
        "API readiness to expose the Redis outage",
        lambda: (
            status
            if (
                status := get_status(
                    f"{base_url}/health/ready",
                    timeout=OUTAGE_READINESS_HTTP_TIMEOUT_SECONDS,
                )
            )
            == 503
            else False
        ),
        timeout=20,
    )
    accepted = post(base_url, body)
    assert_accepted(accepted, "Webhook submitted during Redis outage")
    before_restart = event_state(project, event_prefix)
    if not exact_state(
        {"events": 1, "processed_events": 0, "jobs": 1, "succeeded_jobs": 0, "job_attempts": 0}
    )(before_restart):
        raise DrillFailure("Redis outage did not leave one authoritative pending database job")

    project.run("start", "redis", "worker", "scheduler")
    wait_for_readiness(base_url, timeout=45)
    expected = {
        "events": 1,
        "processed_events": 1,
        "jobs": 1,
        "succeeded_jobs": 1,
        "job_attempts": 1,
        "dead_letter_jobs": 0,
        "failed_events": 0,
    }
    recovered = wait_until(
        "Redis restart job recovery",
        lambda: (
            state if exact_state(expected)(state := event_state(project, event_prefix)) else False
        ),
        timeout=45,
    )
    return {
        "readiness_during_outage": unavailable_readiness,
        "accepted_http_status": accepted.status,
        "before_restart": before_restart,
        "after_restart": recovered,
    }


def run_postgres_phase(project: ComposeProject, base_url: str, run_id: str) -> dict[str, Any]:
    event_prefix = f"lf-{run_id}-postgres-"
    durable_body = payload(f"{event_prefix}durable", 200_001)
    retry_body = payload(f"{event_prefix}retry", 200_002)
    project.run("stop", "worker", "scheduler")
    durable_accept = post(base_url, durable_body)
    assert_accepted(durable_accept, "Pre-restart PostgreSQL webhook")
    before_restart = event_state(project, event_prefix)
    if not exact_state({"events": 1, "jobs": 1, "job_attempts": 0})(before_restart):
        raise DrillFailure("Pre-restart PostgreSQL state was not durably committed")

    project.run("stop", "postgres")
    failed_closed = wait_until(
        "webhook ingress to fail closed while PostgreSQL is down",
        lambda: result if (result := post(base_url, retry_body)).status >= 500 else False,
        timeout=20,
    )
    if not isinstance(failed_closed, HttpResult):
        raise DrillFailure("PostgreSQL outage did not return a bounded failure")
    project.run("start", "postgres")
    wait_until(
        "PostgreSQL to accept queries after restart",
        lambda: event_state(project, event_prefix),
        timeout=45,
    )
    recovered_accept = wait_until(
        "same webhook retry to be accepted after PostgreSQL restart",
        lambda: result if (result := post(base_url, retry_body)).status == 200 else False,
        timeout=30,
    )
    if not isinstance(recovered_accept, HttpResult):
        raise DrillFailure("PostgreSQL recovery did not accept the safe retry")
    assert_accepted(recovered_accept, "Post-restart PostgreSQL webhook retry")
    project.run("start", "worker", "scheduler")
    wait_for_readiness(base_url, timeout=45)
    expected = {
        "events": 2,
        "processed_events": 2,
        "jobs": 2,
        "succeeded_jobs": 2,
        "job_attempts": 2,
        "dead_letter_jobs": 0,
        "failed_events": 0,
    }
    recovered = wait_until(
        "PostgreSQL restart exact-once recovery",
        lambda: (
            state if exact_state(expected)(state := event_state(project, event_prefix)) else False
        ),
        timeout=45,
    )
    return {
        "accepted_before_restart": durable_accept.status,
        "outage_http_status": failed_closed.status,
        "accepted_after_restart": recovered_accept.status,
        "before_restart": before_restart,
        "after_restart": recovered,
    }


def load_script_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DrillFailure("A production operations script could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value))
    return set()


def run_disk_near_full_phase() -> dict[str, Any]:
    check_disk = load_script_module(f"bumpabestie_disk_drill_{uuid.uuid4().hex}", DISK_CHECK_SCRIPT)
    fixed_time = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)  # noqa: UP017

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return fixed_time if tz is None else fixed_time.astimezone(tz)

    with tempfile.TemporaryDirectory(prefix="bumpabestie-disk-drill-") as temporary:
        temp_root = Path(temporary)
        secret_path = temp_root / "alert-secret"
        config_path = temp_root / "alerts.json"
        capture_path = temp_root / "captured-request.json"
        hook_path = temp_root / "capture-alert-hook"
        synthetic_secret = "synthetic-disk-alert-secret-material-000000000000"
        secret_path.write_text(synthetic_secret + "\n", encoding="utf-8")
        secret_path.chmod(0o600)
        config_path.write_text(
            json.dumps(
                {
                    "webhook_url": "https://alerts.invalid/events",
                    "hmac_secret_file": str(secret_path),
                    "max_attempts": 1,
                    "timeout_seconds": 1,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        hook_source = f"""#!/usr/bin/env python3
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("bumpabestie_send_ops_alert_drill", {str(ALERT_SCRIPT)!r})
if spec is None or spec.loader is None:
    raise SystemExit(2)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

def capture(request, *, attempts, timeout_seconds, opener=None):
    document = {{
        "attempts": attempts,
        "body": request.data.decode("utf-8"),
        "endpoint": request.full_url,
        "headers": dict(request.header_items()),
        "method": request.get_method(),
        "network_attempts": 0,
        "timeout_seconds": timeout_seconds,
    }}
    Path({str(capture_path)!r}).write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return True

module.send_with_retries = capture
raise SystemExit(module.main())
"""
        hook_path.write_text(hook_source, encoding="utf-8")
        hook_path.chmod(0o700)

        fake_filesystem = SimpleNamespace(
            f_blocks=1000,
            f_bfree=30,
            f_bavail=30,
            f_frsize=1,
            f_bsize=1,
            f_files=100,
            f_ffree=50,
            f_favail=50,
        )
        output = io.StringIO()
        with (
            patch.object(check_disk.os, "stat", return_value=SimpleNamespace(st_dev=7)),
            patch.object(check_disk.os, "statvfs", return_value=fake_filesystem),
            patch.object(check_disk.socket, "gethostname", return_value="synthetic-private-host"),
            patch.object(check_disk, "datetime", FixedDateTime),
            patch.dict(
                os.environ,
                {"BUMPABESTIE_ALERT_CONFIG_FILE": str(config_path)},
                clear=False,
            ),
            contextlib.redirect_stdout(output),
        ):
            for variable in (
                "BUMPABESTIE_ALERT_HMAC_SECRET_FILE",
                "BUMPABESTIE_ALERT_MAX_ATTEMPTS",
                "BUMPABESTIE_ALERT_TIMEOUT_SECONDS",
                "BUMPABESTIE_ALERT_WEBHOOK_URL",
            ):
                os.environ.pop(variable, None)
            source_exit_code = check_disk.main(
                [
                    "--path",
                    "/synthetic-near-full",
                    "--threshold-percent",
                    "85",
                    "--alert-hook",
                    str(hook_path),
                ]
            )

        lines = [line for line in output.getvalue().splitlines() if line]
        if source_exit_code != 1 or len(lines) != 1 or not capture_path.is_file():
            raise DrillFailure("The synthetic near-full disk event did not traverse its alert hook")
        try:
            source_event = json.loads(lines[0])
            captured = json.loads(capture_path.read_text(encoding="utf-8"))
            envelope = json.loads(captured["body"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DrillFailure("The disk alert drill produced invalid evidence") from exc
        if not all(isinstance(item, dict) for item in (source_event, captured, envelope)):
            raise DrillFailure("The disk alert drill produced an invalid evidence schema")
        filesystems = source_event.get("filesystems")
        filesystem = filesystems[0] if isinstance(filesystems, list) and filesystems else None
        blocks = filesystem.get("blocks") if isinstance(filesystem, dict) else None
        inodes = filesystem.get("inodes") if isinstance(filesystem, dict) else None
        attributes = envelope.get("attributes")
        if (
            not isinstance(blocks, dict)
            or not isinstance(inodes, dict)
            or not isinstance(attributes, dict)
        ):
            raise DrillFailure("The disk alert drill omitted resource evidence")
        if (
            source_event.get("status") != "alert"
            or blocks.get("used_percent") != 97
            or inodes.get("used_percent") != 50
            or envelope.get("event_type") != "disk_capacity_failure"
            or envelope.get("severity") != "critical"
            or attributes
            != {
                "block_used_percent": 97,
                "checked_filesystems": 1,
                "error_count": 0,
                "inode_used_percent": 50,
                "status": "alert",
                "threshold_percent": 85,
            }
        ):
            raise DrillFailure("The near-full disk event was not classified exactly")

        headers = {
            str(key).lower(): str(value) for key, value in captured.get("headers", {}).items()
        }
        body = captured["body"].encode("utf-8")
        occurred_at = str(envelope.get("occurred_at"))
        expected_signature = (
            "v1="
            + hmac.new(
                synthetic_secret.encode(),
                occurred_at.encode("ascii") + b"." + body,
                hashlib.sha256,
            ).hexdigest()
        )
        signature_verified = hmac.compare_digest(
            headers.get("x-bumpabestie-signature", ""), expected_signature
        )
        canonical_event = json.dumps(
            {
                "attributes": attributes,
                "event_type": envelope.get("event_type"),
                "occurred_at": envelope.get("occurred_at"),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        expected_event_id = hashlib.sha256(canonical_event.encode()).hexdigest()
        idempotency_verified = (
            envelope.get("event_id") == expected_event_id
            and headers.get("idempotency-key") == expected_event_id
        )
        timestamp_verified = headers.get("x-bumpabestie-timestamp") == occurred_at
        forbidden_keys = {
            "aliases",
            "device_id",
            "errors",
            "filesystems",
            "host",
            "near_full_device_ids",
        }
        serialized_envelope = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
        sanitization_verified = (
            not forbidden_keys.intersection(nested_keys(envelope))
            and "/synthetic-near-full" not in serialized_envelope
            and "synthetic-private-host" not in serialized_envelope
        )
        if not (
            captured.get("endpoint") == "https://alerts.invalid/events"
            and captured.get("method") == "POST"
            and captured.get("attempts") == 1
            and captured.get("timeout_seconds") == 1.0
            and captured.get("network_attempts") == 0
            and headers.get("content-type") == "application/json"
            and signature_verified
            and idempotency_verified
            and timestamp_verified
            and sanitization_verified
        ):
            raise DrillFailure("The disk alert sanitizer or signing boundary failed")
        return {
            "source_exit_code": source_exit_code,
            "status": "alert",
            "threshold_percent": 85,
            "block_used_percent": 97,
            "inode_used_percent": 50,
            "event_type": "disk_capacity_failure",
            "severity": "critical",
            "signature_verified": True,
            "idempotency_verified": True,
            "timestamp_verified": True,
            "sanitization_verified": True,
            "network_attempts": 0,
        }


def wait_for_readiness(base_url: str, *, timeout: float) -> None:
    wait_until(
        "API, PostgreSQL, Redis, worker, and scheduler readiness",
        lambda: get_status(f"{base_url}/health/ready") == 200,
        timeout=timeout,
    )


def bootstrap(project: ComposeProject, base_url: str, *, skip_build: bool) -> None:
    rendered = project.run("--profile", "tools", "config", "--format", "json", capture=True)
    try:
        validate_rendered_compose(json.loads(rendered.stdout))
    except json.JSONDecodeError as exc:
        raise DrillFailure("Compose returned invalid isolated-stack configuration") from exc
    project.run("down", "--volumes", "--remove-orphans", check=False)
    project.run("up", "-d", *("--build",) if not skip_build else (), "postgres", "redis")
    project.run("--profile", "tools", "run", "--rm", "migrate")
    project.run(
        "up",
        "-d",
        *("--build",) if not skip_build else (),
        "api",
        "worker",
        "scheduler",
    )
    wait_for_readiness(base_url, timeout=90)
    runtime = project.run(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        (
            "import json,os;from urllib.parse import urlsplit;"
            f"empty_keys={tuple(sorted(EMPTY_PROVIDER_VALUES))!r};"
            "print(json.dumps({"
            "'APP_ENV':os.environ.get('APP_ENV'),"
            "'WHATSAPP_BACKEND':os.environ.get('WHATSAPP_BACKEND'),"
            "'AGENT_BACKEND':os.environ.get('AGENT_BACKEND'),"
            "'BUMPA_BACKEND':os.environ.get('BUMPA_BACKEND'),"
            "'fixture_mode':os.environ.get('LOAD_FAILURE_FIXTURE_MODE'),"
            "'database_host':urlsplit(os.environ.get('DATABASE_URL','')).hostname,"
            "'redis_host':urlsplit(os.environ.get('REDIS_URL','')).hostname,"
            "'provider_values_empty':not any(os.environ.get(k) for k in empty_keys),"
            "'secret_files_empty':not any(v for k,v in os.environ.items() "
            "if k.endswith('_FILE')),"
            "'fixture_secret_exact':os.environ.get('META_APP_SECRET')=="
            f"{FIXTURE_SECRET.decode()!r}"
            "}))"
        ),
        capture=True,
    )
    expected = {
        "APP_ENV": "staging",
        "WHATSAPP_BACKEND": "mock",
        "AGENT_BACKEND": "mock",
        "BUMPA_BACKEND": "mock",
        "fixture_mode": "true",
        "database_host": "postgres",
        "redis_host": "redis",
        "provider_values_empty": True,
        "secret_files_empty": True,
        "fixture_secret_exact": True,
    }
    if json.loads(runtime.stdout) != expected:
        raise DrillFailure("Refusing to run outside the isolated synthetic-provider stack")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--keep", action="store_true", help="Keep the isolated stack after success")
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional JSON report path; parent directory must already exist",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1024 <= args.port <= 65535:
        raise DrillFailure("Port must be between 1024 and 65535")
    if args.report and not args.report.parent.is_dir():
        raise DrillFailure("Report parent directory does not exist")
    project = ComposeProject(port=args.port)
    base_url = f"http://127.0.0.1:{args.port}"
    run_id = uuid.uuid4().hex[:12]
    started = time.time()
    succeeded = False
    try:
        bootstrap(project, base_url, skip_build=args.skip_build)
        fixtures = seed_pressure_fixtures(project, run_id)
        report = {
            "schema_version": 2,
            "run_id": run_id,
            "synthetic_providers_only": True,
            "authenticated_chat_sync": run_authenticated_pressure_phase(
                project, base_url, run_id, fixtures
            ),
            "disk_near_full_alert": run_disk_near_full_phase(),
            "load": run_load_phase(project, base_url, run_id),
            "redis_restart": run_redis_phase(project, base_url, run_id),
            "postgres_restart": run_postgres_phase(project, base_url, run_id),
            "duration_seconds": round(time.time() - started, 3),
            "result": "pass",
        }
        serialized = json.dumps(report, indent=2, sort_keys=True)
        if args.report:
            args.report.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        succeeded = True
        return 0
    finally:
        if not (args.keep and succeeded):
            if not succeeded:
                diagnostics = project.run(
                    "logs",
                    "--no-color",
                    "--tail=80",
                    "api",
                    "worker",
                    "scheduler",
                    capture=True,
                    check=False,
                )
                if diagnostics.stdout:
                    print(diagnostics.stdout, file=sys.stderr)
                if diagnostics.stderr:
                    print(diagnostics.stderr, file=sys.stderr)
            project.run("down", "--volumes", "--remove-orphans", check=False)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DrillFailure, subprocess.CalledProcessError) as exc:
        print(f"FAIL load/failure drill: {exc}", file=sys.stderr)
        sys.exit(1)
