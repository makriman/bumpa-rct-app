# ruff: noqa: S106 - all credentials are synthetic test fixtures.
from __future__ import annotations

import hashlib
import json
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    McpConnection,
    McpToolPermission,
    MerchantBusinessProfile,
    PendingAgentAction,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
)
from app.services import agent_actions as actions
from app.services import connector_executor as connectors
from app.services.home_assistant import encrypt_home_assistant_credentials
from app.services.mcp_permissions import McpPermissionDenied


@pytest.fixture()
def state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'approval.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, app_env="test", external_connectors_enabled=True)
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="synthetic-approval", name="Synthetic approval business")
        user = User(primary_phone_e164="+15555550100")
        db.add_all((tenant, user))
        db.flush()
        member = TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role="owner", status="active"
        )
        profile = MerchantBusinessProfile(
            tenant_id=tenant.id, state={"revision": 1, "name": "Synthetic"}
        )
        conversation = Conversation(
            tenant_id=tenant.id, user_id=user.id, channel="web", environment_lane="test"
        )
        connection = McpConnection(
            tenant_id=tenant.id,
            created_by=user.id,
            provider="google_sheets",
            status="active",
            encrypted_credentials=FieldCipher.from_settings(settings).encrypt(
                json.dumps(
                    {
                        "access_token": "synthetic-access-token",
                        "obtained_at": int(time.time()),
                        "expires_in": 3600,
                    }
                )
            ),
            read_only=False,
            admin_approved=True,
            allowed_resources=["sheets:spreadsheet:sheet-one"],
        )
        db.add_all((member, profile, conversation, connection))
        db.flush()
        permission = McpToolPermission(
            tenant_id=tenant.id,
            mcp_connection_id=connection.id,
            tool_name="append_rows",
            permission="write_with_confirmation",
            created_by=user.id,
        )
        read_permission = McpToolPermission(
            tenant_id=tenant.id,
            mcp_connection_id=connection.id,
            tool_name="read_sheet",
            permission="read",
            created_by=user.id,
        )
        source = AgentMessage(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel="web",
            direction="inbound",
            content="Append this exact row",
        )
        db.add_all((permission, read_permission, source))
        db.flush()
        run = AgentExecutionRun(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            initiating_message_id=source.id,
            status="completed",
            expires_at=utcnow(),
        )
        db.add(run)
        db.flush()
        action = PendingAgentAction(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            mcp_connection_id=connection.id,
            tool_name="append_rows",
            target_summary="Append approved row",
            action_input={
                "spreadsheet_id": "sheet-one",
                "range": "Sales!A:A",
                "rows": [["Approved"]],
            },
            approval_binding={
                "conversation_id": conversation.id,
                "channel": "web",
                "lane": "test",
                "waba": "",
                "receiver": "",
                "actor_identity_id": None,
                "initiating_message_id": source.id,
                "execution_run_id": run.id,
            },
            authorization_revision=actions.authorization_revision(
                db, tenant.id, connection.id, "append_rows"
            ),
            status="pending",
            idempotency_key="synthetic-exact-action",
            confirmation_token_hash="0" * 64,
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(action)
        db.flush()
        value = SimpleNamespace(
            db=db,
            settings=settings,
            tenant=tenant,
            user=user,
            member=member,
            profile=profile,
            conversation=conversation,
            connection=connection,
            permission=permission,
            read_permission=read_permission,
            source=source,
            run=run,
            action=action,
        )
        sign(value)
        yield value
    engine.dispose()


def sign(state):
    state.token = actions.confirmation_token(state.settings, state.action)
    state.action.confirmation_token_hash = hashlib.sha256(state.token.encode()).hexdigest()
    state.db.commit()


def confirm(state):
    return actions.decide_pending_action(
        state.db,
        state.settings,
        action_id=state.action.id,
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        supplied_token=state.token,
        decision="confirm",
    )


def mock_provider(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(200, json={"updates": {"updatedRows": 1}})
        return httpx.Response(200, json={"values": [["Synthetic"]]})

    transport = httpx.MockTransport(handler)
    original = connectors.execute_connector_write

    def execute(db, settings, **options):
        return original(db, settings, **options, transport=transport)

    monkeypatch.setattr(actions, "execute_connector_write", execute)
    return calls, transport


def mutate(state, target):
    with Session(state.db.get_bind()) as other:
        if target == "member":
            other.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state.member.id)
                .values(role="member")
            )
        elif target == "user":
            other.execute(update(User).where(User.id == state.user.id).values(status="disabled"))
        elif target == "tenant":
            other.execute(
                update(Tenant).where(Tenant.id == state.tenant.id).values(status="suspended")
            )
        elif target == "conversation":
            other.execute(
                update(Conversation)
                .where(Conversation.id == state.conversation.id)
                .values(status="closed")
            )
        elif target == "alias":
            other.execute(
                update(PhoneIdentity)
                .where(PhoneIdentity.id == state.run.actor_identity_id)
                .values(opt_out=True)
            )
        elif target == "permission":
            other.execute(
                update(McpToolPermission)
                .where(McpToolPermission.id == state.permission.id)
                .values(permission="deny")
            )
        elif target == "profile":
            other.execute(
                update(MerchantBusinessProfile)
                .where(MerchantBusinessProfile.id == state.profile.id)
                .values(state={"revision": 1, "name": "Changed"})
            )
        elif target == "resources":
            other.execute(
                update(McpConnection)
                .where(McpConnection.id == state.connection.id)
                .values(allowed_resources=[])
            )
        elif target == "credentials":
            other.execute(
                update(McpConnection)
                .where(McpConnection.id == state.connection.id)
                .values(encrypted_credentials="rotated-synthetic-ciphertext")
            )
        else:
            raise AssertionError(target)
        other.commit()


