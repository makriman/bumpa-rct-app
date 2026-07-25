from __future__ import annotations

import base64
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models import McpConnection, McpToolPermission, Tenant, User
from app.services import connector_executor as connectors
from app.services.home_assistant import (
    HomeAssistantConnectionError,
    encrypt_home_assistant_credentials,
    validate_home_assistant_base_url,
)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, follow_redirects=False, trust_env=False)


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_connector_registry_dispatches_only_server_authorized_reads_and_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    with factory() as db:
        tenant = Tenant(slug="connector-dispatch", name="Connector Dispatch")
        owner = User(primary_phone_e164="+2348000000066")
        db.add_all((tenant, owner))
        db.flush()
        connection = McpConnection(
            tenant_id=tenant.id,
            created_by=owner.id,
            provider="google_sheets",
            status="active",
            encrypted_credentials="encrypted",
            read_only=False,
            admin_approved=True,
            allowed_resources=["sheets:spreadsheet:sheet-1"],
        )
        db.add(connection)
        db.flush()
        db.add_all(
            (
                McpToolPermission(
                    tenant_id=tenant.id,
                    mcp_connection_id=connection.id,
                    tool_name="read_sheet",
                    permission="read",
                    created_by=owner.id,
                ),
                McpToolPermission(
                    tenant_id=tenant.id,
                    mcp_connection_id=connection.id,
                    tool_name="append_rows",
                    permission="write_with_confirmation",
                    created_by=owner.id,
                ),
            )
        )
        db.commit()
        monkeypatch.setattr(
            connectors,
            "_access_token",
            lambda *_args, **_kwargs: "access-token",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(":append"):
                return httpx.Response(200, json={"updates": {"updatedRows": 1}})
            return httpx.Response(200, json={"values": [["sales"], [2500]]})

        transport = httpx.MockTransport(handler)
        settings = Settings(field_encryption_key="connector-dispatch-key")
        read = connectors.execute_connector_read(
            db,
            settings,
            tenant_id=tenant.id,
            connection_id=connection.id,
            tool_name="read_sheet",
            tool_input={"spreadsheet_id": "sheet-1", "range": "Sales!A:A"},
            transport=transport,
        )
        written = connectors.execute_connector_write(
            db,
            settings,
            tenant_id=tenant.id,
            connection_id=connection.id,
            tool_name="append_rows",
            tool_input={
                "spreadsheet_id": "sheet-1",
                "range": "Sales!A:A",
                "rows": [[2500]],
            },
            idempotency_key="confirmed-action",
            transport=transport,
        )
        assert read["rows"][1] == ["2500"]
        assert written["updated_rows"] == 1


@pytest.mark.parametrize(
    "value",
    [
        "http://assistant.example.com",
        "https://127.0.0.1",
        "https://192.168.1.20",
        "https://assistant.local",
        "https://assistant.example.com:8123",
        "https://assistant.example.com/api",
        "https://user:password@assistant.example.com",
    ],
)
def test_home_assistant_requires_an_explicit_public_https_origin(value: str) -> None:
    with pytest.raises(HomeAssistantConnectionError):
        validate_home_assistant_base_url(value)
    assert (
        validate_home_assistant_base_url("https://Shop-Automation.Example.com/")
        == "https://shop-automation.example.com"
    )


def test_home_assistant_reads_and_confirmed_writes_are_tenant_allowlisted() -> None:
    factory = _factory()
    settings = Settings(
        app_env="test",
        field_encryption_key="home-assistant-connector-test-key",
    )
    calls: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/services":
            return httpx.Response(
                200,
                json=[
                    {
                        "domain": "light",
                        "services": {"turn_on": {}, "turn_off": {}},
                    }
                ],
            )
        if request.method == "POST":
            assert request.url.path == "/api/services/light/turn_on"
            assert json.loads(request.content)["entity_id"] == "light.shop"
            return httpx.Response(200, json=[{"entity_id": "light.shop"}])
        entity_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "entity_id": entity_id,
                "state": "on",
                "attributes": {
                    "friendly_name": "Shop light",
                    "private_blob": "must not be returned",
                },
                "last_changed": "2026-07-25T10:00:00Z",
                "last_updated": "2026-07-25T10:00:00Z",
            },
        )

    with factory() as db:
        tenant = Tenant(slug="home-assistant", name="Home Assistant")
        owner = User(primary_phone_e164="+2348000000088")
        db.add_all((tenant, owner))
        db.flush()
        connection = McpConnection(
            tenant_id=tenant.id,
            created_by=owner.id,
            provider="home_assistant",
            status="active",
            encrypted_credentials=encrypt_home_assistant_credentials(
                settings,
                base_url="https://shop-automation.example.com",
                access_token="dedicated-home-assistant-test-token",  # noqa: S106
            ),
            read_only=False,
            admin_approved=True,
            allowed_resources=[
                "home_assistant:entity:light.shop",
                "home_assistant:service:light.turn_on",
            ],
        )
        db.add(connection)
        db.flush()
        for tool_name, permission in (
            ("list_entities", "read"),
            ("get_state", "read"),
            ("list_services", "read"),
            ("call_service", "write_with_confirmation"),
        ):
            db.add(
                McpToolPermission(
                    tenant_id=tenant.id,
                    mcp_connection_id=connection.id,
                    tool_name=tool_name,
                    permission=permission,
                    created_by=owner.id,
                )
            )
        db.commit()
        transport = httpx.MockTransport(handler)

        entities = connectors.execute_connector_read(
            db,
            settings,
            tenant_id=tenant.id,
            connection_id=connection.id,
            tool_name="list_entities",
            tool_input={},
            transport=transport,
        )
        services = connectors.execute_connector_read(
            db,
            settings,
            tenant_id=tenant.id,
            connection_id=connection.id,
            tool_name="list_services",
            tool_input={},
            transport=transport,
        )
        called = connectors.execute_connector_write(
            db,
            settings,
            tenant_id=tenant.id,
            connection_id=connection.id,
            tool_name="call_service",
            tool_input={
                "service": "light.turn_on",
                "entity_id": "light.shop",
                "data": {"brightness": 120},
            },
            idempotency_key="confirmed-home-assistant-call",
            transport=transport,
        )

        assert entities["entities"] == [
            {
                "entity_id": "light.shop",
                "state": "on",
                "friendly_name": "Shop light",
                "last_changed": "2026-07-25T10:00:00Z",
                "last_updated": "2026-07-25T10:00:00Z",
            }
        ]
        assert services["services"] == ["light.turn_on"]
        assert called == {
            "called": True,
            "service": "light.turn_on",
            "entity_id": "light.shop",
            "result_count": 1,
        }
        assert all(call[2] == "Bearer dedicated-home-assistant-test-token" for call in calls)
        with pytest.raises(connectors.ConnectorExecutionError, match="allowlist"):
            connectors.execute_connector_read(
                db,
                settings,
                tenant_id=tenant.id,
                connection_id=connection.id,
                tool_name="get_state",
                tool_input={"entity_id": "lock.back_door"},
                transport=transport,
            )
        with pytest.raises(connectors.ConnectorExecutionError, match="service data"):
            connectors.execute_connector_write(
                db,
                settings,
                tenant_id=tenant.id,
                connection_id=connection.id,
                tool_name="call_service",
                tool_input={
                    "service": "light.turn_on",
                    "entity_id": "light.shop",
                    "data": {"entity_id": "lock.back_door"},
                },
                idempotency_key="confirmed-but-target-overridden",
                transport=transport,
            )


