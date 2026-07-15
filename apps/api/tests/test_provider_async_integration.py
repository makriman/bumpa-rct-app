from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.db.models import (
    AsyncJob,
    BumpaConnection,
    BumpaSyncRun,
    JobOutbox,
    Tenant,
    WebhookEvent,
)
from app.db.session import SessionLocal
from app.jobs import handlers
from app.jobs.handlers import bumpa_sync_handler, whatsapp_webhook_handler
from app.jobs.runtime import AsyncRuntimeConfig, PermanentJobError, enqueue_job
from app.jobs.worker import process_one
from app.main import app
from app.providers.bumpa import BumpaClient, BumpaProviderError
from app.routes import whatsapp as whatsapp_route
from app.routes.bumpa import _today_for_store
from app.services import bumpa as bumpa_service
from app.services import whatsapp_webhook_ingress
from app.services.whatsapp_webhook_ingress import ClaimedWebhookEvent
from tests.conftest import auth_headers


def _nonlocal_settings() -> Settings:
    base = get_settings()
    return Settings(
        app_env="staging",
        database_url=base.database_url,
        artifact_root=base.artifact_root,
        jwt_secret=base.jwt_secret,
        otp_secret=base.otp_secret,
        field_encryption_key=base.field_encryption_key,
        expose_local_otp=True,
        seed_demo_data=True,
        whatsapp_backend="meta",
        bumpa_backend="mock",
        agent_backend="mock",
        meta_app_secret="s" * 32,
        meta_phone_number_id="3234567890",
        meta_system_user_access_token="t" * 40,
    )


def _webhook_body(message_id: str) -> bytes:
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": "2348111111111",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()


def test_nonlocal_whatsapp_ack_is_transactionally_queued_and_handler_processes(
    client: TestClient, monkeypatch
) -> None:
    settings = _nonlocal_settings()
    body = _webhook_body("wamid.async-contract-1")
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    security_contexts: list[tuple[str | None, bool]] = []
    set_security_context = whatsapp_webhook_ingress.set_security_context

    def record_security_context(
        session, *, tenant_id: str | None = None, privileged: bool = False
    ) -> None:
        security_contexts.append((tenant_id, privileged))
        set_security_context(session, tenant_id=tenant_id, privileged=privileged)

    monkeypatch.setattr(whatsapp_webhook_ingress, "set_security_context", record_security_context)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        accepted = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
        duplicate = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert accepted.status_code == 200
    assert accepted.json() == {"status": "accepted", "queued": True, "duplicate": False}
    assert duplicate.json() == {"status": "accepted", "queued": True, "duplicate": True}
    assert security_contexts == [(None, True), (None, True)]

    with SessionLocal() as session:
        event = session.scalar(
            select(WebhookEvent).where(WebhookEvent.external_event_id == "wamid.async-contract-1")
        )
        assert event is not None
        jobs = list(
            session.scalars(
                select(AsyncJob).where(
                    AsyncJob.kind == "whatsapp.process_webhook",
                    AsyncJob.idempotency_key == "meta:wamid.async-contract-1",
                )
            ).all()
        )
        assert len(jobs) == 1
        assert session.scalar(select(JobOutbox).where(JobOutbox.job_id == jobs[0].id)) is not None
        assert whatsapp_webhook_handler(session, jobs[0]) == {"status": "rejected_unknown_sender"}
        session.refresh(event)
        assert event.processing_status == "processed"


