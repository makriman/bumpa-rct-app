from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.jobs import scheduler, worker
from app.jobs.runtime import AsyncRuntimeConfig
from app.providers.local import (
    LocalAgentRuntime,
    LocalArtifactStore,
    LocalClassifier,
    LocalCommerceProvider,
    LocalMessagingProvider,
    local_profile_key,
)
from app.providers.redaction import csv_safe, parse_money, redact_text
from tests.conftest import auth_headers


def test_all_local_provider_contracts_and_safety_branches(tmp_path: Path) -> None:
    snapshot = LocalCommerceProvider(
        "tenant-a", store_timezone="Africa/Lagos", store_currency="NGN"
    ).sync(date(2026, 1, 1), date(2026, 1, 2))
    assert len(snapshot.datasets) == 10
    assert len(snapshot.orders) == 6
    messaging = LocalMessagingProvider()
    assert messaging.send_text("+2348000000000", "hello") == messaging.send_text(
        "+2348000000000", "hello"
    )
    assert messaging.send_otp("+2348000000000", "123456").startswith("local-msg-")

    agent = LocalAgentRuntime()
    assert "Bumpa Bestie" in agent.respond("profile", "Help me", "No data")
    assert "strongest products" in agent.respond("profile", "Show sales", "Sales: 10")
    with pytest.raises(ValueError, match="credential"):
        agent.respond("profile", "Help", "api_key=forbidden")

    classifier = LocalClassifier()
    assert (
        classifier.classify("Should I restock?", "products")["primary_intent"]
        == "inventory_management"
    )
    assert (
        classifier.classify("Who is my best customer?", "customers")["primary_intent"]
        == "customer_management"
    )
    assert classifier.classify("Check this order", "orders")["primary_intent"] == "order_management"
    assert classifier.classify("What next?", "none")["ai_help_type"] == "recommendation"

    store = LocalArtifactStore(tmp_path)
    key, size, checksum = store.put("reports/a.txt", b"hello")
    assert key == "reports/a.txt" and size == 5 and len(checksum) == 64
    assert store.get(key) == b"hello"
    with pytest.raises(ValueError, match="Invalid artifact key"):
        store.put("../escape", b"no")
    assert local_profile_key().startswith("local-")

    assert parse_money(None) is None
    assert parse_money(True) is None
    assert parse_money(12) is not None
    assert parse_money(12.5) is not None
    assert parse_money("") is None
    assert redact_text("Call +234 800 000 0000") == "Call [PHONE]"
    assert csv_safe("=IMPORTXML()") == "'=IMPORTXML()"
    assert csv_safe("ordinary") == "ordinary"


def test_async_entrypoint_stop_and_disabled_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    worker._stop(15, object())
    scheduler._stop(15, object())
    assert worker.running is False
    assert scheduler.running is False
    disabled = AsyncRuntimeConfig.from_env()
    monkeypatch.setattr(worker.AsyncRuntimeConfig, "from_env", lambda: disabled)
    monkeypatch.setattr(scheduler.AsyncRuntimeConfig, "from_env", lambda: disabled)
    with pytest.raises(RuntimeError, match="disabled"):
        worker.main()
    with pytest.raises(RuntimeError, match="disabled"):
        scheduler.main()


def test_bumpa_failure_is_recorded_and_recovery_succeeds(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    operator = auth_headers(client, "+2348099990001")
    tenant_id = client.get("/v1/tenants/current", headers=owner).json()["id"]
    production_shell = client.post(
        f"/v1/admin/tenants/{tenant_id}/bumpa",
        headers=operator,
        json={
            "api_key": "placeholder-key",
            "scope_type": "business_id",
            "scope_id": "demo-business",
            "store_timezone": "Africa/Lagos",
            "store_currency": "NGN",
            "provider": "bumpa",
        },
    )
    assert production_shell.status_code == 200
    failed = client.post("/v1/bumpa/sync/latest", headers=owner)
    assert failed.status_code == 503
    runs = client.get("/v1/bumpa/sync-runs", headers=owner).json()
    assert runs[0]["status"] == "failed"

    client.post(
        f"/v1/admin/tenants/{tenant_id}/bumpa",
        headers=operator,
        json={
            "api_key": "local-key",
            "scope_type": "business_id",
            "scope_id": "demo-business",
            "store_timezone": "Africa/Lagos",
            "store_currency": "NGN",
            "provider": "local",
        },
    )
    recovered = client.post("/v1/bumpa/sync/latest", headers=owner)
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "success"


def test_admin_error_paths_and_profile_absence(client: TestClient) -> None:
    operator = auth_headers(client, "+2348099990001")
    assert client.get("/v1/admin/tenants/not-found", headers=operator).status_code == 404
    assert client.get("/v1/admin/system/errors", headers=operator).status_code == 200
    assert client.get("/v1/admin/system/sync-runs", headers=operator).status_code == 200
    assert client.get("/v1/admin/usage", headers=operator).status_code == 200
    duplicate = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json={"slug": "demo-store", "name": "Duplicate"},
    )
    assert duplicate.status_code == 409
    tenant = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json={"slug": "profile-less", "name": "Profile Less"},
    ).json()
    user = client.post(
        f"/v1/admin/tenants/{tenant['id']}/users",
        headers=operator,
        json={"name": "Profile Owner", "phone_e164": "+2348444444444", "role": "owner"},
    ).json()
    client.post(
        f"/v1/admin/tenants/{tenant['id']}/phones",
        headers=operator,
        json={"user_id": user["user_id"], "phone_e164": "+2348444444444"},
    )
    profile_owner = auth_headers(client, "+2348444444444")
    assert client.get("/v1/hermes/profile", headers=profile_owner).status_code == 404
