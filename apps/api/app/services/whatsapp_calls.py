"""Signed call-event routing, kept separate from durable message processing."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import Settings


def split_call_changes(payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Remove call changes before storing message webhooks (SDP contains network PII)."""
    has_calls = False
    entries = []
    for entry in payload.get("entry", []):
        changes = []
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if change.get("field") == "calls" or "calls" in value:
                has_calls = True
                remaining = {key: val for key, val in value.items() if key != "calls"}
                if remaining.get("messages") or remaining.get("statuses"):
                    changes.append({**change, "field": "messages", "value": remaining})
            else:
                changes.append(change)
        if changes:
            entries.append({**entry, "changes": changes})
    return has_calls, {**payload, "entry": entries}


async def forward_call_webhook(raw: bytes, signature: str, settings: Settings) -> None:
    if not settings.whatsapp_calling_enabled:
        return
    try:
        async with httpx.AsyncClient(timeout=28, trust_env=False) as client:
            response = await client.post(
                "http://voice:8001/webhooks/whatsapp/calls",
                content=raw,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(
            status_code=503, detail="Call gateway unavailable; retry is safe"
        ) from None