def add_alias(state):
    identity = PhoneIdentity(
        tenant_id=state.tenant.id,
        user_id=state.user.id,
        phone_e164="+15555550200",
        status="approved",
        opt_out=False,
    )
    state.db.add(identity)
    state.db.flush()
    state.conversation.channel = state.source.channel = "whatsapp"
    state.run.actor_identity_id = identity.id
    state.action.approval_binding = {
        **state.action.approval_binding,
        "channel": "whatsapp",
        "actor_identity_id": identity.id,
    }
    sign(state)
    return identity


@pytest.mark.parametrize("target", ["member", "user", "tenant"])
def test_principal_identity_map_cannot_preserve_revoked_write_authority(state, target):
    # Keep these exact ORM instances alive, as the request's Principal does.
    cached = getattr(state, target)
    old_status = getattr(cached, "role", None) or cached.status
    mutate(state, target)
    assert (getattr(cached, "role", None) or cached.status) == old_status
    with pytest.raises(actions.AgentActionError, match="owner or admin"):
        actions.require_write_actor(state.db, state.tenant.id, state.user.id)


@pytest.mark.parametrize("target", ["permission", "profile", "resources", "credentials"])
def test_authorization_revision_refreshes_all_held_objects(state, target):
    before = state.action.authorization_revision
    mutate(state, target)
    after = actions.authorization_revision(
        state.db, state.tenant.id, state.connection.id, "append_rows"
    )
    assert before != after


@pytest.mark.parametrize(
    "target",
    [
        "member",
        "user",
        "tenant",
        "conversation",
        "alias",
        "permission",
        "profile",
        "resources",
        "credentials",
    ],
)
def test_revocation_after_executing_commit_blocks_external_effect(state, monkeypatch, target):
    if target == "alias":
        add_alias(state)
    calls, _transport = mock_provider(monkeypatch)
    revoked = False

    def after_commit(_session):
        nonlocal revoked
        if not revoked and state.action.status == "executing":
            revoked = True
            mutate(state, target)

    event.listen(state.db, "after_commit", after_commit)
    try:
        with pytest.raises(actions.AgentActionError):
            confirm(state)
    finally:
        event.remove(state.db, "after_commit", after_commit)
    assert revoked
    assert calls == []
    assert state.action.status == "failed"


def test_exact_unchanged_action_executes_once_and_replay_is_idempotent(state, monkeypatch):
    calls, _transport = mock_provider(monkeypatch)
    assert confirm(state).status == "succeeded"
    assert len(calls) == 1
    assert confirm(state).status == "succeeded"
    assert len(calls) == 1


def test_expired_write_token_never_refreshes_or_changes_credentials(state, monkeypatch):
    cipher = FieldCipher.from_settings(state.settings)
    state.connection.encrypted_credentials = cipher.encrypt(
        json.dumps(
            {
                "access_token": "synthetic-expired-token",
                "refresh_token": "synthetic-refresh-token",
                "obtained_at": 0,
                "expires_in": 1,
            }
        )
    )
    state.action.authorization_revision = actions.authorization_revision(
        state.db, state.tenant.id, state.connection.id, "append_rows"
    )
    # Persist the explicit test credential change before building the new approval.
    state.db.flush()
    state.action.authorization_revision = actions.authorization_revision(
        state.db, state.tenant.id, state.connection.id, "append_rows"
    )
    sign(state)
    original_credentials = state.connection.encrypted_credentials
    calls, _transport = mock_provider(monkeypatch)

    def forbidden(**_options):
        pytest.fail("An approved write must not refresh credentials inline")

    monkeypatch.setattr(connectors, "refresh_access_token", forbidden)
    with pytest.raises(actions.AgentActionError, match="fresh preview"):
        confirm(state)
    assert calls == []
    assert state.connection.encrypted_credentials == original_credentials


@pytest.mark.parametrize("target", ["member", "conversation", "alias", "permission", "resources"])
def test_pre_dispatch_callback_revalidates_after_an_internal_commit(state, monkeypatch, target):
    if target == "alias":
        add_alias(state)
    calls, _transport = mock_provider(monkeypatch)
    original = connectors._access_token

    def acquiring(db, connection, settings, **options):
        token = original(db, connection, settings, **options)
        # Simulate the historical OAuth helper's commit boundary, including a
        # committed revocation before control returns to the provider dispatcher.
        db.commit()
        mutate(state, target)
        return token

    monkeypatch.setattr(connectors, "_access_token", acquiring)
    with pytest.raises(actions.AgentActionError):
        confirm(state)
    assert calls == []


