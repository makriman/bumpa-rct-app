#!/usr/bin/env python3
"""Deterministic Compose load and dependency-restart drill.

The isolated Compose project uses synthetic WhatsApp payloads and local provider
adapters only. PostgreSQL is authoritative; Redis is exercised strictly as the
wake-up transport used by the production architecture.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

EVENT_COUNT = 50
FIXTURE_SECRET = b"load-failure-fixture-app-secret"
PROJECT_NAME = "bumpabestie-load-failure"
ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (ROOT / "compose.yaml", Path(__file__).with_name("compose.yaml"))
T = TypeVar("T")


class DrillFailure(RuntimeError):
    """A sanitized assertion failure from the local drill."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    latency_ms: float
    response: dict[str, Any] | None


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
            **os.environ,
            "COMPOSE_PROJECT_NAME": PROJECT_NAME,
            "LOAD_FAILURE_PORT": str(port),
        }
        self.command = ["docker", "compose", "--profile", "async"]
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
        lambda: status if (status := get_status(f"{base_url}/health/ready")) == 503 else False,
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


def wait_for_readiness(base_url: str, *, timeout: float) -> None:
    wait_until(
        "API, PostgreSQL, Redis, worker, and scheduler readiness",
        lambda: get_status(f"{base_url}/health/ready") == 200,
        timeout=timeout,
    )


def bootstrap(project: ComposeProject, base_url: str, *, skip_build: bool) -> None:
    project.run("config", "--quiet")
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
            "import json,os;print(json.dumps({k:os.environ.get(k) for k in "
            "['APP_ENV','WHATSAPP_BACKEND','AGENT_BACKEND','BUMPA_BACKEND']}))"
        ),
        capture=True,
    )
    expected = {
        "APP_ENV": "staging",
        "WHATSAPP_BACKEND": "mock",
        "AGENT_BACKEND": "mock",
        "BUMPA_BACKEND": "mock",
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
        report = {
            "schema_version": 1,
            "run_id": run_id,
            "synthetic_providers_only": True,
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