def test_nonlocal_whatsapp_batch_rolls_back_before_ack_when_enqueue_fails(
    client: TestClient, monkeypatch
) -> None:
    settings = _nonlocal_settings()
    message_ids = ["wamid.atomic-batch-1", "wamid.atomic-batch-2"]
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": "2348111111111",
                                    "type": "text",
                                    "text": {"body": "Hello"},
                                }
                                for message_id in message_ids
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    enqueue = whatsapp_webhook_ingress.enqueue_job
    enqueue_calls = 0

    def fail_second_enqueue(*args, **kwargs):
        nonlocal enqueue_calls
        enqueue_calls += 1
        if enqueue_calls == 2:
            raise RuntimeError("synthetic enqueue failure")
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(whatsapp_webhook_ingress, "enqueue_job", fail_second_enqueue)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with pytest.raises(RuntimeError, match="synthetic enqueue failure"):
            client.post(
                "/webhooks/whatsapp",
                content=body,
                headers={"x-hub-signature-256": signature},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    with SessionLocal() as session:
        events = list(
            session.scalars(
                select(WebhookEvent).where(WebhookEvent.external_event_id.in_(message_ids))
            ).all()
        )
        jobs = list(
            session.scalars(
                select(AsyncJob).where(
                    AsyncJob.idempotency_key.in_(
                        [f"meta:{message_id}" for message_id in message_ids]
                    )
                )
            ).all()
        )
    assert events == []
    assert jobs == []


def test_slow_whatsapp_persistence_does_not_block_liveness(monkeypatch) -> None:
    """A slow synchronous database claim must not stall the ASGI event loop."""

    settings = _nonlocal_settings()
    body = _webhook_body("wamid.slow-persistence-contract")
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    persistence_started = threading.Event()
    release_persistence = threading.Event()

    def slow_claim(_payload: dict[str, object], _raw: bytes) -> tuple[ClaimedWebhookEvent, ...]:
        persistence_started.set()
        if not release_persistence.wait(timeout=5):
            raise TimeoutError("test did not release synthetic persistence")
        return (
            ClaimedWebhookEvent(
                event_id="slow-persistence-contract",
                job_created=True,
                terminal_duplicate=False,
            ),
        )

    monkeypatch.setattr(whatsapp_route, "claim_webhook_events", slow_claim)
    app.dependency_overrides[get_settings] = lambda: settings

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            webhook_request = asyncio.create_task(
                async_client.post(
                    "/webhooks/whatsapp",
                    content=body,
                    headers={"x-hub-signature-256": signature},
                )
            )
            try:
                assert await asyncio.to_thread(persistence_started.wait, 1)
                live = await asyncio.wait_for(async_client.get("/health/live"), timeout=1)
                assert live.status_code == 200
                assert live.json() == {"status": "ok", "service": "api"}
            finally:
                release_persistence.set()
            accepted = await asyncio.wait_for(webhook_request, timeout=2)
            assert accepted.status_code == 200
            assert accepted.json() == {
                "status": "accepted",
                "queued": True,
                "duplicate": False,
            }

    try:
        asyncio.run(exercise())
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_oversized_whatsapp_payload_is_rejected_before_persistence(
    client: TestClient, monkeypatch
) -> None:
    settings = _nonlocal_settings()
    persistence_called = False

    def unexpected_persistence(
        _payload: dict[str, object], _raw: bytes
    ) -> tuple[ClaimedWebhookEvent, ...]:
        nonlocal persistence_called
        persistence_called = True
        return ()

    monkeypatch.setattr(whatsapp_route, "claim_webhook_events", unexpected_persistence)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = client.post(
            "/webhooks/whatsapp",
            content=b"x" * (whatsapp_route.MAX_WEBHOOK_BODY_BYTES + 1),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 413
    assert response.json() == {"detail": "Webhook payload too large"}
    assert persistence_called is False


def test_nonlocal_whatsapp_persists_every_batched_message_and_status_before_ack(
    client: TestClient,
) -> None:
    settings = _nonlocal_settings()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "3234567890"},
                            "messages": [
                                {
                                    "id": "wamid.batch-message-1",
                                    "from": "2348111111111",
                                    "type": "text",
                                    "text": {"body": "First"},
                                },
                                {
                                    "id": "wamid.batch-message-2",
                                    "from": "2348222222222",
                                    "type": "text",
                                    "text": {"body": "Second"},
                                },
                            ],
                            "statuses": [
                                {
                                    "id": "wamid.batch-outbound-1",
                                    "status": "delivered",
                                    "timestamp": "100",
                                }
                            ],
                        },
                    },
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.batch-message-3",
                                    "from": "2348333333333",
                                    "type": "text",
                                    "text": {"body": "Third"},
                                }
                            ]
                        },
                    },
                ],
            },
            {
                "id": "waba-2",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.batch-outbound-2",
                                    "status": "sent",
                                    "timestamp": "101",
                                },
                                {
                                    "id": "wamid.batch-outbound-3",
                                    "status": "failed",
                                    "timestamp": "102",
                                },
                            ]
                        },
                    }
                ],
            },
        ],
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        accepted = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
        duplicate = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert accepted.status_code == 200
    assert accepted.json() == {
        "status": "accepted",
        "queued": True,
        "events": 6,
        "duplicates": 0,
    }
    assert duplicate.json() == {
        "status": "accepted",
        "queued": True,
        "events": 6,
        "duplicates": 6,
    }

    external_ids = {
        "wamid.batch-message-1",
        "wamid.batch-message-2",
        "wamid.batch-message-3",
        "wamid.batch-outbound-1:delivered:100",
        "wamid.batch-outbound-2:sent:101",
        "wamid.batch-outbound-3:failed:102",
    }
    with SessionLocal() as session:
        events = list(
            session.scalars(
                select(WebhookEvent).where(WebhookEvent.external_event_id.in_(external_ids))
            ).all()
        )
        assert {event.external_event_id for event in events} == external_ids
        for event in events:
            value = event.payload["entry"][0]["changes"][0]["value"]
            assert len(value.get("messages", [])) + len(value.get("statuses", [])) == 1

        jobs = list(
            session.scalars(
                select(AsyncJob).where(
                    AsyncJob.idempotency_key.in_({f"meta:{item}" for item in external_ids})
                )
            ).all()
        )
        assert len(jobs) == 6
        outboxes = list(
            session.scalars(
                select(JobOutbox).where(JobOutbox.job_id.in_({job.id for job in jobs}))
            ).all()
        )
        assert len(outboxes) == 6


