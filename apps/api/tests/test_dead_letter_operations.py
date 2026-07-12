from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import AsyncJob, AuditLog, JobOutbox, SystemError
from app.db.session import SessionLocal, set_security_context
from app.jobs.runtime import AsyncRuntimeConfig, claim_job, enqueue_job, fail_job
from app.routes import admin
from tests.conftest import auth_headers


def _config() -> AsyncRuntimeConfig:
    return AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="default",
        queue_key_prefix="test",
        heartbeat_ttl_seconds=30,
        pop_timeout_seconds=1,
        scheduler_interval_seconds=1,
        dispatch_batch_size=10,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=10,
        stale_lock_seconds=60,
    )


def _dead_letter_job(tenant_id: str, suffix: str) -> str:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        job, created = enqueue_job(
            db,
            kind="bumpa.sync",
            payload={
                "access_token": f"secret-{suffix}",
                "customer_phone": "+2348000000000",
            },
            idempotency_key=f"dead-letter-operator-{suffix}",
            tenant_id=tenant_id,
            max_attempts=1,
        )
        assert created
        db.commit()
        assert claim_job(db, job.id, "test-worker")
        assert (
            fail_job(
                db,
                job.id,
                RuntimeError(f"raw-secret-{suffix}"),
                _config(),
                worker_id="test-worker",
            )
            == "dead_letter"
        )
        return job.id


def test_terminal_failure_is_scrubbed_and_operator_job_view_never_exposes_payloads(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    owner = auth_headers(client, "+2348012345678")
    other_owner = auth_headers(client, "+2348012345679")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    other_tenant_id = client.get("/v1/tenants/current", headers=other_owner).json()["id"]
    job_id = _dead_letter_job(tenant_id, "redaction")
    other_job_id = _dead_letter_job(other_tenant_id, "isolation")

    assert client.get("/v1/admin/system/jobs", headers=owner).status_code == 403
    assert client.get("/v1/admin/system/jobs", headers=other_owner).status_code == 403

    response = client.get(
        "/v1/admin/system/jobs",
        headers=operator,
        params={"status": "dead_letter", "tenant_id": tenant_id},
    )
    assert response.status_code == 200
    rows = response.json()
    assert {row["id"] for row in rows} == {job_id}
    assert other_job_id not in {row["id"] for row in rows}
    rendered = json.dumps(rows)
    assert "secret-redaction" not in rendered
    assert "+2348000000000" not in rendered
    assert "raw-secret-redaction" not in rendered
    assert all(
        forbidden not in rows[0]
        for forbidden in ("payload", "result", "idempotency_key", "last_error", "locked_by")
    )
    assert rows[0]["failure_category"] == "execution_failure"
    assert rows[0]["replayable"] is True

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        error = next(
            (
                row
                for row in db.scalars(
                    select(SystemError).where(SystemError.service == "async_worker")
                ).all()
                if row.error_metadata.get("job_id") == job_id
            ),
            None,
        )
        assert error is not None
        serialized = json.dumps(
            {
                "message": error.message,
                "stack": error.stack,
                "metadata": error.error_metadata,
            }
        )
        assert error.message == "Asynchronous job reached terminal failure"
        assert error.stack is None
        assert "secret-redaction" not in serialized
        assert "+2348000000000" not in serialized
        assert "raw-secret-redaction" not in serialized
        assert set(error.error_metadata) == {
            "job_id",
            "job_kind",
            "terminal_reason",
            "attempts",
            "max_attempts",
        }


def test_replay_is_reason_gated_authorized_audited_and_idempotent(client: TestClient) -> None:
    operator = auth_headers(client, "+2348099990001")
    owner = auth_headers(client, "+2348012345678")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    job_id = _dead_letter_job(tenant_id, "replay")

    assert (
        client.post(
            f"/v1/admin/system/jobs/{job_id}/replay",
            headers=owner,
            json={"reason": "operator_verified_safe_retry"},
        ).status_code
        == 403
    )
    invalid = client.post(
        f"/v1/admin/system/jobs/{job_id}/replay",
        headers=operator,
        json={"reason": "free form text could contain secret credentials"},
    )
    assert invalid.status_code == 422

    replayed = client.post(
        f"/v1/admin/system/jobs/{job_id}/replay",
        headers=operator,
        json={"reason": "upstream_credentials_rotated", "max_attempts": 3},
    )
    assert replayed.status_code == 200
    assert replayed.json()["status"] == "retry"
    assert replayed.json()["attempts"] == 0
    assert replayed.json()["max_attempts"] == 3
    assert replayed.json()["replayable"] is False

    duplicate = client.post(
        f"/v1/admin/system/jobs/{job_id}/replay",
        headers=operator,
        json={"reason": "upstream_credentials_rotated", "max_attempts": 3},
    )
    assert duplicate.status_code == 409

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        job = db.get(AsyncJob, job_id)
        assert job is not None and job.status == "retry" and job.payload["access_token"]
        outbox = db.scalar(select(JobOutbox).where(JobOutbox.job_id == job_id))
        assert outbox is not None and outbox.status == "pending"
        audits = list(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.action == "async_job.replayed",
                    AuditLog.resource_id == job_id,
                )
            ).all()
        )
        assert len(audits) == 1
        assert audits[0].tenant_id == tenant_id
        assert audits[0].after == {
            "status": "retry",
            "attempts": 0,
            "max_attempts": 3,
            "reason": "upstream_credentials_rotated",
        }
        assert "access_token" not in json.dumps(audits[0].after)


def test_replay_state_rolls_back_if_audit_cannot_be_written(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = auth_headers(client, "+2348099990001")
    owner = auth_headers(client, "+2348012345678")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    job_id = _dead_letter_job(tenant_id, "atomic")

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(admin, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            f"/v1/admin/system/jobs/{job_id}/replay",
            headers=operator,
            json={"reason": "dependency_recovered"},
        )

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        job = db.get(AsyncJob, job_id)
        assert job is not None and job.status == "dead_letter"
        assert job.attempts == 1 and job.finished_at is not None
        assert (
            db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "async_job.replayed",
                    AuditLog.resource_id == job_id,
                )
            )
            == 0
        )
