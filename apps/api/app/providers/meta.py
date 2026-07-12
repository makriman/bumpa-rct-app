from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

if TYPE_CHECKING:
    from app.core.config import Settings

GRAPH_BASE_URL = "https://graph.facebook.com"
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
GRAPH_VERSION_RE = re.compile(r"^v\d+\.\d+$")
TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]{1,512}$")
LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}(?:_[A-Z]{2})?$")
MAX_TEXT_LENGTH = 4000


@dataclass(frozen=True)
class MetaDelivery:
    message_id: str
    recipient_id: str | None
    request_id: str | None


class MetaProviderError(RuntimeError):
    """Sanitized provider failure safe to expose to application logs and handlers."""

    def __init__(
        self,
        category: Literal["transport", "timeout", "rate_limited", "provider", "invalid_response"],
        *,
        retryable: bool,
        http_status: int | None = None,
        provider_code: str | None = None,
        request_id: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(f"Meta WhatsApp request failed ({category})")
        self.category = category
        self.retryable = retryable
        self.http_status = http_status
        self.provider_code = provider_code
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


class MetaWhatsAppClient:
    def __init__(
        self,
        *,
        graph_version: str,
        phone_number_id: str,
        access_token: str,
        otp_template_name: str = "bb_otp_login",
        template_language_code: str = "en",
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 262_144,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not GRAPH_VERSION_RE.fullmatch(graph_version):
            raise ValueError("Invalid Meta Graph version")
        if not re.fullmatch(r"\d{5,64}", phone_number_id):
            raise ValueError("Invalid Meta phone number ID")
        if not access_token or len(access_token) < 16:
            raise ValueError("Meta access token is missing or too short")
        if not TEMPLATE_NAME_RE.fullmatch(otp_template_name):
            raise ValueError("Invalid Meta OTP template name")
        if not LANGUAGE_CODE_RE.fullmatch(template_language_code):
            raise ValueError("Invalid Meta template language code")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("Meta timeout must be between 1 and 60 seconds")
        if not 4096 <= max_response_bytes <= 1_048_576:
            raise ValueError("Meta response limit is outside the supported range")

        self._url = f"{GRAPH_BASE_URL}/{graph_version}/{phone_number_id}/messages"
        self._access_token = access_token
        self._otp_template_name = otp_template_name
        self._template_language_code = template_language_code
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> MetaWhatsAppClient:
        phone_number_id = settings.meta_phone_number_id
        if not phone_number_id:
            raise ValueError("Meta phone number ID is not configured")
        return cls(
            graph_version=settings.meta_graph_version,
            phone_number_id=phone_number_id,
            access_token=settings.effective_meta_system_user_access_token,
            otp_template_name=settings.meta_otp_template_name,
            template_language_code=settings.meta_template_language_code,
            timeout_seconds=settings.meta_request_timeout_seconds,
            max_response_bytes=settings.meta_max_response_bytes,
        )

    def send_text(self, phone_e164: str, body: str) -> str:
        if not body or not body.strip():
            raise ValueError("WhatsApp text body must not be empty")
        if len(body) > MAX_TEXT_LENGTH:
            raise ValueError(f"WhatsApp text body exceeds {MAX_TEXT_LENGTH} characters")
        delivery = self._send(
            phone_e164,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "type": "text",
                "text": {"preview_url": False, "body": body},
            },
        )
        return delivery.message_id

    def send_template(
        self,
        phone_e164: str,
        *,
        template_name: str,
        language_code: str,
        components: list[dict[str, Any]] | None = None,
    ) -> str:
        if not TEMPLATE_NAME_RE.fullmatch(template_name):
            raise ValueError("Invalid Meta template name")
        if not LANGUAGE_CODE_RE.fullmatch(language_code):
            raise ValueError("Invalid Meta template language code")
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template["components"] = components
        delivery = self._send(
            phone_e164,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "type": "template",
                "template": template,
            },
        )
        return delivery.message_id

    def send_otp(self, phone_e164: str, code: str) -> str:
        if not re.fullmatch(r"\d{6,10}", code):
            raise ValueError("OTP code must contain 6 to 10 digits")
        return self.send_template(
            phone_e164,
            template_name=self._otp_template_name,
            language_code=self._template_language_code,
            components=[
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": code}],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [{"type": "text", "text": code}],
                },
            ],
        )

    def _send(self, phone_e164: str, payload: dict[str, Any]) -> MetaDelivery:
        to = self._normalize_phone(phone_e164)
        request_payload = {**payload, "to": to}
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    "POST", self._url, json=request_payload, headers=headers
                ) as response:
                    raw = self._read_bounded(response)
                    request_id = response.headers.get("x-fb-request-id")
                    retry_after = self._retry_after(response.headers.get("retry-after"))
                    data = self._decode_json(raw)
                    if not response.is_success:
                        provider_error = data.get("error") if isinstance(data, dict) else None
                        code = (
                            str(provider_error.get("code"))
                            if isinstance(provider_error, dict)
                            and provider_error.get("code") is not None
                            else None
                        )
                        retryable = (
                            response.status_code in {408, 425, 429} or response.status_code >= 500
                        )
                        raise MetaProviderError(
                            "rate_limited" if response.status_code == 429 else "provider",
                            retryable=retryable,
                            http_status=response.status_code,
                            provider_code=code,
                            request_id=request_id,
                            retry_after_seconds=retry_after,
                        )
        except MetaProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise MetaProviderError("timeout", retryable=True) from exc
        except httpx.RequestError as exc:
            raise MetaProviderError("transport", retryable=True) from exc

        if not isinstance(data, dict):
            raise MetaProviderError("invalid_response", retryable=False, request_id=request_id)
        messages = data.get("messages")
        message_id = (
            messages[0].get("id")
            if isinstance(messages, list) and messages and isinstance(messages[0], dict)
            else None
        )
        if not isinstance(message_id, str) or not message_id or len(message_id) > 160:
            raise MetaProviderError("invalid_response", retryable=False, request_id=request_id)
        contacts = data.get("contacts")
        recipient_id = (
            contacts[0].get("wa_id")
            if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict)
            else None
        )
        return MetaDelivery(
            message_id=message_id,
            recipient_id=recipient_id if isinstance(recipient_id, str) else None,
            request_id=request_id,
        )

    def _read_bounded(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self._max_response_bytes:
                    raise MetaProviderError("invalid_response", retryable=False)
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self._max_response_bytes:
                raise MetaProviderError("invalid_response", retryable=False)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _decode_json(raw: bytes) -> Any:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MetaProviderError("invalid_response", retryable=False) from exc

    @staticmethod
    def _normalize_phone(phone_e164: str) -> str:
        if not E164_RE.fullmatch(phone_e164):
            raise ValueError("WhatsApp recipient must use E.164 format")
        return phone_e164[1:]

    @staticmethod
    def _retry_after(value: str | None) -> int | None:
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if 0 <= parsed <= 86_400 else None
