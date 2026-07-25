from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings
from app.providers.hermes import HermesEndpoint

PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,159}$")
LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}$")
SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "audio/aac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "video/3gpp",
        "video/mp4",
        "video/webm",
    }
)


class HermesMediaError(RuntimeError):
    """A sanitized failure from the private Hermes-local media boundary."""


class HermesMediaClient:
    """Tenant-authenticated Whisper/Piper client on Hermes' private control port."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def transcribe(
        self,
        endpoint: HermesEndpoint,
        *,
        content: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        if mime_type not in SUPPORTED_MEDIA_TYPES:
            raise HermesMediaError("The voice or video format is not supported.")
        if not content or len(content) > self._settings.whatsapp_media_max_bytes:
            raise HermesMediaError("The voice or video attachment exceeds its safety limit.")
        response = self._request(
            endpoint,
            operation="transcribe",
            content=content,
            headers={"Content-Type": mime_type, "Accept": "application/json"},
        )
        if len(response.content) > 65_536:
            raise HermesMediaError("Voice transcription returned invalid data.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HermesMediaError("Voice transcription returned invalid data.") from exc
        if not isinstance(payload, dict):
            raise HermesMediaError("Voice transcription returned invalid data.")
        text = payload.get("text")
        language = payload.get("language")
        probability = payload.get("language_probability")
        if not isinstance(text, str) or not text.strip():
            raise HermesMediaError("No speech could be recognised in the attachment.")
        if language is not None and (
            not isinstance(language, str) or not LANGUAGE_CODE.fullmatch(language)
        ):
            raise HermesMediaError("Voice transcription returned invalid data.")
        if probability is not None and (
            not isinstance(probability, int | float) or not 0 <= float(probability) <= 1
        ):
            raise HermesMediaError("Voice transcription returned invalid data.")
        return {
            "text": text.strip()[:12_000],
            "language_code": language,
            "language_probability": float(probability) if probability is not None else None,
            "provider": "hermes_local_whisper",
        }

    def synthesize(
        self,
        endpoint: HermesEndpoint,
        *,
        text: str,
        language: str,
    ) -> bytes:
        normalized = text.strip()
        if not normalized:
            raise HermesMediaError("There is no text to convert to a voice note.")
        language = language.strip().lower()
        if not LANGUAGE_CODE.fullmatch(language):
            raise HermesMediaError("The requested voice language is not supported.")
        response = self._request(
            endpoint,
            operation="synthesize",
            json={"text": normalized[:5_000], "language": language},
            headers={"Content-Type": "application/json", "Accept": "audio/ogg"},
        )
        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "audio/ogg":
            raise HermesMediaError("The local voice provider returned invalid media.")
        if not response.content:
            raise HermesMediaError("The local voice provider returned an empty response.")
        if len(response.content) > 16_777_216:
            raise HermesMediaError("The local voice provider response exceeded its safety limit.")
        return response.content

    def _request(
        self,
        endpoint: HermesEndpoint,
        *,
        operation: str,
        content: bytes | None = None,
        json: dict[str, str] | None = None,
        headers: dict[str, str],
    ) -> httpx.Response:
        if not PROFILE_NAME.fullmatch(endpoint.profile_name) or len(endpoint.api_key) < 8:
            raise HermesMediaError("Hermes media profile is invalid.")
        if operation not in {"transcribe", "synthesize"}:
            raise HermesMediaError("Hermes media operation is invalid.")
        url = self._control_url(endpoint.profile_name, operation)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=3,
                    read=self._settings.hermes_media_timeout_seconds,
                    write=self._settings.hermes_media_timeout_seconds,
                    pool=3,
                ),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {endpoint.api_key}", **headers},
                    content=content,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise HermesMediaError("Local speech processing is temporarily unavailable.") from exc
        if response.status_code in {401, 403}:
            raise HermesMediaError("Local speech processing authentication failed.")
        if response.status_code == 413:
            raise HermesMediaError("The media attachment exceeds its safety limit.")
        if response.status_code == 415:
            raise HermesMediaError("The voice or video format is not supported.")
        if response.status_code == 422:
            raise HermesMediaError("The requested voice language is not supported.")
        if response.status_code == 429:
            raise HermesMediaError("Local speech processing is busy. Please try again shortly.")
        if response.status_code >= 500:
            raise HermesMediaError("Local speech processing is temporarily unavailable.")
        if response.status_code != 200:
            raise HermesMediaError("Local speech processing rejected the request.")
        return response

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
            raise HermesMediaError("Hermes media URL is outside the private runtime boundary.")
        origin = self._settings.hermes_base_internal_host.rstrip("/")
        safe_profile = quote(profile_name, safe="")
        return (
            f"{origin}:{self._settings.hermes_control_port}/v1/profiles/{safe_profile}/{operation}"
        )
