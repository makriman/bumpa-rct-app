from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app.core.crypto import FieldCipher
from app.db.models import AsyncJob, BumpaConnection, BumpaRawResponse, BumpaSyncRun, Tenant
from app.db.session import SessionLocal
from app.jobs import handlers
from app.jobs.runtime import AsyncRuntimeConfig, enqueue_job
from app.jobs.worker import process_one
from app.providers.bumpa import BumpaClient
from app.services import bumpa as bumpa_service

FIXTURES = Path(__file__).parents[3] / "tests" / "contract" / "fixtures" / "bumpa"
ANALYTICS = json.loads((FIXTURES / "analytics_responses.json").read_text())


def test_isolated_products_timeout_finishes_async_job_as_degraded_partial(
    monkeypatch,
    client,
) -> None:
    del client  # The app lifespan creates the shared integration-test schema.
    field_key = "async-isolated-degraded-field-key"
    private_detail = "private-timeout-detail-must-not-be-persisted"
    calls: dict[str, int] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            calls["orders"] = calls.get("orders", 0) + 1
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "orders": {
                        "data": [],
                        "current_page": 1,
                        "last_page": 1,
                        "per_page": 100,
                        "total": 0,
                    },
                },
            )
        area = request.url.path.rsplit("/", 1)[-1]
        dataset = request.url.params["dataset"]
        key = f"{area}.{dataset}"
        calls[key] = calls.get(key, 0) + 1
        if key == "products.overview":
            raise httpx.ReadTimeout(private_detail, request=request)
        payload = deepcopy(ANALYTICS[key])
        if "range" in payload:
            timezone = ZoneInfo("Africa/Lagos")
            requested_from = date.fromisoformat(request.url.params["from"])
            requested_to = date.fromisoformat(request.url.params["to"])
            period = [
                datetime.combine(requested_from, datetime.min.time(), timezone)
                .astimezone(UTC)
                .isoformat(),
                datetime.combine(requested_to, datetime.max.time(), timezone)
                .astimezone(UTC)
                .isoformat(),
            ]
            payload["range"] = {"from": period[0], "to": period[1]}
            raw_data = payload.get("data")
            if isinstance(raw_data, dict):
                chart = raw_data.get("chart")
                if isinstance(chart, dict) and isinstance(chart.get("current_period"), dict):
                    chart["current_period"]["range"] = period
                    previous = chart.get("previous_period")
                    if isinstance(previous, dict):
                        days = (requested_to - requested_from).days + 1
                        previous_to = requested_from - timedelta(days=1)
                        previous_from = previous_to - timedelta(days=days - 1)
                        previous["range"] = [
                            datetime.combine(previous_from, datetime.min.time(), timezone)
                            .astimezone(UTC)
                            .isoformat(),
                            datetime.combine(previous_to, datetime.max.time(), timezone)
                            .astimezone(UTC)
                            .isoformat(),
                        ]
        return httpx.Response(200, json=payload)

    class IsolatedTimeoutBumpaClient(BumpaClient):
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
                    transport=httpx.MockTransport(respond),
                    base_url="https://api.getbumpa.com/api",
                ),
                sleep=lambda _seconds: None,
                max_attempts=3,
            )

    monkeypatch.setattr(bumpa_service, "BumpaClient", IsolatedTimeoutBumpaClient)
    monkeypatch.setattr(
        handlers,
        "get_settings",
        lambda: SimpleNamespace(field_encryption_key=field_key, bumpa_backend="bumpa"),
    )
    runtime = AsyncRuntimeConfig(
        enabled=True,
        redis_url="redis://unused",
        queue_name="bumpa-isolated-degraded",
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
        tenant = Tenant(slug="async-isolated-degraded", name="Async Isolated Degraded")
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
        job, created = enqueue_job(
            session,
            kind="bumpa.sync",
            payload={
                "tenant_id": tenant.id,
                "connection_id": connection.id,
                "boundary_revision": connection.boundary_revision,
                "date_from": "2026-07-01",
                "date_to": "2026-07-12",
            },
            idempotency_key="bumpa-isolated-degraded-contract",
            queue_name=runtime.queue_name,
            max_attempts=5,
            tenant_id=tenant.id,
        )
        session.commit()
        assert created is True

        assert (
            process_one(
                session,
                job_id=job.id,
                worker_id="isolated-degraded-worker",
                config=runtime,
            )
            == "succeeded"
        )
        stored_job = session.get(AsyncJob, job.id)
        assert stored_job is not None
        assert stored_job.status == "succeeded"
        assert stored_job.attempts == 1
        assert stored_job.result is not None
        assert stored_job.result["status"] == "partial"
        assert stored_job.result["completion_quality"] == "degraded"
        assert stored_job.result["partial_reason"] == "dataset_error"

        run = session.scalar(
            select(BumpaSyncRun).where(BumpaSyncRun.bumpa_connection_id == connection.id)
        )
        assert run is not None
        assert run.status == "partial"
        assert run.completion_quality == "degraded"
        assert run.partial_reason == "dataset_error"
        assert connection.last_successful_sync_at is None
        assert connection.last_failed_sync_at is not None
        assert run.finished_at is not None
        assert connection.last_failed_sync_at.replace(tzinfo=None) == run.finished_at.replace(
            tzinfo=None
        )
        raw_failure = session.scalar(
            select(BumpaRawResponse).where(
                BumpaRawResponse.sync_run_id == run.id,
                BumpaRawResponse.resource == "products",
                BumpaRawResponse.dataset == "overview",
            )
        )
        assert raw_failure is not None
        assert raw_failure.http_status is None
        assert raw_failure.failure_kind == "timeout"
        assert private_detail not in (raw_failure.error_message or "")
        assert private_detail not in repr(stored_job.result)
        assert calls["products.overview"] == 2
        assert calls["products.products_sold"] == 1
        assert calls["orders"] == 1
