from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Literal, cast
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.schemas import McpProvider, McpToolPermissionValue

McpToolKind = Literal["read", "write"]


class McpOAuthError(RuntimeError):
    """A bounded OAuth failure whose message is safe to return to a user."""


@dataclass(frozen=True)
class McpTool:
    name: str
    label: str
    kind: McpToolKind


@dataclass(frozen=True)
class ProviderDefinition:
    provider: McpProvider
    name: str
    authorization_url: str
    exchange_url: str
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    tools: tuple[McpTool, ...]
    uses_pkce: bool


@dataclass(frozen=True)
class OAuthClient:
    definition: ProviderDefinition
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class OAuthState:
    connection_id: str
    tenant_id: str
    user_id: str
    provider: McpProvider
    verifier: str
    expires_at: int


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOCATION_URL = "https://oauth2.googleapis.com/revoke"

PROVIDERS: dict[McpProvider, ProviderDefinition] = {
    "google_drive": ProviderDefinition(
        provider="google_drive",
        name="Google Drive",
        authorization_url=GOOGLE_AUTHORIZATION_URL,
        exchange_url=GOOGLE_TOKEN_URL,
        read_scopes=("https://www.googleapis.com/auth/drive.readonly",),
        write_scopes=("https://www.googleapis.com/auth/drive.file",),
        tools=(
            McpTool("search_files", "Search approved files", "read"),
            McpTool("read_file", "Read an approved file", "read"),
            McpTool("create_file", "Create a file", "write"),
        ),
        uses_pkce=True,
    ),
    "google_sheets": ProviderDefinition(
        provider="google_sheets",
        name="Google Sheets",
        authorization_url=GOOGLE_AUTHORIZATION_URL,
        exchange_url=GOOGLE_TOKEN_URL,
        read_scopes=("https://www.googleapis.com/auth/spreadsheets.readonly",),
        write_scopes=("https://www.googleapis.com/auth/spreadsheets",),
        tools=(
            McpTool("read_sheet", "Read an approved spreadsheet", "read"),
            McpTool("append_rows", "Append spreadsheet rows", "write"),
        ),
        uses_pkce=True,
    ),
    "gmail": ProviderDefinition(
        provider="gmail",
        name="Gmail",
        authorization_url=GOOGLE_AUTHORIZATION_URL,
        exchange_url=GOOGLE_TOKEN_URL,
        read_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        write_scopes=("https://www.googleapis.com/auth/gmail.send",),
        tools=(
            McpTool("search_messages", "Search approved messages", "read"),
            McpTool("read_message", "Read an approved message", "read"),
            McpTool("send_message", "Send a message", "write"),
        ),
        uses_pkce=True,
    ),
    "calendar": ProviderDefinition(
        provider="calendar",
        name="Google Calendar",
        authorization_url=GOOGLE_AUTHORIZATION_URL,
        exchange_url=GOOGLE_TOKEN_URL,
        read_scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        write_scopes=("https://www.googleapis.com/auth/calendar.events",),
        tools=(
            McpTool("list_events", "List calendar events", "read"),
            McpTool("create_event", "Create a calendar event", "write"),
        ),
        uses_pkce=True,
    ),
    "meta_ads": ProviderDefinition(
        provider="meta_ads",
        name="Meta Ads",
        authorization_url="https://www.facebook.com/v23.0/dialog/oauth",
        exchange_url="https://graph.facebook.com/v23.0/oauth/access_token",
        read_scopes=("ads_read",),
        write_scopes=("ads_management",),
        tools=(
            McpTool("read_campaigns", "Read campaign performance", "read"),
            McpTool("update_campaign_status", "Change campaign status", "write"),
        ),
        uses_pkce=False,
    ),
}


