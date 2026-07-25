from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    HermesProfile,
    McpConnection,
    McpToolPermission,
    OperationalAgentEvent,
    Tenant,
    User,
)
from app.services import mcp_business as tools


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "field_encryption_key": "mcp-tool-surface-key",
        "internal_service_token": "mcp-tool-surface-service-token-0001",
        "agent_backend": "hermes",
        "external_connectors_enabled": True,
        "web_research_enabled": True,
        "sandbox_tools_enabled": True,
        "managed_image_generation_enabled": True,
        "tavily_api_key": "tavily-fixture-key-with-safe-length",
        "sandbox_worker_url": "https://sandbox.example.com",
        "sandbox_service_token": ("sandbox-fixture-token-with-at-least-thirty-two-characters"),  # noqa: S106
    }
    values.update(overrides)
    return Settings(**values)


def test_mcp_tool_surface_binds_tenant_and_preserves_capability_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    with factory() as db:
        tenant = Tenant(slug="mcp-surface", name="MCP Surface")
        owner = User(primary_phone_e164="+2348000000077")
        db.add_all((tenant, owner))
        db.flush()
        connection = McpConnection(
            tenant_id=tenant.id,
            created_by=owner.id,
            provider="google_drive",
            status="active",
            read_only=False,
            admin_approved=True,
            encrypted_credentials="fixture",
            allowed_resources=["drive:file:file-1"],
        )
        db.add(connection)
        db.flush()
        db.add_all(
            (
                McpToolPermission(
                    tenant_id=tenant.id,
                    mcp_connection_id=connection.id,
                    tool_name="read_file",
                    permission="read",
                    created_by=owner.id,
                ),
                McpToolPermission(
                    tenant_id=tenant.id,
                    mcp_connection_id=connection.id,
                    tool_name="create_file",
                    permission="write_with_confirmation",
                    created_by=owner.id,
                ),
            )
        )
        db.commit()
        tenant_id = tenant.id
        connection_id = connection.id

    def run_tool(
        _name: str,
        operation: Any,
    ) -> dict[str, Any]:
        with factory() as db:
            return operation(db, tenant_id)

    monkeypatch.setattr(tools, "settings", _settings())
    monkeypatch.setattr(tools, "_run_tool", run_tool)
    monkeypatch.setattr(
        tools,
        "business_profile",
        lambda _db, scoped: {"tool": "profile", "tenant": scoped},
    )
    monkeypatch.setattr(
        tools,
        "data_coverage",
        lambda _db, scoped: {"tool": "coverage", "tenant": scoped},
    )
    monkeypatch.setattr(
        tools,
        "business_overview",
        lambda _db, scoped, **values: {"tool": "overview", "tenant": scoped, **values},
    )
    monkeypatch.setattr(
        tools,
        "sales_trend",
        lambda _db, scoped, **values: {"tool": "trend", "tenant": scoped, **values},
    )
    monkeypatch.setattr(
        tools,
        "order_breakdown",
        lambda _db, scoped, **values: {"tool": "orders", "tenant": scoped, **values},
    )
    monkeypatch.setattr(
        tools,
        "product_performance",
        lambda _db, scoped, **values: {"tool": "products", "tenant": scoped, **values},
    )
    monkeypatch.setattr(
        tools,
        "customer_summary",
        lambda _db, scoped, **values: {"tool": "customers", "tenant": scoped, **values},
    )
    monkeypatch.setattr(
        tools,
        "inventory_overview",
        lambda _db, scoped: {"tool": "inventory", "tenant": scoped},
    )
    monkeypatch.setattr(
        tools,
        "execute_connector_read",
        lambda _db, _settings, **values: {"connector_read": values},
    )
    monkeypatch.setattr(
        tools,
        "search_web",
        lambda _settings, **values: {"research": values},
    )
    monkeypatch.setattr(
        tools,
        "generate_and_queue_image",
        lambda _db, _settings, **values: {"generated": values},
    )
    monkeypatch.setattr(
        tools,
        "prepare_pending_action",
        lambda _db, _settings, **_values: (
            SimpleNamespace(
                id="action-1",
                target_summary="Create exact file",
                action_input={"name": "plan.txt"},
                expires_at=datetime(2026, 7, 25, tzinfo=UTC),
            ),
            "confirmation-token",
        ),
    )

    class FakeSandbox:
        def __init__(self, _settings: Settings) -> None:
            pass

        def run_code(self, **values: Any) -> dict[str, Any]:
            return {"operation": "code", **values}

        def exec(self, **values: Any) -> dict[str, Any]:
            return {"operation": "exec", **values}

        def file_operation(self, **values: Any) -> dict[str, Any]:
            return {"operation": "file", **values}

        def destroy(self, **values: Any) -> dict[str, Any]:
            return {"operation": "destroy", **values}

    monkeypatch.setattr(tools, "SandboxClient", FakeSandbox)

    assert tools.get_business_profile()["tenant"] == tenant_id
    assert tools.get_data_coverage()["tool"] == "coverage"
    assert tools.get_business_overview(compare_to="none")["compare_to"] == "none"
    assert tools.get_sales_trend(granularity="invalid")["granularity"] == "day"
    assert tools.get_order_breakdown()["tool"] == "orders"
    assert tools.get_product_performance(limit=5)["limit"] == 5
    assert tools.get_customer_summary()["tool"] == "customers"
    assert tools.get_inventory_overview()["tool"] == "inventory"
    assert tools.calculate_exactly("(10 + 5) * 2")["result"] == "30"
    listed = tools.list_connected_services()
    assert listed["connections"][0]["connection_id"] == connection_id
    assert listed["connections"][0]["tools"][0]["tool_name"] == "create_file"
    assert (
        tools.read_connected_service(
            connection_id,
            "read_file",
            {"file_id": "file-1"},
        )["connector_read"]["tenant_id"]
        == tenant_id
    )
    action = tools.prepare_connector_action(
        connection_id,
        "create_file",
        "Create exact file",
        {"name": "plan.txt"},
        "context-token",
    )
    assert action["confirmation_required"] is True
    assert action["exact_input"] == {"name": "plan.txt"}
    assert tools.research_web("Ghana expansion")["research"]["query"] == "Ghana expansion"
    assert tools.generate_image("Create a poster", "context-token")["generated"]["tenant_id"] == (
        tenant_id
    )
    assert tools.sandbox_run_code("session", "python", "print(1)")["operation"] == "code"
    assert tools.sandbox_exec("session", "pwd")["operation"] == "exec"
    assert tools.sandbox_file("session", "read", "/workspace/a")["operation"] == "file"
    assert tools.sandbox_destroy("session")["operation"] == "destroy"


