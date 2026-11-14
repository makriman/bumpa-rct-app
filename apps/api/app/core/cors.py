"""Exact-origin checks for credentialed browser access.

Starlette reflects every request origin when ``*`` is configured together with
``allow_credentials``. Cookie CSRF uses the same allowlist, so a wildcard, the
``null`` origin, or a non-origin value must never reach either control.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import Settings

_UNSAFE_ORIGIN_TOKENS = frozenset({"*", "null"})


def credentialed_cors_origins(settings: Settings) -> list[str]:
    """Return the exact origins that may receive credentialed CORS headers.

    Local and test environments may use HTTP. Everywhere else, HTTP origins are
    omitted so a production process that skipped the shell env gate cannot
    reflect an insecure origin. Wildcards and non-origins fail closed instead.
    """

    allowed: list[str] = []
    for configured in settings.effective_cors_origins:
        origin = _exact_credentialed_origin(configured)
        if not settings.is_local and not origin.startswith("https://"):
            continue
        if origin not in allowed:
            allowed.append(origin)
    return allowed


def normalized_browser_origin(value: str) -> str | None:
    """Reduce a browser Origin or Referer to its scheme, host, and port."""

    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port
    except ValueError:
        return None
    if parsed.hostname == "null" or "*" in parsed.hostname:
        return None
    return _format_origin(parsed.scheme, parsed.hostname, port)


def _exact_credentialed_origin(value: str) -> str:
    candidate = value.strip()
    if candidate.lower() in _UNSAFE_ORIGIN_TOKENS or any(
        character in candidate for character in "*\r\n\t "
    ):
        raise ValueError("Credentialed CORS cannot use a wildcard or null origin")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Credentialed CORS origins must be exact scheme-host-port values")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Credentialed CORS origins must be exact scheme-host-port values") from exc
    if parsed.hostname == "null":
        raise ValueError("Credentialed CORS origins must be exact scheme-host-port values")
    return _format_origin(parsed.scheme, parsed.hostname, port)


def _format_origin(scheme: str, hostname: str, port: int | None) -> str:
    normalized = hostname.lower()
    host = f"[{normalized}]" if ":" in normalized else normalized
    default_port = 443 if scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{host}{suffix}"
