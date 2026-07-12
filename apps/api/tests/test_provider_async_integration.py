from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, timedelta
from types import SimpleNamespace

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
from app.providers.bumpa import BumpaProviderError
from app.services import bumpa as bumpa_service
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
    client: TestClient,
) -> None:
    settings = _nonlocal_settings()
    body = _webhook_body("wamid.async-contract-1")
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
    assert accepted.json() == {"status": "accepted", "queued": True, "duplicate": False}
    assert duplicate.json() == {"status": "accepted", "queued": True, "duplicate": True}

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
        assert session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id)) is not None
        result = bumpa_sync_handler(session, job)
        assert result is not None
        assert result["status"] == "success"
        assert result["requested_from"] == payload["date_from"]
        assert result["requested_to"] == payload["date_to"]


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