def test_nonlocal_bumpa_sync_is_idempotently_queued_and_handler_runs(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    settings = _nonlocal_settings()
    today = date.today()
    payload = {
        "date_from": str(today - timedelta(days=6)),
        "date_to": str(today),
    }
    headers = {**owner, "Idempotency-Key": "async-bumpa-contract-1"}
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        accepted = client.post("/v1/bumpa/sync", headers=headers, json=payload)
        duplicate = client.post("/v1/bumpa/sync", headers=headers, json=payload)
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"
    assert accepted.json()["duplicate"] is False
    assert duplicate.status_code == 202
    assert duplicate.json()["job_id"] == accepted.json()["job_id"]
    assert duplicate.json()["duplicate"] is True
    queued_job = client.get(f"/v1/bumpa/sync-jobs/{accepted.json()['job_id']}", headers=owner)
    assert queued_job.status_code == 200
    assert queued_job.json() == {
        "job_id": accepted.json()["job_id"],
        "status": "pending",
        "requested_from": payload["date_from"],
        "requested_to": payload["date_to"],
        "sync_run_id": None,
        "finished_at": None,
    }

    conflicting_payload = {**payload, "date_from": str(today - timedelta(days=7))}
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        conflict = client.post("/v1/bumpa/sync", headers=headers, json=conflicting_payload)
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert conflict.status_code == 409
    assert "different sync request" in conflict.json()["detail"]

    with SessionLocal() as session:
        job = session.get(AsyncJob, accepted.json()["job_id"])
        assert job is not None
        assert job.tenant_id == tenant_id
        assert job.payload["tenant_id"] == tenant_id
        assert job.payload["boundary_revision"] == 1
        assert session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id)) is not None
        result = bumpa_sync_handler(session, job)
        assert result is not None
        assert result["status"] == "success"
        assert result["requested_from"] == payload["date_from"]
        assert result["requested_to"] == payload["date_to"]
        job.status = "succeeded"
        job.result = result
        session.commit()

    completed_job = client.get(f"/v1/bumpa/sync-jobs/{accepted.json()['job_id']}", headers=owner)
    assert completed_job.status_code == 200
    assert completed_job.json()["status"] == "succeeded"
    assert completed_job.json()["sync_run_id"] == result["sync_run_id"]
    other_owner = auth_headers(client, "+2348012345679")
    assert (
        client.get(
            f"/v1/bumpa/sync-jobs/{accepted.json()['job_id']}", headers=other_owner
        ).status_code
        == 404
    )


