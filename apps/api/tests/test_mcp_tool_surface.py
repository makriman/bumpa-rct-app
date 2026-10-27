from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    Conversation,
    HermesProfile,
    McpConnection,
    McpToolPermission,
    OperationalAgentEvent,
    Tenant,
    User,
)
from app.services import mcp_business as tools
from app.services.agent_actions import AgentActionError, decode_action_context_token
from app.services.chat import _provider_session_key
from tests.run_fixture import active_context as _fixture_active_context

active_context = cast(Callable[..., str], _fixture_active_context)


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
        "sandbox_worker_url": "https://sandbox.example.com",
        "sandbox_service_token": ("sandbox-fixture-token-with-at-least-thirty-two-characters"),  # noqa: S106
    }
    values.update(overrides)
    return Settings(**values)


def _active_scope(factory: sessionmaker[Session], settings: Settings) -> SimpleNamespace:
    with factory() as db:
        tenant = Tenant(slug="mcp-current-scope", name="MCP Current Scope")
        user = User(primary_phone_e164="+2348000000078")
        db.add_all((tenant, user))
        db.flush()
        conversation = Conversation(tenant_id=tenant.id, user_id=user.id, channel="web")
        db.add(conversation)
        db.flush()
        conversation.provider_session_key = _provider_session_key(
            settings,
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel="web",
        )
        token = active_context(
            db,
            settings,
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel="web",
        )
        return SimpleNamespace(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            workspace=conversation.provider_session_key,
            token=token,
        )


def _bind_scope(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    settings: Settings,
    tenant_id: str,
) -> None:
    monkeypatch.setattr(tools, "settings", settings)
    monkeypatch.setattr(tools, "SessionLocal", factory)
    monkeypatch.setattr(tools, "_tenant_id", lambda: tenant_id)
    monkeypatch.setattr(tools, "set_security_context", lambda *_args, **_kwargs: None)


def test_mcp_tool_surface_binds_tenant_and_preserves_capability_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    settings = _settings()
    with factory() as db:
        tenant = Tenant(slug="mcp-surface", name="MCP Surface")
        owner = User(primary_phone_e164="+2348000000077")
        db.add_all((tenant, owner))
        db.flush()
        conversation = Conversation(tenant_id=tenant.id, user_id=owner.id, channel="web")
        db.add(conversation)
        db.flush()
        conversation.provider_session_key = _provider_session_key(
            settings,
            tenant_id=tenant.id,
            user_id=owner.id,
            conversation_id=conversation.id,
            channel="web",
        )
        workspace = conversation.provider_session_key
        context = active_context(
            db,
            settings,
            tenant_id=tenant.id,
            user_id=owner.id,
            conversation_id=conversation.id,
            channel="web",
        )
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

    _bind_scope(monkeypatch, factory, settings, tenant_id)
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
        "queue_sandbox_file",
        lambda _db, _settings, **values: {"queued_file": values},
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

    assert tools.get_business_profile(context)["tenant"] == tenant_id
    assert tools.get_data_coverage(context)["tool"] == "coverage"
    assert tools.get_business_overview(context, compare_to="none")["compare_to"] == "none"
    assert tools.get_sales_trend(context, granularity="invalid")["granularity"] == "day"
    assert tools.get_order_breakdown(context)["tool"] == "orders"
    assert tools.get_product_performance(context, limit=5)["limit"] == 5
    assert tools.get_customer_summary(context)["tool"] == "customers"
    assert tools.get_inventory_overview(context)["tool"] == "inventory"
    assert tools.calculate_exactly("(10 + 5) * 2", context)["result"] == "30"
    listed = tools.list_connected_services(context)
    assert listed["connections"][0]["connection_id"] == connection_id
    assert listed["connections"][0]["tools"][0]["tool_name"] == "create_file"
    assert (
        tools.read_connected_service(
            connection_id,
            "read_file",
            {"file_id": "file-1"},
            context,
        )["connector_read"]["tenant_id"]
        == tenant_id
    )
    action = tools.prepare_connector_action(
        connection_id,
        "create_file",
        "Create exact file",
        {"name": "plan.txt"},
        context,
    )
    assert action["confirmation_required"] is True
    assert action["exact_input"] == {"name": "plan.txt"}
    assert tools.research_web("Ghana expansion", context)["research"]["query"] == "Ghana expansion"
    assert tools.generate_image("Create a poster", context)["generated"]["tenant_id"] == (tenant_id)
    queued_file = tools.queue_sandbox_file_for_delivery(
        workspace,
        "/workspace/plan.csv",
        "plan.csv",
        "text/csv",
        context,
    )
    assert queued_file["queued_file"]["tenant_id"] == tenant_id
    assert tools.sandbox_run_code(workspace, "python", "print(1)", context)["operation"] == "code"
    assert tools.sandbox_exec(workspace, "pwd", context)["operation"] == "exec"
    assert tools.sandbox_file(workspace, "read", "/workspace/a", context)["operation"] == "file"
    assert tools.sandbox_destroy(workspace, context)["operation"] == "destroy"


