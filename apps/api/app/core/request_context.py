from __future__ import annotations

import ipaddress
import re
from contextvars import ContextVar
from dataclasses import dataclass

_MAX_USER_AGENT_INPUT = 2048
_CLIENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("edge", re.compile(r"\bedg(?:a|ios)?/(\d{1,4})")),
    ("opera", re.compile(r"\bopr/(\d{1,4})")),
    ("chrome", re.compile(r"\b(?:chrome|crios)/(\d{1,4})")),
    ("firefox", re.compile(r"\b(?:firefox|fxios)/(\d{1,4})")),
    ("curl", re.compile(r"\bcurl/(\d{1,4})")),
    ("wget", re.compile(r"\bwget/(\d{1,4})")),
    ("postman", re.compile(r"\bpostmanruntime/(\d{1,4})")),
    ("httpx", re.compile(r"\bpython-httpx/(\d{1,4})")),
    ("requests", re.compile(r"\bpython-requests/(\d{1,4})")),
    ("okhttp", re.compile(r"\bokhttp/(\d{1,4})")),
    ("whatsapp", re.compile(r"\bwhatsapp/(\d{1,4})")),
)
_SAFARI_VERSION_RE = re.compile(r"\bversion/(\d{1,4})")
_MOZILLA_VERSION_RE = re.compile(r"\bmozilla/(\d{1,4})")


@dataclass(frozen=True, slots=True)
class AuditRequestContext:
    """Privacy-bounded request metadata safe for durable operational evidence."""

    client_ip: str | None
    user_agent: str | None


audit_request_context_var: ContextVar[AuditRequestContext | None] = ContextVar(
    "audit_request_context",
    default=None,
)


def build_audit_request_context(
    *,
    client_host: str | None,
    user_agent: str | None,
) -> AuditRequestContext:
    """Build context only from the ASGI peer and a privacy-scrubbed UA header.

    Callers must pass ``request.client.host`` rather than reading forwarding
    headers themselves. The edge proxy and ASGI server own that trust boundary.
    Full client addresses are never retained: IPv4 is reduced to a /24 network
    and IPv6 to a /48 network before the value reaches an audit model.
    """

    return AuditRequestContext(
        client_ip=_anonymized_client_ip(client_host),
        user_agent=_redacted_user_agent(user_agent),
    )


def _anonymized_client_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    prefix_length = 24 if address.version == 4 else 48
    network = ipaddress.ip_network((address, prefix_length), strict=False)
    return network.network_address.compressed


def _redacted_user_agent(value: str | None) -> str | None:
    if not value:
        return None
    # A User-Agent is arbitrary caller input, so regex-substituting known PII
    # shapes is not a sufficient privacy boundary. Persist only a fixed product
    # and platform taxonomy plus a numeric major version; no caller text is
    # copied into the durable record.
    normalized = value[:_MAX_USER_AGENT_INPUT].lower()
    client, major = _client_family(normalized)
    platform = _platform_family(normalized)
    version = f"/{major}" if major else ""
    return f"client={client}{version}; platform={platform}"


def _client_family(value: str) -> tuple[str, str | None]:
    for family, pattern in _CLIENT_PATTERNS:
        if match := pattern.search(value):
            return family, match.group(1)
    if "safari/" in value and (match := _SAFARI_VERSION_RE.search(value)):
        return "safari", match.group(1)
    if match := _MOZILLA_VERSION_RE.search(value):
        return "mozilla", match.group(1)
    return "other", None


def _platform_family(value: str) -> str:
    if "android" in value:
        return "android"
    if "iphone" in value or "ipad" in value or "ios" in value:
        return "ios"
    if "windows" in value:
        return "windows"
    if "mac os" in value or "macintosh" in value:
        return "macos"
    if "linux" in value or "x11" in value:
        return "linux"
    return "other"