def test_store_today_uses_the_connection_timezone_at_utc_day_boundaries() -> None:
    instant = datetime(2026, 7, 14, 23, 30, tzinfo=UTC)
    assert _today_for_store("Africa/Lagos", instant=instant) == date(2026, 7, 15)
    assert _today_for_store("America/New_York", instant=instant) == date(2026, 7, 14)
    with pytest.raises(ValueError, match="timezone"):
        _today_for_store("UTC", instant=datetime(2026, 7, 14, 23, 30))


def test_queued_bumpa_sync_rejects_a_replaced_connection_boundary(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    settings = _nonlocal_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        accepted = client.post(
            "/v1/bumpa/sync",
            headers={**owner, "Idempotency-Key": "stale-boundary-job"},
            json={"date_from": "2026-07-01", "date_to": "2026-07-12"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert accepted.status_code == 202

    with SessionLocal() as session:
        job = session.get(AsyncJob, accepted.json()["job_id"])
        assert job is not None
        connection = session.get(BumpaConnection, job.payload["connection_id"])
        assert connection is not None
        assert job.payload["boundary_revision"] == connection.boundary_revision
        connection.boundary_revision += 1
        session.commit()
        with pytest.raises(PermanentJobError, match="replaced"):
            bumpa_sync_handler(session, job)


def test_provider_handlers_reject_malformed_or_cross_tenant_payloads() -> None:
    with SessionLocal() as session:
        malformed_webhook = AsyncJob(
            kind="whatsapp.process_webhook",
            payload={"event_id": 42},
            idempotency_key="test:malformed-webhook",
        )
        with pytest.raises(PermanentJobError, match="event_id"):
            whatsapp_webhook_handler(session, malformed_webhook)

        malformed_bumpa = AsyncJob(
            kind="bumpa.sync",
            payload={
                "tenant_id": "not-the-connection-tenant",
                "connection_id": "missing",
                "date_from": "2026-02-02",
                "date_to": "2026-01-01",
            },
            idempotency_key="test:malformed-bumpa",
        )
        with pytest.raises(PermanentJobError, match="date range"):
            bumpa_sync_handler(session, malformed_bumpa)


@pytest.mark.parametrize(
    ("status_code", "retryable", "expected_status"),
    [(429, True, "retry"), (503, True, "retry"), (401, False, "dead_letter")],
)
def test_bumpa_worker_retries_exhausted_transient_failures_and_dead_letters_auth(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    retryable: bool,
    expected_status: str,
) -> None:
    field_key = "worker-bumpa-provider-test-field-key"
    private_detail = "private-upstream-body-must-not-be-persisted"

    class FailingBumpaClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FailingBumpaClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def sync(self, _date_from: date, _date_to: date) -> None:
            message = (
                "Bumpa is temporarily unavailable" if retryable else "Bumpa authentication failed"
            )
            raise BumpaProviderError(
                message,
                status_code=status_code,
                retryable=retryable,
            ) from RuntimeError(private_detail)

    monkeypatch.setattr(bumpa_service, "BumpaClient", FailingBumpaClient)
    monkeypatch.setattr(
        handlers,
        "get_settings",
        lambda: SimpleNamespace(field_encryption_key=field_key, bumpa_backend="bumpa"),
    )
    runtime = AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="bumpa-provider-failures",
        queue_key_prefix="test",
        heartbeat_ttl_seconds=45,
        pop_timeout_seconds=1,
        scheduler_interval_seconds=0.01,
        dispatch_batch_size=10,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=10,
        stale_lock_seconds=60,
    )

    with SessionLocal() as session:
        tenant = Tenant(
            slug=f"worker-bumpa-{status_code}",
            name=f"Worker Bumpa {status_code}",
        )
        session.add(tenant)
        session.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=FieldCipher(field_key).encrypt("private-api-key"),
            scope_type="business_id",
            scope_id="business-test",
            provider="bumpa",
            status="active",
        )
        session.add(connection)
        session.flush()
        job, _created = enqueue_job(
            session,
            kind="bumpa.sync",
            payload={
                "tenant_id": tenant.id,
                "connection_id": connection.id,
                "boundary_revision": connection.boundary_revision,
                "date_from": "2026-01-01",
                "date_to": "2026-01-02",
            },
            idempotency_key=f"bumpa-provider-failure:{status_code}",
            queue_name=runtime.queue_name,
            max_attempts=3,
            tenant_id=tenant.id,
        )
        session.commit()

        assert (
            process_one(
                session,
                job_id=job.id,
                worker_id="provider-failure-worker",
                config=runtime,
            )
            == expected_status
        )
        stored = session.get(AsyncJob, job.id)
        assert stored is not None
        assert stored.status == expected_status
        assert stored.attempts == 1
        assert private_detail not in (stored.last_error or "")
        run = session.scalar(
            select(BumpaSyncRun).where(BumpaSyncRun.bumpa_connection_id == connection.id)
        )
        assert run is not None
        assert run.status == "failed"
        assert private_detail not in (run.error or "")


def test_bumpa_timeout_is_bounded_per_attempt_and_terminal_after_job_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    field_key = "worker-bumpa-timeout-test-field-key"
    private_detail = "private-timeout-detail-must-not-be-persisted"
    calls = 0
    sleeps: list[float] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(private_detail, request=request)

    class TimeoutBumpaClient(BumpaClient):
        def __init__(
            self,
            api_key: str,
            scope_type: str,
            scope_id: str,
            *,
            store_timezone: str,
            store_currency: str,
        ) -> None:
            super().__init__(
                api_key,
                scope_type,
                scope_id,
                store_timezone=store_timezone,
                store_currency=store_currency,
                client=httpx.Client(
                    transport=httpx.MockTransport(timeout),
                    base_url="https://api.getbumpa.com/api",
                ),
                sleep=sleeps.append,
                max_attempts=3,
            )

    monkeypatch.setattr(bumpa_service, "BumpaClient", TimeoutBumpaClient)
    monkeypatch.setattr(
        handlers,
        "get_settings",
        lambda: SimpleNamespace(field_encryption_key=field_key, bumpa_backend="bumpa"),
    )
    runtime = AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="bumpa-timeout-failure",
        queue_key_prefix="test",
        heartbeat_ttl_seconds=45,
        pop_timeout_seconds=1,
        scheduler_interval_seconds=0.01,
        dispatch_batch_size=10,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=10,
        stale_lock_seconds=60,
    )

    with SessionLocal() as session:
        tenant = Tenant(slug="worker-bumpa-timeout", name="Worker Bumpa Timeout")
        session.add(tenant)
        session.flush()
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=FieldCipher(field_key).encrypt("private-api-key"),
            scope_type="business_id",
            scope_id="business-test",
            provider="bumpa",
            status="active",
        )
        session.add(connection)
        session.flush()
        job, _created = enqueue_job(
            session,
            kind="bumpa.sync",
            payload={
                "tenant_id": tenant.id,
                "connection_id": connection.id,
                "boundary_revision": connection.boundary_revision,
                "date_from": "2026-01-01",
                "date_to": "2026-01-02",
            },
            idempotency_key="bumpa-provider-timeout",
            queue_name=runtime.queue_name,
            max_attempts=3,
            tenant_id=tenant.id,
        )
        session.commit()

        statuses: list[str] = []
        for attempt in range(3):
            if attempt:
                stored = session.get(AsyncJob, job.id)
                assert stored is not None
                stored.available_at -= timedelta(seconds=runtime.retry_max_seconds + 1)
                session.commit()
            statuses.append(
                process_one(
                    session,
                    job_id=job.id,
                    worker_id="provider-timeout-worker",
                    config=runtime,
                )
            )

        stored = session.get(AsyncJob, job.id)
        assert stored is not None
        assert statuses == ["retry", "retry", "dead_letter"]
        assert stored.status == "dead_letter"
        assert stored.attempts == stored.max_attempts == 3
        # Each job attempt exhausts the first dataset's three-call budget, then
        # probes all nine remaining analytics endpoints and orders once before
        # classifying a provider-wide outage.
        assert calls == 39
        assert sleeps == [1, 2] * 3
        assert private_detail not in (stored.last_error or "")
        assert "private-api-key" not in (stored.last_error or "")
        runs = list(
            session.scalars(
                select(BumpaSyncRun).where(BumpaSyncRun.bumpa_connection_id == connection.id)
            ).all()
        )
        assert len(runs) == 3
        assert all(run.status == "failed" for run in runs)
        assert all(run.error == "Bumpa is temporarily unreachable" for run in runs)
        assert all(private_detail not in (run.error or "") for run in runs)