def test_mcp_provider_features_fail_closed_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tools,
        "settings",
        _settings(
            external_connectors_enabled=False,
            web_research_enabled=False,
            sandbox_tools_enabled=False,
            managed_image_generation_enabled=False,
        ),
    )
    assert tools.read_connected_service("connection", "read_file", {})["availability"] == (
        "unavailable"
    )
    assert tools.research_web("Ghana")["availability"] == "unavailable"
    assert tools.generate_image("Poster", "context")["availability"] == "unavailable"
    assert tools.sandbox_run_code("session", "python", "print(1)")["availability"] == (
        "unavailable"
    )
    assert tools.sandbox_exec("session", "pwd")["availability"] == "unavailable"
    assert tools.sandbox_file("session", "list", "/workspace")["availability"] == "unavailable"
    assert tools.sandbox_destroy("session")["availability"] == "unavailable"


def test_mcp_profile_token_verifier_and_operational_events_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    token = "mcp-profile-token-with-at-least-thirty-two-characters"
    with factory() as db:
        tenant = Tenant(slug="mcp-auth", name="MCP Auth")
        db.add(tenant)
        db.flush()
        profile = HermesProfile(
            tenant_id=tenant.id,
            profile_name="mcp-auth",
            profile_path="/profiles/mcp-auth",
            provider="hermes",
            api_internal_url="http://hermes:8700/v1",
            api_port=8700,
            encrypted_api_key="fixture",
            mcp_token_hash=hashlib.sha256(token.encode()).hexdigest(),
            status="active",
        )
        db.add(profile)
        db.commit()
        tenant_id = tenant.id
        profile_id = profile.id

    monkeypatch.setattr(tools, "SessionLocal", factory)
    monkeypatch.setattr(tools, "set_security_context", lambda *_args, **_kwargs: None)
    verified = asyncio.run(tools.HermesProfileTokenVerifier().verify_token(token))
    assert verified is not None
    assert verified.subject == tenant_id
    assert verified.client_id == profile_id
    assert asyncio.run(tools.HermesProfileTokenVerifier().verify_token("short")) is None

    monkeypatch.setattr(tools, "_tenant_id", lambda: tenant_id)
    result = tools._run_tool(
        "successful_tool",
        lambda _db, scoped: {"tenant": scoped},
    )
    assert result == {"tenant": tenant_id}
    with pytest.raises(RuntimeError, match="safe failure"):
        tools._run_tool(
            "failed_tool",
            lambda _db, _scoped: (_ for _ in ()).throw(RuntimeError("safe failure")),
        )
    with factory() as db:
        events = db.query(OperationalAgentEvent).order_by(OperationalAgentEvent.created_at).all()
        assert [event.status for event in events] == ["succeeded", "failed"]
        assert all(event.tenant_id == tenant_id for event in events)