def test_read_refresh_callback_runs_after_credential_commit_and_can_revoke_dispatch(
    state, monkeypatch
):
    cipher = FieldCipher.from_settings(state.settings)
    state.connection.encrypted_credentials = cipher.encrypt(
        json.dumps(
            {
                "access_token": "synthetic-old-token",
                "refresh_token": "synthetic-refresh-token",
                "obtained_at": 0,
                "expires_in": 1,
            }
        )
    )
    state.db.commit()
    calls, transport = mock_provider(monkeypatch)
    refreshed = []

    def refresh(**options):
        refreshed.append(True)
        return {
            **options["credential_bundle"],
            "access_token": "synthetic-new-token",
            "obtained_at": int(time.time()),
            "expires_in": 3600,
        }

    def guard():
        # OAuth refresh is durable before this guard; actor/source locks must
        # be reacquired here, rather than relying on pre-refresh checks.
        with Session(state.db.get_bind()) as other:
            persisted = other.get(McpConnection, state.connection.id)
            assert "synthetic-new-token" in cipher.decrypt(persisted.encrypted_credentials)
        mutate(state, "member")
        actions.require_write_actor(state.db, state.tenant.id, state.user.id)

    monkeypatch.setattr(connectors, "refresh_access_token", refresh)
    with pytest.raises(actions.AgentActionError):
        connectors.execute_connector_read(
            state.db,
            state.settings,
            tenant_id=state.tenant.id,
            connection_id=state.connection.id,
            tool_name="read_sheet",
            tool_input={"spreadsheet_id": "sheet-one", "range": "A:A"},
            transport=transport,
            before_dispatch=guard,
        )
    assert refreshed == [True]
    assert calls == []


def test_dispatch_recomputes_resource_allowlist_after_callback(state, monkeypatch):
    calls, transport = mock_provider(monkeypatch)

    def guard():
        state.db.execute(
            update(McpConnection)
            .where(McpConnection.id == state.connection.id)
            .values(allowed_resources=[])
            .execution_options(synchronize_session=False)
        )

    with pytest.raises(connectors.ConnectorExecutionError, match="allowlist"):
        connectors.execute_connector_read(
            state.db,
            state.settings,
            tenant_id=state.tenant.id,
            connection_id=state.connection.id,
            tool_name="read_sheet",
            tool_input={"spreadsheet_id": "sheet-one", "range": "A:A"},
            transport=transport,
            before_dispatch=guard,
        )
    assert calls == []


def test_dispatch_does_not_use_stale_token_after_callback_credential_rotation(state, monkeypatch):
    calls, transport = mock_provider(monkeypatch)

    def guard():
        state.db.execute(
            update(McpConnection)
            .where(McpConnection.id == state.connection.id)
            .values(encrypted_credentials="rotated-ciphertext")
            .execution_options(synchronize_session=False)
        )

    with pytest.raises(connectors.ConnectorExecutionError, match="authorization changed"):
        connectors.execute_connector_read(
            state.db,
            state.settings,
            tenant_id=state.tenant.id,
            connection_id=state.connection.id,
            tool_name="read_sheet",
            tool_input={"spreadsheet_id": "sheet-one", "range": "A:A"},
            transport=transport,
            before_dispatch=guard,
        )
    assert calls == []


def test_home_assistant_checks_callback_before_any_service_request(state, monkeypatch):
    state.connection.provider = "home_assistant"
    state.connection.encrypted_credentials = encrypt_home_assistant_credentials(
        state.settings,
        base_url="https://automation.example.com",
        access_token="synthetic-home-assistant-token",
    )
    state.connection.allowed_resources = [
        "home_assistant:entity:light.shop",
        "home_assistant:service:light.turn_on",
    ]
    state.permission.tool_name = "call_service"
    state.db.commit()
    calls, transport = mock_provider(monkeypatch)

    def guard():
        raise actions.AgentActionError("Source conversation was closed")

    with pytest.raises(actions.AgentActionError):
        connectors.execute_connector_write(
            state.db,
            state.settings,
            tenant_id=state.tenant.id,
            connection_id=state.connection.id,
            tool_name="call_service",
            tool_input={"entity_id": "light.shop", "service": "light.turn_on"},
            idempotency_key="synthetic-service",
            transport=transport,
            before_dispatch=guard,
        )
    assert calls == []


def test_cached_denied_permission_cannot_dispatch_even_without_actor_callback(state, monkeypatch):
    calls, transport = mock_provider(monkeypatch)
    state.db.execute(
        update(McpToolPermission)
        .where(McpToolPermission.id == state.read_permission.id)
        .values(permission="deny")
        .execution_options(synchronize_session=False)
    )
    state.db.commit()
    assert state.read_permission.permission == "read"
    with pytest.raises(McpPermissionDenied):
        connectors.execute_connector_read(
            state.db,
            state.settings,
            tenant_id=state.tenant.id,
            connection_id=state.connection.id,
            tool_name="read_sheet",
            tool_input={"spreadsheet_id": "sheet-one", "range": "A:A"},
            transport=transport,
        )
    assert calls == []
