from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AuditLog, PlatformRole, Tenant, TenantOnboarding
from app.db.session import SessionLocal
from app.jobs import handlers as _handlers  # noqa: F401 - register worker handlers
from app.jobs.runtime import claim_job, complete_job, registry
from app.main import app
from tests.conftest import auth_headers


def _tenant_payload(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "name": "Onboarding Audit Test",
        "business_category": "retail",
        "country": "NG",
        "city": "Lagos",
        "timezone": "Africa/Lagos",
        "currency_code": "NGN",
        "research_consent_status": "pending",
    }


def test_local_tenant_creation_audit_has_authoritative_tenant_references(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")

    response = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json=_tenant_payload("onboarding-audit-regression"),
    )

    assert response.status_code == 201, response.text
    tenant_id = response.json()["id"]
    with SessionLocal() as db:
        event = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "tenant.created",
                AuditLog.resource_type == "tenant",
                AuditLog.resource_id == tenant_id,
            )
            .order_by(AuditLog.created_at.desc())
        )
    assert event is not None
    assert event.tenant_id == tenant_id
    assert event.resource_id == tenant_id


def test_production_tenant_creation_requires_resumable_onboarding(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    production_settings = get_settings().model_copy(update={"app_env": "production"})
    app.dependency_overrides[get_settings] = lambda: production_settings
    try:
        response = client.post(
            "/v1/admin/tenants",
            headers=operator,
            json=_tenant_payload("production-bypass-denied"),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "tenant_onboarding_required",
        "message": "Production tenants must be created through the resumable onboarding workflow",
        "retryable": False,
    }


def test_direct_tenant_create_and_update_reject_invalid_iana_timezones(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    invalid_create = _tenant_payload("invalid-direct-timezone")
    invalid_create["timezone"] = "Not/A_Real_Zone"

    created = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json=invalid_create,
    )
    assert created.status_code == 422

    owner = auth_headers(client, "+2348012345678")
    updated = client.patch(
        "/v1/tenants/current",
        headers=owner,
        json={"timezone": "Not/A_Real_Zone"},
    )
    assert updated.status_code == 422


def test_onboarding_routes_are_published_in_the_local_api_schema(
    client: TestClient,
) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert paths["/v1/admin/onboardings"].keys() >= {"get", "post"}
    assert paths["/v1/admin/onboardings/{onboarding_id}"].keys() >= {"get"}
    for command in (
        "owner",
        "phone",
        "bumpa",
        "initial-sync",
        "initial-sync/accept",
        "hermes",
        "complete",
    ):
        assert "post" in paths[f"/v1/admin/onboardings/{{onboarding_id}}/{command}"]


def test_onboarding_revision_header_is_bounded_before_integer_parsing(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    response = client.post(
        "/v1/admin/onboardings/not-created/owner",
        headers={
            **operator,
            "Idempotency-Key": "bounded-revision-regression",
            "If-Match": "9" * 5_000,
        },
        json={
            "name": "Synthetic Owner",
            "phone_e164": "+15550102716",
            "email": None,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "revision_invalid"


def test_onboarding_commands_require_revision_and_idempotency_headers(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")
    body = {
        "name": "Synthetic Owner",
        "phone_e164": "+15550102716",
        "email": None,
    }

    missing_revision = client.post(
        "/v1/admin/onboardings/not-created/owner",
        headers={**operator, "Idempotency-Key": "missing-revision"},
        json=body,
    )
    missing_idempotency = client.post(
        "/v1/admin/onboardings/not-created/owner",
        headers={**operator, "If-Match": "0"},
        json=body,
    )

    assert missing_revision.status_code == 428
    assert missing_revision.json()["detail"]["code"] == "revision_required"
    assert missing_idempotency.status_code == 422
    assert missing_idempotency.json()["detail"]["code"] == "idempotency_key_required"


def test_onboarding_start_rejects_invalid_timezone_and_currency_context(
    client: TestClient,
) -> None:
    operator = auth_headers(client, "+2348099990001")

    response = client.post(
        "/v1/admin/onboardings",
        headers={**operator, "Idempotency-Key": "invalid-timezone-start"},
        json={
            "slug": "invalid-timezone-start",
            "name": "Invalid timezone",
            "business_category": "retail",
            "country": "KE",
            "city": "Nairobi",
            "timezone": "Africa/Not_A_Real_City",
            "currency_code": "KES",
        },
    )

    assert response.status_code == 422
    fields = response.json()["error"]["fields"]
    assert any(
        error.get("location") == ["body", "timezone"]
        and "valid IANA timezone" in error.get("message", "")
        for error in fields
    )
    with SessionLocal() as db:
        assert db.scalar(select(Tenant).where(Tenant.slug == "invalid-timezone-start")) is None

    invalid_currency = client.post(
        "/v1/admin/onboardings",
        headers={**operator, "Idempotency-Key": "invalid-currency-start"},
        json={
            "slug": "invalid-currency-start",
            "name": "Invalid currency",
            "business_category": "retail",
            "country": "KE",
            "city": "Nairobi",
            "timezone": "Africa/Nairobi",
            "currency_code": "K3S",
        },
    )

    assert invalid_currency.status_code == 422
    assert any(
        error.get("location") == ["body", "currency_code"]
        for error in invalid_currency.json()["error"]["fields"]
    )


def test_resumable_onboarding_activates_an_audited_dual_role_demo_tenant(
    client: TestClient,
) -> None:
    operator_phone = "+2348099990001"
    operator = auth_headers(client, operator_phone)
    secret = "local-test-bumpa-key"

    started = client.post(
        "/v1/admin/onboardings",
        headers={**operator, "Idempotency-Key": "saga-e2e-start"},
        json={
            "slug": "resumable-saga-e2e",
            "name": "Resumable Saga E2E",
            "business_category": "retail",
            "country": "NG",
            "city": "Lagos",
            "timezone": "Africa/Lagos",
            "currency_code": "NGN",
        },
    )
    assert started.status_code == 201, started.text
    view = started.json()
    onboarding_id = view["id"]
    tenant_id = view["tenant_id"]
    replayed_start = client.post(
        "/v1/admin/onboardings",
        headers={**operator, "Idempotency-Key": "saga-e2e-start"},
        json={
            "slug": "resumable-saga-e2e",
            "name": "Resumable Saga E2E",
            "business_category": "retail",
            "country": "NG",
            "city": "Lagos",
            "timezone": "Africa/Lagos",
            "currency_code": "NGN",
        },
    )
    assert replayed_start.status_code == 200
    assert replayed_start.json()["id"] == onboarding_id

    def command(
        path: str,
        key: str,
        body: dict[str, object],
        *,
        expected_status: int = 200,
    ) -> dict[str, object]:
        nonlocal view
        response = client.post(
            f"/v1/admin/onboardings/{onboarding_id}/{path}",
            headers={
                **operator,
                "Idempotency-Key": key,
                "If-Match": str(view["revision"]),
            },
            json=body,
        )
        assert response.status_code == expected_status, response.text
        view = response.json()
        return view

    owner_body = {
        # Existing global operator profile remains authoritative.
        "name": "Tenant-specific alias must not overwrite operator",
        "phone_e164": operator_phone,
        "email": "tenant-alias@example.com",
    }
    command(
        "owner",
        "saga-e2e-owner",
        owner_body,
    )
    assert view["owner"]["name"] == "Ope Operator"
    replayed_owner = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/owner",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-owner",
            "If-Match": "0",
        },
        json=owner_body,
    )
    assert replayed_owner.status_code == 200
    assert replayed_owner.json()["revision"] == view["revision"]
    conflicting_owner_replay = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/owner",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-owner",
            "If-Match": "0",
        },
        json={**owner_body, "name": "Different idempotent input"},
    )
    assert conflicting_owner_replay.status_code == 409
    assert conflicting_owner_replay.json()["detail"]["code"] == "idempotency_conflict"
    # Global operator routes remain available to finish provisioning, while
    # tenant-scoped user routes stay closed until atomic activation.
    tenant_route_before_activation = client.get(
        "/v1/settings/team",
        headers={**operator, "X-Tenant-ID": tenant_id},
    )
    assert tenant_route_before_activation.status_code == 403
    stale_revision = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/phone",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-phone-stale",
            "If-Match": "0",
        },
        json={"confirmation": "approve", "label": "Demo owner"},
    )
    assert stale_revision.status_code == 412
    assert stale_revision.json()["detail"]["code"] == "revision_conflict"
    command(
        "phone",
        "saga-e2e-phone",
        {"confirmation": "approve", "label": "Demo owner"},
    )
    bumpa_body = {
        "api_key": secret,
        "provider": "bumpa",
        "scope_type": "business_id",
        "scope_id": "saga-e2e-business",
        "store_timezone": "Africa/Lagos",
        "store_currency": "NGN",
    }
    bumpa_revision = view["revision"]
    command(
        "bumpa",
        "saga-e2e-bumpa",
        bumpa_body,
    )
    replayed_bumpa = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/bumpa",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-bumpa",
            "If-Match": str(bumpa_revision),
        },
        json=bumpa_body,
    )
    assert replayed_bumpa.status_code == 200
    conflicting_bumpa_replay = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/bumpa",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-bumpa",
            "If-Match": str(bumpa_revision),
        },
        json={**bumpa_body, "api_key": "replacement-test-key"},
    )
    assert conflicting_bumpa_replay.status_code == 409
    assert conflicting_bumpa_replay.json()["detail"]["code"] == "idempotency_conflict"
    sync_revision = view["revision"]
    sync_body = {"date_from": "2026-06-13", "date_to": "2026-07-13"}
    command(
        "initial-sync",
        "saga-e2e-sync",
        sync_body,
        expected_status=202,
    )
    job_id = view["initial_sync"]["job_id"]
    replayed_sync = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/initial-sync",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-sync",
            "If-Match": str(sync_revision),
        },
        json=sync_body,
    )
    assert replayed_sync.status_code == 202
    assert replayed_sync.json()["initial_sync"]["job_id"] == job_id

    with SessionLocal() as worker_db:
        claimed = claim_job(worker_db, job_id, "onboarding-e2e-worker")
        assert claimed is not None
        result = registry.handler_for(claimed.kind)(worker_db, claimed)
        complete_job(
            worker_db,
            job_id,
            result,
            worker_id="onboarding-e2e-worker",
        )

    refreshed = client.get(f"/v1/admin/onboardings/{onboarding_id}", headers=operator)
    assert refreshed.status_code == 200, refreshed.text
    view = refreshed.json()
    assert view["initial_sync"]["job_status"] == "succeeded"
    assert view["initial_sync"]["completion_quality"] == "complete"

    command(
        "initial-sync/accept",
        "saga-e2e-sync-accept",
        {"confirmation": "accept"},
    )
    command(
        "hermes",
        "saga-e2e-hermes",
        {"confirmation": "provision"},
    )
    pre_complete_revision = view["revision"]
    complete_body = {"confirmation": "activate"}
    command(
        "complete",
        "saga-e2e-complete",
        complete_body,
    )
    replayed_complete = client.post(
        f"/v1/admin/onboardings/{onboarding_id}/complete",
        headers={
            **operator,
            "Idempotency-Key": "saga-e2e-complete",
            "If-Match": str(pre_complete_revision),
        },
        json=complete_body,
    )
    assert replayed_complete.status_code == 200
    replayed_view = replayed_complete.json()
    assert replayed_view["id"] == view["id"]
    assert replayed_view["tenant_id"] == view["tenant_id"]
    assert replayed_view["owner"]["user_id"] == view["owner"]["user_id"]
    assert replayed_view["owner"]["membership_id"] == view["owner"]["membership_id"]
    assert replayed_view["phone"]["identity_id"] == view["phone"]["identity_id"]
    assert replayed_view["bumpa"]["connection_id"] == view["bumpa"]["connection_id"]
    assert replayed_view["initial_sync"]["job_id"] == view["initial_sync"]["job_id"]
    assert replayed_view["initial_sync"]["sync_run_id"] == view["initial_sync"]["sync_run_id"]
    assert replayed_view["hermes"]["profile_id"] == view["hermes"]["profile_id"]
    assert replayed_view["revision"] == view["revision"]

    assert view["status"] == "completed"
    assert view["current_step"] == "completed"
    assert view["tenant"]["status"] == "active"
    serialized = str(view)
    assert operator_phone not in serialized
    assert secret not in serialized

    with SessionLocal() as db:
        tenant = db.get(Tenant, tenant_id)
        saga = db.get(TenantOnboarding, onboarding_id)
        completion_events = list(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id,
                    AuditLog.action == "tenant.onboarding.completed",
                    AuditLog.resource_id == onboarding_id,
                )
            )
        )
        operator_role = db.scalar(
            select(PlatformRole).where(
                PlatformRole.user_id == saga.owner_user_id,
                PlatformRole.role == "operator",
            )
        )
    assert tenant is not None and tenant.status == "active"
    assert saga is not None and saga.status == "completed"
    assert len(completion_events) == 1
    assert completion_events[0].resource_id == onboarding_id
    assert completion_events[0].tenant_id == tenant_id
    assert operator_role is not None
    tenant_route_after_activation = client.get(
        "/v1/settings/team",
        headers={**operator, "X-Tenant-ID": tenant_id},
    )
    assert tenant_route_after_activation.status_code == 200
