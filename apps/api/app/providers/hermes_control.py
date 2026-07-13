from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.providers.hermes import HermesAuthenticationError, HermesProfileError, HermesUnavailable


@dataclass(frozen=True)
class HermesControlResult:
    status: str


class HermesControlClient:
    """Narrow authenticated client for the private Hermes lifecycle service."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def restart(self, *, profile_name: str, api_key: str) -> HermesControlResult:
        return self._lifecycle_request(
            profile_name=profile_name,
            api_key=api_key,
            operation="restart",
        )

    def activate(self, *, profile_name: str, api_key: str) -> HermesControlResult:
        return self._lifecycle_request(
            profile_name=profile_name,
            api_key=api_key,
            operation="activate",
        )

    def _lifecycle_request(
        self,
        *,
        profile_name: str,
        api_key: str,
        operation: str,
    ) -> HermesControlResult:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,159}", profile_name):
            raise HermesProfileError("Hermes profile name is invalid")
        if len(api_key) < 8:
            raise HermesProfileError("Hermes profile key is invalid")
        if operation not in {"activate", "restart"}:
            raise HermesProfileError("Hermes control operation is invalid")
        url = self._control_url(profile_name, operation)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=3, read=25, write=3, pool=3),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={"confirmation": operation},
                )
        except httpx.HTTPError as exc:
            raise HermesUnavailable("Hermes control service is unreachable") from exc
        if response.status_code in {401, 403}:
            raise HermesAuthenticationError("Hermes control authentication failed")
        if response.status_code >= 500:
            raise HermesUnavailable("Hermes control service is unavailable")
        if response.status_code != 200:
            raise HermesProfileError("Hermes control service rejected the request")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HermesUnavailable("Hermes control service returned invalid data") from exc
        if payload != {"status": "activated" if operation == "activate" else "restarted"}:
            raise HermesUnavailable("Hermes control service returned invalid data")
        return HermesControlResult(status=str(payload["status"]))

    def _control_url(self, profile_name: str, operation: str) -> str:
        configured = urlsplit(self._settings.hermes_base_internal_host)
        valid = (
            configured.scheme == "http"
            and configured.hostname is not None
            and configured.hostname != "localhost"
            and configured.port is None
            and configured.path in {"", "/"}
            and configured.username is None
            and configured.password is None
            and not configured.query
            and not configured.fragment
        )
        if not valid:
            raise HermesProfileError("Hermes control URL is outside the private runtime boundary")
        origin = self._settings.hermes_base_internal_host.rstrip("/")
        return (
            f"{origin}:{self._settings.hermes_control_port}/v1/profiles/{profile_name}/{operation}"
        )
