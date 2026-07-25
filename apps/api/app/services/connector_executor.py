from __future__ import annotations

import base64
import json
import re
import time
from email.message import EmailMessage
from typing import Any, cast
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.db.models import McpConnection
from app.providers.redaction import redact_text
from app.schemas import McpProvider
from app.services.mcp_oauth import McpOAuthError, refresh_access_token
from app.services.mcp_permissions import authorize_mcp_tool

GOOGLE_DRIVE = "https://www.googleapis.com"
GOOGLE_SHEETS = "https://sheets.googleapis.com"
GOOGLE_GMAIL = "https://gmail.googleapis.com"
GOOGLE_CALENDAR = "https://www.googleapis.com"
META_GRAPH = "https://graph.facebook.com"


class ConnectorExecutionError(RuntimeError):
    """A bounded connector failure safe for an assistant or API response."""


def execute_connector_read(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    connection_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    connection = authorize_mcp_tool(
        db,
        tenant_id=tenant_id,
        connection_id=connection_id,
        tool_name=tool_name,
    )
    return _execute(
        db,
        settings,
        connection=connection,
        tool_name=tool_name,
        tool_input=tool_input,
        write=False,
        transport=transport,
    )


def execute_connector_write(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    connection_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    idempotency_key: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    connection = authorize_mcp_tool(
        db,
        tenant_id=tenant_id,
        connection_id=connection_id,
        tool_name=tool_name,
        write_confirmed=True,
    )
    return _execute(
        db,
        settings,
        connection=connection,
        tool_name=tool_name,
        tool_input=tool_input,
        write=True,
        idempotency_key=idempotency_key,
        transport=transport,
    )


def _execute(
    db: Session,
    settings: Settings,
    *,
    connection: McpConnection,
    tool_name: str,
    tool_input: dict[str, Any],
    write: bool,
    idempotency_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    token = _access_token(db, connection, settings, transport=transport)
    resources = set(connection.allowed_resources)
    timeout = httpx.Timeout(settings.mcp_oauth_request_timeout_seconds)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        if connection.provider == "google_drive":
            return _drive(client, token, resources, tool_name, tool_input, idempotency_key)
        if connection.provider == "google_sheets":
            return _sheets(client, token, resources, tool_name, tool_input)
        if connection.provider == "gmail":
            return _gmail(client, token, resources, tool_name, tool_input, idempotency_key)
        if connection.provider == "calendar":
            return _calendar(client, token, resources, tool_name, tool_input, idempotency_key)
        if connection.provider == "meta_ads":
            return _meta_ads(
                client,
                token,
                resources,
                tool_name,
                tool_input,
                settings.meta_graph_version,
            )
    kind = "write" if write else "read"
    raise ConnectorExecutionError(f"The configured connector does not support this {kind}.")


def _drive(
    client: httpx.Client,
    token: str,
    resources: set[str],
    tool_name: str,
    values: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    if tool_name == "search_files":
        query = _string(values, "query", maximum=200).casefold()
        rows: list[dict[str, Any]] = []
        for resource in sorted(resources):
            if not resource.startswith("drive:file:"):
                continue
            file_id = resource.removeprefix("drive:file:")
            payload = _json(
                client,
                "GET",
                f"{GOOGLE_DRIVE}/drive/v3/files/{quote(file_id, safe='')}",
                token,
                params={"fields": "id,name,mimeType,modifiedTime,size,trashed"},
            )
            if payload.get("trashed") is True:
                continue
            name = str(payload.get("name", ""))
            if query in name.casefold():
                rows.append(
                    {
                        "id": str(payload.get("id", ""))[:200],
                        "name": name[:500],
                        "mime_type": str(payload.get("mimeType", ""))[:160],
                        "modified_at": payload.get("modifiedTime"),
                        "size": payload.get("size"),
                    }
                )
            if len(rows) >= 20:
                break
        return {"files": rows, "count": len(rows), "coverage_warning": None}
    if tool_name == "read_file":
        file_id = _string(values, "file_id", maximum=200)
        _require_resource(resources, f"drive:file:{file_id}")
        metadata = _json(
            client,
            "GET",
            f"{GOOGLE_DRIVE}/drive/v3/files/{quote(file_id, safe='')}",
            token,
            params={"fields": "id,name,mimeType,modifiedTime,size,trashed"},
        )
        mime_type = str(metadata.get("mimeType", ""))
        if mime_type.startswith("application/vnd.google-apps."):
            content = _bytes(
                client,
                "GET",
                f"{GOOGLE_DRIVE}/drive/v3/files/{quote(file_id, safe='')}/export",
                token,
                params={"mimeType": "text/plain"},
            )
        else:
            content = _bytes(
                client,
                "GET",
                f"{GOOGLE_DRIVE}/drive/v3/files/{quote(file_id, safe='')}",
                token,
                params={"alt": "media"},
            )
        return {
            "file": {
                "id": file_id,
                "name": str(metadata.get("name", ""))[:500],
                "mime_type": mime_type[:160],
                "modified_at": metadata.get("modifiedTime"),
                "text": content.decode("utf-8", errors="replace")[:20_000],
            },
            "untrusted_content": True,
        }
    if tool_name == "create_file":
        folder_id = _string(values, "folder_id", maximum=200)
        _require_resource(resources, f"drive:folder:{folder_id}")
        name = _string(values, "name", maximum=240)
        file_content = _string(values, "content", maximum=100_000)
        mime_type = str(values.get("mime_type") or "text/plain")[:160]
        marker = (idempotency_key or "")[:120]
        existing = _json(
            client,
            "GET",
            f"{GOOGLE_DRIVE}/drive/v3/files",
            token,
            params={
                "q": f"appProperties has {{ key='bumpa_action' and value='{marker}' }} "
                "and trashed=false",
                "fields": "files(id,name,mimeType)",
                "pageSize": "2",
            },
        )
        files = existing.get("files")
        if isinstance(files, list) and files:
            return {"created": False, "idempotent_replay": True, "file": files[0]}
        response = _json(
            client,
            "POST",
            f"{GOOGLE_DRIVE}/upload/drive/v3/files",
            token,
            params={"uploadType": "multipart", "fields": "id,name,mimeType,webViewLink"},
            files={
                "metadata": (
                    None,
                    json.dumps(
                        {
                            "name": name,
                            "parents": [folder_id],
                            "appProperties": {"bumpa_action": marker},
                        }
                    ),
                    "application/json",
                ),
                "media": (name, file_content.encode(), mime_type),
            },
        )
        return {"created": True, "file": response}
    raise ConnectorExecutionError("This Google Drive tool is not supported.")


def _sheets(
    client: httpx.Client,
    token: str,
    resources: set[str],
    tool_name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    spreadsheet_id = _string(values, "spreadsheet_id", maximum=200)
    _require_resource(resources, f"sheets:spreadsheet:{spreadsheet_id}")
    cell_range = _string(values, "range", maximum=300)
    endpoint = (
        f"{GOOGLE_SHEETS}/v4/spreadsheets/{quote(spreadsheet_id, safe='')}/values/"
        f"{quote(cell_range, safe='!:$')}"
    )
    if tool_name == "read_sheet":
        response = _json(client, "GET", endpoint, token)
        rows = response.get("values")
        bounded = (
            [[str(cell)[:1000] for cell in row[:50]] for row in rows[:200] if isinstance(row, list)]
            if isinstance(rows, list)
            else []
        )
        return {
            "spreadsheet_id": spreadsheet_id,
            "range": response.get("range") or cell_range,
            "rows": bounded,
            "truncated": isinstance(rows, list) and len(rows) > len(bounded),
            "untrusted_content": True,
        }
    if tool_name == "append_rows":
        rows = values.get("rows")
        if (
            not isinstance(rows, list)
            or not rows
            or len(rows) > 100
            or any(not isinstance(row, list) or len(row) > 50 for row in rows)
        ):
            raise ConnectorExecutionError("Spreadsheet rows are missing or exceed safe limits.")
        bounded = [[str(cell)[:1000] for cell in row] for row in rows]
        response = _json(
            client,
            "POST",
            endpoint + ":append",
            token,
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json_body={"majorDimension": "ROWS", "values": bounded},
        )
        updates: dict[str, Any] = (
            response["updates"] if isinstance(response.get("updates"), dict) else {}
        )
        return {
            "updated_range": updates.get("updatedRange"),
            "updated_rows": updates.get("updatedRows"),
            "updated_cells": updates.get("updatedCells"),
        }
    raise ConnectorExecutionError("This Google Sheets tool is not supported.")


def _gmail(
    client: httpx.Client,
    token: str,
    resources: set[str],
    tool_name: str,
    values: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    labels = sorted(
        item.removeprefix("gmail:label:") for item in resources if item.startswith("gmail:label:")
    )
    if tool_name == "search_messages":
        if not labels:
            raise ConnectorExecutionError("No Gmail labels are allowlisted.")
        query = _string(values, "query", maximum=200)
        seen: set[str] = set()
        messages: list[dict[str, Any]] = []
        for label in labels:
            response = _json(
                client,
                "GET",
                f"{GOOGLE_GMAIL}/gmail/v1/users/me/messages",
                token,
                params={"q": query, "labelIds": label, "maxResults": "10"},
            )
            for row in response.get("messages", []):
                if (
                    isinstance(row, dict)
                    and isinstance(row.get("id"), str)
                    and row["id"] not in seen
                ):
                    seen.add(row["id"])
                    messages.append(
                        {"id": row["id"], "thread_id": row.get("threadId"), "label": label}
                    )
                    if len(messages) >= 20:
                        break
        return {"messages": messages, "count": len(messages), "content_included": False}
    if tool_name == "read_message":
        message_id = _string(values, "message_id", maximum=200)
        response = _json(
            client,
            "GET",
            f"{GOOGLE_GMAIL}/gmail/v1/users/me/messages/{quote(message_id, safe='')}",
            token,
            params={"format": "full"},
        )
        message_labels = set(str(value) for value in response.get("labelIds", []))
        if not message_labels.intersection(labels):
            raise ConnectorExecutionError("The Gmail message is outside the tenant allowlist.")
        payload: dict[str, Any] = (
            response["payload"] if isinstance(response.get("payload"), dict) else {}
        )
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in payload.get("headers", [])
            if isinstance(item, dict)
        }
        return {
            "message": {
                "id": message_id,
                "thread_id": response.get("threadId"),
                "subject": redact_text(headers.get("subject", ""))[:500],
                "from": redact_text(headers.get("from", ""))[:500],
                "date": headers.get("date", "")[:200],
                "body": redact_text(_gmail_text(payload))[:20_000],
            },
            "untrusted_content": True,
        }
    if tool_name == "send_message":
        to = _string(values, "to", maximum=320)
        domain = to.rsplit("@", 1)[-1].lower() if "@" in to else ""
        _require_resource(resources, f"gmail:recipient-domain:{domain}")
        subject = _string(values, "subject", maximum=500)
        body = _string(values, "body", maximum=50_000)
        email = EmailMessage()
        email["To"] = to
        email["Subject"] = subject
        email["Message-ID"] = f"<{idempotency_key}@actions.bumpabestie.internal>"
        email.set_content(body)
        encoded = base64.urlsafe_b64encode(email.as_bytes()).decode().rstrip("=")
        response = _json(
            client,
            "POST",
            f"{GOOGLE_GMAIL}/gmail/v1/users/me/messages/send",
            token,
            json_body={"raw": encoded},
        )
        return {
            "sent": True,
            "message_id": response.get("id"),
            "thread_id": response.get("threadId"),
        }
    raise ConnectorExecutionError("This Gmail tool is not supported.")


def _calendar(
    client: httpx.Client,
    token: str,
    resources: set[str],
    tool_name: str,
    values: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    calendar_id = _string(values, "calendar_id", maximum=200)
    _require_resource(resources, f"calendar:calendar:{calendar_id}")
    endpoint = f"{GOOGLE_CALENDAR}/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
    if tool_name == "list_events":
        response = _json(
            client,
            "GET",
            endpoint,
            token,
            params={
                "timeMin": _string(values, "time_min", maximum=50),
                "timeMax": _string(values, "time_max", maximum=50),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "50",
            },
        )
        items: list[Any] = response["items"] if isinstance(response.get("items"), list) else []
        return {
            "events": [
                {
                    "id": str(item.get("id", ""))[:200],
                    "summary": str(item.get("summary", ""))[:500],
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "status": item.get("status"),
                }
                for item in items[:50]
                if isinstance(item, dict)
            ],
            "untrusted_content": True,
        }
    if tool_name == "create_event":
        summary = _string(values, "summary", maximum=500)
        start = _date_time_object(values.get("start"), "start")
        end = _date_time_object(values.get("end"), "end")
        event_id = re.sub(r"[^a-v0-9]", "", (idempotency_key or "").lower())[:64]
        body: dict[str, Any] = {
            "id": event_id,
            "summary": summary,
            "start": start,
            "end": end,
        }
        description = values.get("description")
        if isinstance(description, str) and description.strip():
            body["description"] = description.strip()[:10_000]
        response = _json(client, "POST", endpoint, token, json_body=body)
        return {
            "created": True,
            "event_id": response.get("id"),
            "html_link": response.get("htmlLink"),
            "status": response.get("status"),
        }
    raise ConnectorExecutionError("This Google Calendar tool is not supported.")


def _meta_ads(
    client: httpx.Client,
    token: str,
    resources: set[str],
    tool_name: str,
    values: dict[str, Any],
    graph_version: str,
) -> dict[str, Any]:
    account_id = _string(values, "ad_account_id", maximum=80)
    _require_resource(resources, f"meta_ads:account:{account_id}")
    base = f"{META_GRAPH}/{graph_version}"
    if tool_name == "read_campaigns":
        response = _json(
            client,
            "GET",
            f"{base}/{quote(account_id, safe='')}/campaigns",
            token,
            params={
                "fields": "id,name,status,effective_status,objective,updated_time",
                "limit": "50",
            },
        )
        rows: list[Any] = response["data"] if isinstance(response.get("data"), list) else []
        return {"campaigns": rows[:50], "count": len(rows[:50]), "untrusted_content": True}
    if tool_name == "update_campaign_status":
        campaign_id = _string(values, "campaign_id", maximum=80)
        status = _string(values, "status", maximum=20).upper()
        if status not in {"ACTIVE", "PAUSED"}:
            raise ConnectorExecutionError("Campaign status must be ACTIVE or PAUSED.")
        metadata = _json(
            client,
            "GET",
            f"{base}/{quote(campaign_id, safe='')}",
            token,
            params={"fields": "id,account_id,status"},
        )
        actual_account = str(metadata.get("account_id", ""))
        if actual_account and account_id.removeprefix("act_") != actual_account.removeprefix(
            "act_"
        ):
            raise ConnectorExecutionError("The campaign is outside the allowlisted ad account.")
        response = _json(
            client,
            "POST",
            f"{base}/{quote(campaign_id, safe='')}",
            token,
            json_body={"status": status},
        )
        return {
            "updated": response.get("success") is True,
            "campaign_id": campaign_id,
            "status": status,
        }
    raise ConnectorExecutionError("This Meta Ads tool is not supported.")


def _access_token(
    db: Session,
    connection: McpConnection,
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None,
) -> str:
    try:
        bundle = json.loads(
            FieldCipher.from_settings(settings).decrypt(connection.encrypted_credentials or "")
        )
        token = bundle["access_token"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConnectorExecutionError("The connector credentials are unavailable.") from exc
    if not isinstance(token, str) or not 8 <= len(token) <= 16_384:
        raise ConnectorExecutionError("The connector credentials are unavailable.")
    expires_in = bundle.get("expires_in")
    obtained_at = bundle.get("obtained_at")
    if (
        isinstance(expires_in, int)
        and isinstance(obtained_at, int)
        and obtained_at + expires_in <= int(time.time()) + 60
    ):
        try:
            bundle = refresh_access_token(
                settings=settings,
                provider=cast(McpProvider, connection.provider),
                credential_bundle=bundle,
                transport=transport,
            )
        except McpOAuthError as exc:
            connection.status = "error"
            db.commit()
            raise ConnectorExecutionError(str(exc)) from exc
        connection.encrypted_credentials = FieldCipher.from_settings(settings).encrypt(
            json.dumps(bundle, separators=(",", ":"), sort_keys=True)
        )
        connection.status = "active"
        db.commit()
        token = str(bundle["access_token"])
    return token


def _require_resource(resources: set[str], expected: str) -> None:
    if expected not in resources:
        raise ConnectorExecutionError("The requested resource is outside the tenant allowlist.")


def _string(values: dict[str, Any], key: str, *, maximum: int) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ConnectorExecutionError(f"Connector input {key} is missing or invalid.")
    return value.strip()


def _date_time_object(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConnectorExecutionError(f"Calendar {label} is missing.")
    if isinstance(value.get("dateTime"), str):
        result = {"dateTime": value["dateTime"][:50]}
        if isinstance(value.get("timeZone"), str):
            result["timeZone"] = value["timeZone"][:80]
        return result
    if isinstance(value.get("date"), str):
        return {"date": value["date"][:20]}
    raise ConnectorExecutionError(f"Calendar {label} is invalid.")


def _gmail_text(payload: dict[str, Any]) -> str:
    mime_type = payload.get("mimeType")
    body: dict[str, Any] = payload["body"] if isinstance(payload.get("body"), dict) else {}
    data = body.get("data")
    if mime_type == "text/plain" and isinstance(data, str):
        try:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
                "utf-8", errors="replace"
            )
        except ValueError:
            return ""
    parts = payload.get("parts")
    if isinstance(parts, list):
        return "\n".join(_gmail_text(part) for part in parts if isinstance(part, dict))
    return ""


def _json(
    client: httpx.Client,
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = _request(
        client,
        method,
        url,
        token,
        params=params,
        json_body=json_body,
        files=files,
    )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorExecutionError("The connector returned invalid data.") from exc
    if not isinstance(payload, dict):
        raise ConnectorExecutionError("The connector returned invalid data.")
    return payload


def _bytes(
    client: httpx.Client,
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, str] | None = None,
) -> bytes:
    return _request(client, method, url, token, params=params)


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> bytes:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        with client.stream(
            method,
            url,
            params=params,
            json=json_body,
            files=files,
            headers=headers,
        ) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > 1_048_576:
                    raise ConnectorExecutionError("The connector response exceeded its safe limit.")
                content.extend(chunk)
            if response.status_code in {401, 403}:
                raise ConnectorExecutionError("The connector authorization is no longer valid.")
            if response.status_code == 429:
                raise ConnectorExecutionError("The connector is temporarily rate limited.")
            if response.status_code >= 500:
                raise ConnectorExecutionError("The connector is temporarily unavailable.")
            if not response.is_success:
                raise ConnectorExecutionError("The connector rejected the requested operation.")
    except ConnectorExecutionError:
        raise
    except httpx.HTTPError as exc:
        raise ConnectorExecutionError("The connector is temporarily unavailable.") from exc
    return bytes(content)
