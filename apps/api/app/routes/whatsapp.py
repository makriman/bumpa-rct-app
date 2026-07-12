from __future__ import annotations

import hmac
import json
import logging
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.services.whatsapp_webhook_ingress import (
    InlineWebhookProcessingError,
    WebhookClaimConflict,
    claim_webhook_events,
    process_claimed_events_inline,
)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
logger = logging.getLogger("bumpabestie.whatsapp")
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


@router.get("")
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    if settings.whatsapp_backend == "disabled":
        raise HTTPException(status_code=503, detail="WhatsApp webhook is disabled")
    if mode == "subscribe" and hmac.compare_digest(
        token or "", settings.effective_meta_webhook_verify_token
    ):
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Invalid webhook verification")


@router.post("")
async def receive_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Verify, persist, and atomically enqueue before acknowledging Meta."""

    if settings.whatsapp_backend == "disabled":
        raise HTTPException(status_code=503, detail="WhatsApp webhook is disabled")
    raw = await _read_bounded_body(request)
    signature = request.headers.get("x-hub-signature-256")
    if not _valid_signature(raw, signature, settings.effective_meta_app_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    payload: dict[str, Any] = decoded

    try:
        claimed = await run_in_threadpool(claim_webhook_events, payload, raw)
    except WebhookClaimConflict:
        raise HTTPException(status_code=409, detail="Webhook event could not be claimed") from None

    if settings.is_local:
        try:
            results = await run_in_threadpool(process_claimed_events_inline, claimed, settings)
        except InlineWebhookProcessingError as exc:
            logger.exception("webhook_inline_processing_failed", extra={"event_id": exc.event_id})
            raise HTTPException(
                status_code=503,
                detail="Webhook processing failed; retry is safe",
            ) from None
        if len(results) == 1:
            return results[0]
        return {
            "status": "accepted",
            "events": len(results),
            "duplicates": sum(result["status"] == "duplicate" for result in results),
        }

    if len(claimed) == 1:
        claim = claimed[0]
        if claim.terminal_duplicate:
            return {"status": "duplicate"}
        return {"status": "accepted", "queued": True, "duplicate": not claim.job_created}
    return {
        "status": "accepted",
        "queued": True,
        "events": len(claimed),
        "duplicates": sum(not claim.job_created for claim in claimed),
    }


async def _read_bounded_body(request: Request) -> bytes:
    """Read the signed payload asynchronously without allowing unbounded buffering."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload too large",
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload too large",
            )
        body.extend(chunk)
    return bytes(body)


def _valid_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
