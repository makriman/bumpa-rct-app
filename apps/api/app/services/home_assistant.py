from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.crypto import FieldCipher

HOST_RE = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.IGNORECASE,
)
BLOCKED_SUFFIXES = (".internal", ".lan", ".local", ".localhost", ".home", ".arpa")


class HomeAssistantConnectionError(ValueError):
    """A safe validation failure for a tenant-supplied Home Assistant connection."""


@dataclass(frozen=True)
class HomeAssistantCredentials:
    base_url: str
    access_token: str


def validate_home_assistant_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized or len(normalized) > 500:
        raise HomeAssistantConnectionError("Home Assistant URL is invalid.")
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HomeAssistantConnectionError("Home Assistant URL is invalid.") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
        or not HOST_RE.fullmatch(hostname)
        or hostname == "localhost"
        or hostname.endswith(BLOCKED_SUFFIXES)
    ):
        raise HomeAssistantConnectionError(
            "Home Assistant must use an explicit public HTTPS hostname on port 443."
        )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise HomeAssistantConnectionError(
            "Home Assistant IP addresses and private-network discovery are not permitted."
        )
    return f"https://{hostname}"


def encrypt_home_assistant_credentials(
    settings: Settings,
    *,
    base_url: str,
    access_token: str,
) -> str:
    normalized_url = validate_home_assistant_base_url(base_url)
    token = access_token.strip()
    if (
        not 20 <= len(token) <= 16_384
        or any(character.isspace() for character in token)
        or "\x00" in token
    ):
        raise HomeAssistantConnectionError("Home Assistant access token is invalid.")
    return FieldCipher.from_settings(settings).encrypt(
        json.dumps(
            {
                "access_token": token,
                "base_url": normalized_url,
                "credential_type": "home_assistant_long_lived_token",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def decrypt_home_assistant_credentials(
    settings: Settings,
    encrypted_credentials: str | None,
) -> HomeAssistantCredentials:
    try:
        payload = json.loads(
            FieldCipher.from_settings(settings).decrypt(encrypted_credentials or "")
        )
        base_url = validate_home_assistant_base_url(str(payload["base_url"]))
        access_token = str(payload["access_token"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HomeAssistantConnectionError("Home Assistant credentials are unavailable.") from exc
    if (
        payload.get("credential_type") != "home_assistant_long_lived_token"
        or not 20 <= len(access_token) <= 16_384
        or any(character.isspace() for character in access_token)
    ):
        raise HomeAssistantConnectionError("Home Assistant credentials are unavailable.")
    return HomeAssistantCredentials(base_url=base_url, access_token=access_token)
