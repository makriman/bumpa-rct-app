from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings


class Offer(BaseModel):
    sdp_type: Literal["offer"]
    sdp: str = Field(min_length=16, max_length=65_536)


class CallEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[A-Za-z0-9._:-]{5,256}$")
    event: Literal["connect", "terminate"]
    direction: Literal["USER_INITIATED", "BUSINESS_INITIATED"] = "USER_INITIATED"
    timestamp: int
    from_: str = Field(default="", alias="from", pattern=r"^\+?[1-9][0-9]{7,14}$|^$")
    session: Offer | None = None


@dataclass(frozen=True)
class IncomingCall:
    phone_number_id: str
    call: CallEvent


def call_events(payload: dict[str, Any], settings: Settings) -> list[IncomingCall]:
    result = []
    for entry in payload.get("entry", []):
        if entry.get("id") != settings.meta_waba_id:
            continue
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if value.get("metadata", {}).get("phone_number_id") != settings.meta_phone_number_id:
                continue
            for raw in value.get("calls", []):
                if raw.get("event") not in {"connect", "terminate"}:
                    continue
                try:
                    event = CallEvent.model_validate(raw)
                except ValidationError:
                    raise ValueError("Invalid call event") from None
                if event.direction == "BUSINESS_INITIATED":
                    continue
                if event.event == "connect" and (event.session is None or not event.from_):
                    raise ValueError("Inbound call needs a caller and SDP offer")
                result.append(IncomingCall(str(settings.meta_phone_number_id), event))
    if len(result) > 16:
        raise ValueError("Too many call events")
    return result


class SignalingError(RuntimeError):
    pass


class MetaCalls:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def action(self, call_id: str, action: str, sdp: str | None = None) -> None:
        if action not in {"pre_accept", "accept", "reject", "terminate"}:
            raise ValueError("Unsupported call action")
        if (action in {"pre_accept", "accept"}) != bool(sdp):
            raise ValueError("Call answer required only for acceptance")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "call_id": call_id,
            "action": action,
        }
        if sdp:
            payload["session"] = {"sdp_type": "answer", "sdp": sdp}
        url = (
            f"https://graph.facebook.com/{self.settings.meta_graph_version}/"
            f"{self.settings.meta_phone_number_id}/calls"
        )
        try:
            async with httpx.AsyncClient(
                timeout=5,
                trust_env=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.settings.effective_meta_system_user_access_token}",
                    },
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65_536:
                            raise SignalingError("Call signaling response too large")
                    if response.status_code != 200 or json.loads(body).get("success") is not True:
                        raise SignalingError("Call signaling failed")
        except (httpx.HTTPError, ValueError, AttributeError):
            raise SignalingError("Call signaling unavailable") from None
