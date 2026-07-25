from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.db.models import AuditLog, McpConnection, McpToolPermission
from app.db.session import SessionLocal, set_security_context
from app.main import app
from app.services.mcp_oauth import (
    McpOAuthError,
    build_authorization_url,
    connection_scopes,
    decode_oauth_state,
    default_permissions,
    exchange_authorization_code,
    refresh_access_token,
    registry,
    revoke_oauth_token,
    validate_tool_permission,
)
from app.services.mcp_permissions import McpPermissionDenied, authorize_mcp_tool
from tests.conftest import auth_headers


def _remove_provider(provider: str) -> None:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        connection_ids = list(
            db.scalars(select(McpConnection.id).where(McpConnection.provider == provider)).all()
        )
        if connection_ids:
            db.execute(
                delete(McpToolPermission).where(
                    McpToolPermission.mcp_connection_id.in_(connection_ids)
                )
            )
            db.execute(delete(McpConnection).where(McpConnection.id.in_(connection_ids)))
        db.commit()


def test_operator_approval_permission_confirmation_and_revocation(client: TestClient) -> None:
    provider = "meta_ads"
    _remove_provider(provider)
    owner = auth_headers(client, "+2348012345678")
    operator = auth_headers(client, "+2348099990001")
    try:
        requested = client.post(
            "/v1/settings/mcp-connections",
            headers=owner,
            json={"provider": provider, "read_only": False},
        )
        assert requested.status_code == 201, requested.text
        connection_id = requested.json()["id"]
        assert requested.json()["status"] == "admin_pending"
        assert requested.json()["oauth_available"] is False

        duplicate = client.post(
            "/v1/settings/mcp-connections",
            headers=owner,
            json={"provider": provider},
        )
        assert duplicate.status_code == 409

        pending = client.get(
            "/v1/admin/mcp-connections?status=admin_pending",
            headers=operator,
        )
        assert pending.status_code == 200
        assert any(row["id"] == connection_id for row in pending.json())
        approved = client.patch(
            f"/v1/admin/mcp-connections/{connection_id}",
            headers=operator,
            json={"decision": "approve", "reason": "Approved for controlled testing"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        assert approved.json()["permissions"] == {
            "read_campaigns": "read",
            "update_campaign_status": "deny",
        }

        missing_ack = client.patch(
            f"/v1/settings/mcp-connections/{connection_id}/permissions/update_campaign_status",
            headers=owner,
            json={"permission": "write_with_confirmation"},
        )
        assert missing_ack.status_code == 422
        write_permission = client.patch(
            f"/v1/settings/mcp-connections/{connection_id}/permissions/update_campaign_status",
            headers=owner,
            json={
                "permission": "write_with_confirmation",
                "acknowledge_write_confirmation": True,
            },
        )
        assert write_permission.status_code == 200, write_permission.text

        oauth_unavailable = client.post(
            f"/v1/settings/mcp-connections/{connection_id}/oauth/start",
            headers=owner,
        )
        assert oauth_unavailable.status_code == 503

        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            connection = db.get(McpConnection, connection_id)
            assert connection is not None
            connection.status = "active"
            connection.encrypted_credentials = FieldCipher(
                get_settings().field_encryption_key
            ).encrypt(json.dumps({"access_token": "opaque-test-token"}))
            db.commit()
            with pytest.raises(McpPermissionDenied, match="fresh user confirmation"):
                authorize_mcp_tool(
                    db,
                    tenant_id=connection.tenant_id,
                    connection_id=connection.id,
                    tool_name="update_campaign_status",
                )
            assert (
                authorize_mcp_tool(
                    db,
                    tenant_id=connection.tenant_id,
                    connection_id=connection.id,
                    tool_name="update_campaign_status",
                    write_confirmed=True,
                ).id
                == connection.id
            )

        rejected = client.patch(
            f"/v1/admin/mcp-connections/{connection_id}",
            headers=operator,
            json={"decision": "reject", "reason": "Access is no longer required"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert set(rejected.json()["permissions"].values()) == {"deny"}

        removed = client.delete(
            f"/v1/settings/mcp-connections/{connection_id}",
            headers=owner,
        )
        assert removed.status_code == 204
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            actions = set(
                db.scalars(
                    select(AuditLog.action).where(AuditLog.resource_id == connection_id)
                ).all()
            )
        assert {
            "mcp.connection.requested",
            "mcp.connection.approved",
            "mcp.connection.rejected",
            "mcp.connection.revoked",
        } <= actions
    finally:
        _remove_provider(provider)


def test_home_assistant_requires_operator_approval_and_encrypts_manual_token(
    client: TestClient,
) -> None:
    provider = "home_assistant"
    _remove_provider(provider)
    owner = auth_headers(client, "+2348012345678")
    operator = auth_headers(client, "+2348099990001")
    access_token = "dedicated-home-assistant-pilot-token"
    try:
        requested = client.post(
            "/v1/settings/mcp-connections",
            headers=owner,
            json={
                "provider": provider,
                "read_only": False,
                "allowed_resources": [
                    "home_assistant:entity:light.shop",
                    "home_assistant:service:light.turn_on",
                ],
            },
        )
        assert requested.status_code == 201, requested.text
        connection_id = requested.json()["id"]
        assert requested.json()["oauth_available"] is False

        blocked = client.post(
            f"/v1/settings/mcp-connections/{connection_id}/home-assistant/connect",
            headers=owner,
            json={
                "base_url": "https://shop-automation.example.com",
                "access_token": access_token,
            },
        )
        assert blocked.status_code == 409

        approved = client.patch(
            f"/v1/admin/mcp-connections/{connection_id}",
            headers=operator,
            json={"decision": "approve", "reason": "Approved trusted shop automation"},
        )
        assert approved.status_code == 200, approved.text

        private_origin = client.post(
            f"/v1/settings/mcp-connections/{connection_id}/home-assistant/connect",
            headers=owner,
            json={
                "base_url": "https://192.168.1.10",
                "access_token": access_token,
            },
        )
        assert private_origin.status_code == 422

        connected = client.post(
            f"/v1/settings/mcp-connections/{connection_id}/home-assistant/connect",
            headers=owner,
            json={
                "base_url": "https://shop-automation.example.com",
                "access_token": access_token,
            },
        )
        assert connected.status_code == 200, connected.text
        assert connected.json()["status"] == "active"
        assert access_token not in connected.text
        assert "shop-automation.example.com" not in connected.text

        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            connection = db.get(McpConnection, connection_id)
            assert connection is not None
            assert connection.encrypted_credentials
            assert access_token not in connection.encrypted_credentials
            assert "shop-automation.example.com" not in connection.encrypted_credentials
            actions = set(
                db.scalars(
                    select(AuditLog.action).where(AuditLog.resource_id == connection_id)
                ).all()
            )
            assert "mcp.home_assistant.connected" in actions
    finally:
        _remove_provider(provider)


def test_oauth_state_callback_encrypts_tokens_and_rejects_replay(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import settings as settings_route

    provider = "gmail"
    _remove_provider(provider)
    configured = Settings(
        app_env="test",
        mcp_google_oauth_enabled=True,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret=secrets.token_urlsafe(24),
    )
    app.dependency_overrides[get_settings] = lambda: configured
    owner = auth_headers(client, "+2348012345678")
    operator = auth_headers(client, "+2348099990001")
    try:
        requested = client.post(
            "/v1/settings/mcp-connections",
            headers=owner,
            json={"provider": provider},
        )
        connection_id = requested.json()["id"]
        approved = client.patch(
            f"/v1/admin/mcp-connections/{connection_id}",
            headers=operator,
            json={"decision": "approve", "reason": "Approved read-only connection"},
        )
        assert approved.status_code == 200
        started = client.post(
            f"/v1/settings/mcp-connections/{connection_id}/oauth/start",
            headers=owner,
        )
        assert started.status_code == 200, started.text
        authorization_url = started.json()["authorization_url"]
        parsed = urlsplit(authorization_url)
        query = parse_qs(parsed.query)
        assert parsed.hostname == "accounts.google.com"
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == [
            "http://bumpabestie.localhost:8080/api/backend/settings/mcp-oauth/callback"
        ]
        state = query["state"][0]
        decoded = decode_oauth_state(state, configured)
        assert decoded.connection_id == connection_id

        monkeypatch.setattr(
            settings_route,
            "exchange_authorization_code",
            lambda **_kwargs: {
                "access_token": "access-token-not-returned-to-browser",
                "refresh_token": "refresh-token-not-returned-to-browser",
                "token_type": "Bearer",
            },
        )
        callback = client.get(
            "/v1/settings/mcp-oauth/callback",
            params={"state": state, "code": "one-time-code"},
            headers=owner,
            follow_redirects=False,
        )
        assert callback.status_code == 303, callback.text
        assert callback.headers["location"].endswith("/settings/mcp?oauth=success")
        assert callback.headers["referrer-policy"] == "no-referrer"
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            connection = db.get(McpConnection, connection_id)
            assert connection is not None
            assert connection.status == "active"
            assert connection.encrypted_credentials is not None
            assert "access-token" not in connection.encrypted_credentials
            token_bundle = json.loads(
                FieldCipher(configured.field_encryption_key).decrypt(
                    connection.encrypted_credentials
                )
            )
            assert token_bundle["access_token"].startswith("access-token")

        replay = client.get(
            "/v1/settings/mcp-oauth/callback",
            params={"state": state, "code": "replayed-code"},
            headers=owner,
            follow_redirects=False,
        )
        assert replay.status_code == 409
    finally:
        app.dependency_overrides.pop(get_settings, None)
        _remove_provider(provider)


def test_oauth_exchange_is_fixed_origin_bounded_and_sanitized() -> None:
    configured = Settings(
        app_env="test",
        mcp_google_oauth_enabled=True,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret=secrets.token_urlsafe(24),
        mcp_oauth_max_response_bytes=4096,
    )
    authorization_url, _expires = build_authorization_url(
        settings=configured,
        connection_id="connection-id",
        tenant_id="tenant-id",
        user_id="user-id",
        provider="google_drive",
        read_only=True,
    )
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]
    assert decode_oauth_state(state, configured).provider == "google_drive"
    assert parse_qs(urlsplit(authorization_url).query)["redirect_uri"] == [
        configured.mcp_oauth_callback_url
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://oauth2.googleapis.com/token")
        assert request.method == "POST"
        assert parse_qs(request.content.decode())["redirect_uri"] == [
            configured.mcp_oauth_callback_url
        ]
        return httpx.Response(200, json={"access_token": "provider-access-token"})

    bundle = exchange_authorization_code(
        settings=configured,
        provider="google_drive",
        code="one-time-code",
        verifier=decode_oauth_state(state, configured).verifier,
        transport=httpx.MockTransport(handler),
    )
    assert bundle == {"access_token": "provider-access-token"}

    encrypted = FieldCipher(configured.field_encryption_key).encrypt(
        json.dumps({"access_token": "provider-access-token"})
    )

    def revoke_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://oauth2.googleapis.com/revoke")
        assert b"provider-access-token" in request.content
        return httpx.Response(200)

    assert revoke_oauth_token(
        settings=configured,
        provider="google_drive",
        encrypted_credentials=encrypted,
        transport=httpx.MockTransport(revoke_handler),
    )

    oversized = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=b"x" * 4097,
            headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(McpOAuthError, match="invalid response"):
        exchange_authorization_code(
            settings=configured,
            provider="google_drive",
            code="one-time-code",
            verifier="v" * 64,
            transport=oversized,
        )

    class ChunkedOversizedBody(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"x" * 2048
            yield b"x" * 2049

    chunked_oversized = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            stream=ChunkedOversizedBody(),
            headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(McpOAuthError, match="invalid response"):
        exchange_authorization_code(
            settings=configured,
            provider="google_drive",
            code="one-time-code",
            verifier="v" * 64,
            transport=chunked_oversized,
        )


def test_oauth_state_remains_readable_during_old_key_ttl_grace() -> None:
    old_secret = "old-oauth-field-key-material-" + "o" * 24
    new_secret = "new-oauth-field-key-material-" + "n" * 24
    old_writer = Settings(
        app_env="test",
        field_encryption_key=old_secret,
        field_encryption_key_id="old-2026-01",
        field_encryption_write_version="v2",
        mcp_google_oauth_enabled=True,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret=secrets.token_urlsafe(24),
    )
    authorization_url, _expires = build_authorization_url(
        settings=old_writer,
        connection_id="connection-id",
        tenant_id="tenant-id",
        user_id="user-id",
        provider="google_drive",
        read_only=True,
    )
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]

    during_grace = old_writer.model_copy(
        update={
            "field_encryption_key": new_secret,
            "field_encryption_key_id": "current-2026-07",
            "field_encryption_old_keys": {"old-2026-01": old_secret},
        }
    )
    assert decode_oauth_state(state, during_grace).connection_id == "connection-id"

    after_grace = during_grace.model_copy(update={"field_encryption_old_keys": {}})
    with pytest.raises(McpOAuthError, match="invalid or expired"):
        decode_oauth_state(state, after_grace)


def test_connector_registry_permissions_and_google_refresh_are_bounded() -> None:
    configured = Settings(
        app_env="test",
        field_encryption_key="oauth-refresh-field-key-material-0001",
        mcp_google_oauth_enabled=True,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret=secrets.token_urlsafe(24),
    )
    rows = registry(configured)
    assert {row["provider"] for row in rows} == {
        "google_drive",
        "google_sheets",
        "gmail",
        "calendar",
        "meta_ads",
        "home_assistant",
    }
    assert next(row for row in rows if row["provider"] == "gmail")["enabled"] is True
    assert next(row for row in rows if row["provider"] == "meta_ads")["enabled"] is False
    home_assistant = next(row for row in rows if row["provider"] == "home_assistant")
    assert home_assistant["enabled"] is True
    assert home_assistant["connection_method"] == "manual_token"
    assert any(
        scope.endswith("/gmail.send") for scope in connection_scopes("gmail", read_only=False)
    )
    assert default_permissions("gmail", read_only=True) == {
        "search_messages": "read",
        "read_message": "read",
    }
    assert default_permissions("gmail", read_only=False)["send_message"] == "deny"
    validate_tool_permission("gmail", "read_message", "read", read_only=True)
    validate_tool_permission(
        "gmail",
        "send_message",
        "write_with_confirmation",
        read_only=False,
    )
    with pytest.raises(ValueError, match="approved provider"):
        validate_tool_permission("gmail", "shell", "read", read_only=True)
    with pytest.raises(ValueError, match="Write tools"):
        validate_tool_permission("gmail", "send_message", "read", read_only=False)
    with pytest.raises(ValueError, match="Read tools"):
        validate_tool_permission(
            "gmail",
            "read_message",
            "write_with_confirmation",
            read_only=False,
        )
    with pytest.raises(ValueError, match="read-only"):
        validate_tool_permission(
            "gmail",
            "send_message",
            "write_with_confirmation",
            read_only=True,
        )

    captured: list[httpx.Request] = []

    def success(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "refreshed-access-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "scope-a scope-b",
            },
        )

    original = {
        "access_token": "old-access-token",
        "refresh_token": "persistent-refresh-token",
        "custom": "preserved",
    }
    refreshed = refresh_access_token(
        settings=configured,
        provider="gmail",
        credential_bundle=original,
        transport=httpx.MockTransport(success),
    )
    assert captured[0].url == httpx.URL("https://oauth2.googleapis.com/token")
    form = parse_qs(captured[0].content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["persistent-refresh-token"]
    assert refreshed["access_token"] == "refreshed-access-token"
    assert refreshed["refresh_token"] == "persistent-refresh-token"
    assert refreshed["custom"] == "preserved"
    assert refreshed["expires_in"] == 3600
    assert refreshed["token_type"] == "Bearer"


@pytest.mark.parametrize(
    ("response", "match"),
    (
        (httpx.Response(401), "expired"),
        (httpx.Response(200, text="not-json"), "invalid response"),
        (httpx.Response(200, json=["not", "a", "mapping"]), "invalid response"),
        (httpx.Response(200, json={"access_token": "short"}), "invalid response"),
    ),
)
def test_google_refresh_rejects_provider_failures(
    response: httpx.Response,
    match: str,
) -> None:
    configured = Settings(
        app_env="test",
        mcp_google_oauth_enabled=True,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret=secrets.token_urlsafe(24),
    )
    with pytest.raises(McpOAuthError, match=match):
        refresh_access_token(
            settings=configured,
            provider="google_drive",
            credential_bundle={"refresh_token": "persistent-refresh-token"},
            transport=httpx.MockTransport(lambda _request: response),
        )

    with pytest.raises(McpOAuthError, match="reconnect"):
        refresh_access_token(
            settings=configured,
            provider="meta_ads",
            credential_bundle={"refresh_token": "persistent-refresh-token"},
        )


def test_google_refresh_maps_transport_failure_without_exposing_details() -> None:
    configured = Settings(
        app_env="test",
        mcp_google_oauth_enabled=True,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret=secrets.token_urlsafe(24),
    )

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-provider-detail", request=request)

    with pytest.raises(McpOAuthError, match="unavailable") as raised:
        refresh_access_token(
            settings=configured,
            provider="google_sheets",
            credential_bundle={"refresh_token": "persistent-refresh-token"},
            transport=httpx.MockTransport(unavailable),
        )
    assert "private-provider-detail" not in str(raised.value)