def registry(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for definition in PROVIDERS.values():
        rows.append(
            {
                "provider": definition.provider,
                "name": definition.name,
                "enabled": oauth_client(settings, definition.provider) is not None,
                "default_mode": "read_only",
                "tools": [
                    {"name": tool.name, "label": tool.label, "kind": tool.kind}
                    for tool in definition.tools
                ],
            }
        )
    return rows


def oauth_client(settings: Settings, provider: McpProvider) -> OAuthClient | None:
    definition = PROVIDERS[provider]
    if provider == "meta_ads":
        if not settings.mcp_meta_ads_oauth_enabled:
            return None
        definition = replace(
            definition,
            authorization_url=(
                f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth"
            ),
            exchange_url=(
                f"https://graph.facebook.com/{settings.meta_graph_version}/oauth/access_token"
            ),
        )
        client_id = settings.meta_ads_oauth_client_id or ""
        client_secret = settings.effective_meta_ads_oauth_client_secret
    else:
        if not settings.mcp_google_oauth_enabled:
            return None
        client_id = settings.google_oauth_client_id or ""
        client_secret = settings.effective_google_oauth_client_secret
    if not client_id or not client_secret:
        return None
    return OAuthClient(definition, client_id, client_secret)


def connection_scopes(provider: McpProvider, *, read_only: bool) -> list[str]:
    definition = PROVIDERS[provider]
    scopes = list(definition.read_scopes)
    if not read_only:
        scopes.extend(definition.write_scopes)
    return scopes


def default_permissions(
    provider: McpProvider, *, read_only: bool
) -> dict[str, McpToolPermissionValue]:
    return {
        tool.name: ("read" if tool.kind == "read" else "deny")
        for tool in PROVIDERS[provider].tools
        if not read_only or tool.kind == "read"
    }


def validate_tool_permission(
    provider: McpProvider,
    tool_name: str,
    permission: McpToolPermissionValue,
    *,
    read_only: bool,
) -> None:
    tool = next((item for item in PROVIDERS[provider].tools if item.name == tool_name), None)
    if tool is None:
        raise ValueError("Tool is not in the approved provider registry")
    if permission == "read" and tool.kind != "read":
        raise ValueError("Write tools cannot be granted read permission")
    if permission == "write_with_confirmation":
        if tool.kind != "write":
            raise ValueError("Read tools cannot be granted write permission")
        if read_only:
            raise ValueError("This connection is restricted to read-only access")


def build_authorization_url(
    *,
    settings: Settings,
    connection_id: str,
    tenant_id: str,
    user_id: str,
    provider: McpProvider,
    read_only: bool,
) -> tuple[str, int]:
    client = oauth_client(settings, provider)
    if client is None:
        raise McpOAuthError("OAuth is not configured for this connector")
    verifier = secrets.token_urlsafe(64)
    expires_at = int(
        (utcnow() + timedelta(seconds=settings.mcp_oauth_state_ttl_seconds)).timestamp()
    )
    state = OAuthState(
        connection_id=connection_id,
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        verifier=verifier,
        expires_at=expires_at,
    )
    encrypted_state = FieldCipher.from_settings(settings).encrypt(
        json.dumps(state.__dict__, separators=(",", ":"), sort_keys=True)
    )
    parameters = {
        "client_id": client.client_id,
        "redirect_uri": settings.mcp_oauth_callback_url,
        "response_type": "code",
        "scope": " ".join(connection_scopes(provider, read_only=read_only)),
        "state": encrypted_state,
    }
    if client.definition.uses_pkce:
        digest = hashlib.sha256(verifier.encode()).digest()
        parameters.update(
            {
                "code_challenge": base64.urlsafe_b64encode(digest).decode().rstrip("="),
                "code_challenge_method": "S256",
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            }
        )
    return f"{client.definition.authorization_url}?{urlencode(parameters)}", expires_at


def decode_oauth_state(value: str, settings: Settings) -> OAuthState:
    if not value or len(value) > 8192:
        raise McpOAuthError("OAuth state is invalid or expired")
    try:
        payload = json.loads(FieldCipher.from_settings(settings).decrypt(value))
        state = OAuthState(
            connection_id=str(payload["connection_id"]),
            tenant_id=str(payload["tenant_id"]),
            user_id=str(payload["user_id"]),
            provider=cast(McpProvider, payload["provider"]),
            verifier=str(payload["verifier"]),
            expires_at=int(payload["expires_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise McpOAuthError("OAuth state is invalid or expired") from exc
    if state.provider not in PROVIDERS or state.expires_at < int(utcnow().timestamp()):
        raise McpOAuthError("OAuth state is invalid or expired")
    if not state.connection_id or not state.tenant_id or not state.user_id:
        raise McpOAuthError("OAuth state is invalid or expired")
    if len(state.verifier) < 43 or len(state.verifier) > 128:
        raise McpOAuthError("OAuth state is invalid or expired")
    return state


def exchange_authorization_code(
    *,
    settings: Settings,
    provider: McpProvider,
    code: str,
    verifier: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    if not code or len(code) > 4096:
        raise McpOAuthError("The OAuth provider returned an invalid authorization code")
    client_config = oauth_client(settings, provider)
    if client_config is None:
        raise McpOAuthError("OAuth is not configured for this connector")
    data = {
        "client_id": client_config.client_id,
        "client_secret": client_config.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.mcp_oauth_callback_url,
    }
    if client_config.definition.uses_pkce:
        data["code_verifier"] = verifier
    try:
        with httpx.Client(
            timeout=settings.mcp_oauth_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream(
                "POST",
                client_config.definition.exchange_url,
                data=data,
                headers={"Accept": "application/json", "User-Agent": "BumpaBestie/1.0"},
            ) as response:
                declared_length = response.headers.get("content-length")
                if declared_length:
                    try:
                        if int(declared_length) > settings.mcp_oauth_max_response_bytes:
                            raise McpOAuthError("The OAuth provider returned an invalid response")
                    except ValueError as exc:
                        raise McpOAuthError(
                            "The OAuth provider returned an invalid response"
                        ) from exc
                if not 200 <= response.status_code < 300:
                    raise McpOAuthError("The OAuth provider rejected the connection")
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > settings.mcp_oauth_max_response_bytes:
                        raise McpOAuthError("The OAuth provider returned an invalid response")
                    content.extend(chunk)
    except httpx.HTTPError as exc:
        raise McpOAuthError("The OAuth provider is temporarily unavailable") from exc
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpOAuthError("The OAuth provider returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise McpOAuthError("The OAuth provider returned an invalid response")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not 8 <= len(access_token) <= 16_384:
        raise McpOAuthError("The OAuth provider returned an invalid response")
    bundle: dict[str, Any] = {"access_token": access_token}
    for key in ("refresh_token", "token_type", "scope"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) <= 16_384:
            bundle[key] = value
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, int) and 0 <= expires_in <= 31_536_000:
        bundle["expires_in"] = expires_in
        bundle["obtained_at"] = int(utcnow().timestamp())
    return bundle


def revoke_oauth_token(
    *,
    settings: Settings,
    provider: McpProvider,
    encrypted_credentials: str | None,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """Best-effort upstream revocation with fixed destinations and no token logging.

    Local access must still be deleted when a provider is unavailable, so this
    function returns a confirmation bit instead of preventing local revocation.
    """

    if not encrypted_credentials:
        return True
    try:
        bundle = json.loads(FieldCipher.from_settings(settings).decrypt(encrypted_credentials))
        access_token = bundle["access_token"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(access_token, str) or not 8 <= len(access_token) <= 16_384:
        return False
    try:
        with httpx.Client(
            timeout=settings.mcp_oauth_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            if provider == "meta_ads":
                request = client.build_request(
                    "DELETE",
                    f"https://graph.facebook.com/{settings.meta_graph_version}/me/permissions",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "User-Agent": "BumpaBestie/1.0",
                    },
                )
            else:
                request = client.build_request(
                    "POST",
                    GOOGLE_REVOCATION_URL,
                    data={"token": access_token},
                    headers={"Accept": "application/json", "User-Agent": "BumpaBestie/1.0"},
                )
            response = client.send(request, stream=True)
            status_code = response.status_code
            response.close()
    except httpx.HTTPError:
        return False
    return 200 <= status_code < 300