def test_mcp_provider_features_fail_closed_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        external_connectors_enabled=False,
        web_research_enabled=False,
        sandbox_tools_enabled=False,
        managed_image_generation_enabled=False,
    )
    factory = _factory()
    scope = _active_scope(factory, settings)
    _bind_scope(monkeypatch, factory, settings, scope.tenant_id)
    with factory() as db:
        decode_action_context_token(
            settings, scope.token, expected_tenant_id=scope.tenant_id, db=db
        )
    assert tools.read_connected_service("connection", "read_file", {}, scope.token)[
        "availability"
    ] == ("unavailable")
    assert tools.research_web("Ghana", scope.token)["availability"] == "unavailable"
    assert tools.generate_image("Poster", scope.token)["availability"] == "unavailable"
    assert (
        tools.queue_sandbox_file_for_delivery(
            scope.workspace,
            "/workspace/plan.csv",
            "plan.csv",
            "text/csv",
            scope.token,
        )["availability"]
        == "unavailable"
    )
    assert tools.sandbox_run_code(scope.workspace, "python", "print(1)", scope.token)[
        "availability"
    ] == ("unavailable")
    assert tools.sandbox_exec(scope.workspace, "pwd", scope.token)["availability"] == "unavailable"
    assert (
        tools.sandbox_file(scope.workspace, "list", "/workspace", scope.token)["availability"]
        == "unavailable"
    )
    assert tools.sandbox_destroy(scope.workspace, scope.token)["availability"] == "unavailable"


@pytest.mark.parametrize("surface", ["business_read", "connector_read", "sandbox"])
@pytest.mark.parametrize("token_state", ["omitted", "empty", "stale", "revoked_actor"])
def test_mcp_read_and_sandbox_surfaces_reject_missing_or_revoked_context_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    token_state: str,
) -> None:
    settings = _settings()
    factory = _factory()
    scope = _active_scope(factory, settings)
    _bind_scope(monkeypatch, factory, settings, scope.tenant_id)
    calls = []

    def forbidden(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(surface)
        raise AssertionError("A missing/stale capability reached a provider")

    monkeypatch.setattr(tools, "business_profile", forbidden)
    monkeypatch.setattr(tools, "execute_connector_read", forbidden)
    monkeypatch.setattr(tools, "SandboxClient", forbidden)
    kwargs = {}
    if token_state == "empty":
        kwargs["action_context_token"] = ""
    elif token_state == "stale":
        kwargs["action_context_token"] = scope.token
        with factory() as db:
            replacement = active_context(
                db,
                settings,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                conversation_id=scope.conversation_id,
                channel="web",
            )
            decode_action_context_token(
                settings, replacement, expected_tenant_id=scope.tenant_id, db=db
            )
    elif token_state == "revoked_actor":
        kwargs["action_context_token"] = scope.token
        with factory() as db:
            actor = db.get(User, scope.user_id)
            assert actor is not None
            actor.status = "disabled"
            db.commit()

    with pytest.raises(TypeError if token_state == "omitted" else AgentActionError):
        if surface == "business_read":
            tools.get_business_profile(**kwargs)
        elif surface == "connector_read":
            tools.read_connected_service("connection", "read_file", {}, **kwargs)
        else:
            tools.sandbox_run_code(scope.workspace, "python", "print(1)", **kwargs)
    assert calls == []


def test_mcp_sandbox_rejects_another_conversation_workspace_with_current_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    factory = _factory()
    scope = _active_scope(factory, settings)
    _bind_scope(monkeypatch, factory, settings, scope.tenant_id)
    calls = []
    monkeypatch.setattr(tools, "SandboxClient", lambda *_args: calls.append("sandbox"))
    with pytest.raises(PermissionError, match="does not belong"):
        tools.sandbox_run_code("bb_another_conversation", "python", "print(1)", scope.token)
    assert calls == []


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