def test_google_drive_reads_exports_and_idempotent_writes_are_allowlisted() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/export"):
            return httpx.Response(200, content=b"Exported planning notes")
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"Plain file contents")
        if path == "/drive/v3/files":
            return httpx.Response(200, json={"files": []})
        if path.startswith("/upload/drive/v3/files"):
            return httpx.Response(
                200,
                json={"id": "created-1", "name": "plan.txt", "mimeType": "text/plain"},
            )
        file_id = path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "id": file_id,
                "name": "Ghana Expansion Plan" if file_id == "file-1" else "Ledger",
                "mimeType": (
                    "application/vnd.google-apps.document"
                    if file_id == "google-doc"
                    else "text/plain"
                ),
                "modifiedTime": "2026-07-25T00:00:00Z",
                "size": "42",
                "trashed": False,
            },
        )

    transport = httpx.MockTransport(handler)
    resources = {
        "drive:file:file-1",
        "drive:file:google-doc",
        "drive:folder:folder-1",
    }
    with _client(transport) as client:
        found = connectors._drive(
            client,
            "access-token",
            resources,
            "search_files",
            {"query": "ghana"},
            None,
        )
        plain = connectors._drive(
            client,
            "access-token",
            resources,
            "read_file",
            {"file_id": "file-1"},
            None,
        )
        exported = connectors._drive(
            client,
            "access-token",
            resources,
            "read_file",
            {"file_id": "google-doc"},
            None,
        )
        created = connectors._drive(
            client,
            "access-token",
            resources,
            "create_file",
            {
                "folder_id": "folder-1",
                "name": "plan.txt",
                "content": "Exact plan",
            },
            "action-marker",
        )

    assert found["count"] == 1
    assert plain["file"]["text"] == "Plain file contents"
    assert exported["file"]["text"] == "Exported planning notes"
    assert created["created"] is True
    assert ("POST", "/upload/drive/v3/files") in calls
    with _client(transport) as client:
        with pytest.raises(connectors.ConnectorExecutionError, match="allowlist"):
            connectors._drive(
                client,
                "access-token",
                resources,
                "read_file",
                {"file_id": "private-file"},
                None,
            )


def test_sheets_gmail_calendar_and_meta_reads_and_confirmed_writes() -> None:
    requests: list[httpx.Request] = []
    gmail_body = base64.urlsafe_b64encode(b"Customer email buyer@example.com").decode().rstrip("=")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if "sheets.googleapis.com" in request.url.host:
            if path.endswith(":append"):
                return httpx.Response(
                    200,
                    json={
                        "updates": {
                            "updatedRange": "Sales!A3:B3",
                            "updatedRows": 1,
                            "updatedCells": 2,
                        }
                    },
                )
            return httpx.Response(
                200,
                json={"range": "Sales!A1:B2", "values": [["Date", "Sales"], ["25", 4000]]},
            )
        if "gmail.googleapis.com" in request.url.host:
            if path.endswith("/messages/send"):
                return httpx.Response(200, json={"id": "sent-1", "threadId": "thread-1"})
            if path.endswith("/messages"):
                return httpx.Response(
                    200,
                    json={"messages": [{"id": "mail-1", "threadId": "thread-1"}]},
                )
            return httpx.Response(
                200,
                json={
                    "id": "mail-1",
                    "threadId": "thread-1",
                    "labelIds": ["Label_1"],
                    "payload": {
                        "mimeType": "multipart/alternative",
                        "headers": [
                            {"name": "Subject", "value": "Order for buyer@example.com"},
                            {"name": "From", "value": "buyer@example.com"},
                            {"name": "Date", "value": "Fri"},
                        ],
                        "parts": [{"mimeType": "text/plain", "body": {"data": gmail_body}}],
                    },
                },
            )
        if "/calendar/v3/" in path:
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "id": "event-1",
                        "htmlLink": "https://calendar.google.com/event",
                        "status": "confirmed",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "event-1",
                            "summary": "Supplier meeting",
                            "start": {"dateTime": "2026-07-26T10:00:00+01:00"},
                            "end": {"dateTime": "2026-07-26T11:00:00+01:00"},
                            "status": "confirmed",
                        }
                    ]
                },
            )
        if path.endswith("/campaigns"):
            return httpx.Response(
                200,
                json={"data": [{"id": "campaign-1", "name": "Summer sales", "status": "PAUSED"}]},
            )
        if request.method == "GET":
            return httpx.Response(200, json={"id": "campaign-1", "account_id": "123"})
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    with _client(transport) as client:
        sheet = connectors._sheets(
            client,
            "token",
            {"sheets:spreadsheet:sheet-1"},
            "read_sheet",
            {"spreadsheet_id": "sheet-1", "range": "Sales!A1:B2"},
        )
        appended = connectors._sheets(
            client,
            "token",
            {"sheets:spreadsheet:sheet-1"},
            "append_rows",
            {
                "spreadsheet_id": "sheet-1",
                "range": "Sales!A:B",
                "rows": [["2026-07-25", 4000]],
            },
        )
        searched = connectors._gmail(
            client,
            "token",
            {"gmail:label:Label_1", "gmail:recipient-domain:example.com"},
            "search_messages",
            {"query": "order"},
            None,
        )
        message = connectors._gmail(
            client,
            "token",
            {"gmail:label:Label_1"},
            "read_message",
            {"message_id": "mail-1"},
            None,
        )
        sent = connectors._gmail(
            client,
            "token",
            {"gmail:recipient-domain:example.com"},
            "send_message",
            {"to": "buyer@example.com", "subject": "Quote", "body": "NGN 25,000"},
            "confirmed-action",
        )
        events = connectors._calendar(
            client,
            "token",
            {"calendar:calendar:primary"},
            "list_events",
            {
                "calendar_id": "primary",
                "time_min": "2026-07-25T00:00:00Z",
                "time_max": "2026-08-01T00:00:00Z",
            },
            None,
        )
        created_event = connectors._calendar(
            client,
            "token",
            {"calendar:calendar:primary"},
            "create_event",
            {
                "calendar_id": "primary",
                "summary": "Supplier meeting",
                "description": "Discuss stock",
                "start": {"dateTime": "2026-07-26T10:00:00+01:00", "timeZone": "Africa/Lagos"},
                "end": {"dateTime": "2026-07-26T11:00:00+01:00", "timeZone": "Africa/Lagos"},
            },
            "abcdef0123456789",
        )
        campaigns = connectors._meta_ads(
            client,
            "token",
            {"meta_ads:account:act_123"},
            "read_campaigns",
            {"ad_account_id": "act_123"},
            "v25.0",
        )
        updated = connectors._meta_ads(
            client,
            "token",
            {"meta_ads:account:act_123"},
            "update_campaign_status",
            {
                "ad_account_id": "act_123",
                "campaign_id": "campaign-1",
                "status": "active",
            },
            "v25.0",
        )

    assert sheet["rows"][1] == ["25", "4000"]
    assert appended["updated_cells"] == 2
    assert searched["count"] == 1
    assert "[EMAIL]" in message["message"]["body"]
    assert sent["sent"] is True
    assert events["events"][0]["summary"] == "Supplier meeting"
    assert created_event["event_id"] == "event-1"
    assert campaigns["count"] == 1
    assert updated == {"updated": True, "campaign_id": "campaign-1", "status": "ACTIVE"}
    sent_request = next(request for request in requests if request.url.path.endswith("/send"))
    raw = json.loads(sent_request.content)["raw"]
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert b"confirmed-action@actions.bumpabestie.internal" in decoded


@pytest.mark.parametrize(
    ("status", "message"),
    (
        (401, "authorization"),
        (429, "rate limited"),
        (503, "unavailable"),
        (400, "rejected"),
    ),
)
def test_connector_failures_are_sanitized(status: int, message: str) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status, json={"secret": "x"}))
    with _client(transport) as client:
        with pytest.raises(connectors.ConnectorExecutionError, match=message):
            connectors._json(client, "GET", "https://www.googleapis.com/example", "token")

    assert connectors._date_time_object({"date": "2026-07-25"}, "start") == {"date": "2026-07-25"}
    with pytest.raises(connectors.ConnectorExecutionError, match="missing"):
        connectors._string({}, "required", maximum=10)
    with pytest.raises(connectors.ConnectorExecutionError, match="invalid"):
        connectors._date_time_object({}, "end")
