from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import WebhookEvent
from app.db.session import get_db, set_security_context
from app.jobs.runtime import enqueue_job
from app.services.whatsapp import (
    extract_delivery_status,
    extract_message,
    process_inbox_event,
    split_inbox_events,
)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
logger = logging.getLogger("bumpabestie.whatsapp")


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
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Verify, persist, and atomically enqueue before acknowledging Meta."""

    if settings.whatsapp_backend == "disabled":
        raise HTTPException(status_code=503, detail="WhatsApp webhook is disabled")
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not _valid_signature(raw, signature, settings.effective_meta_app_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    payload: dict[str, Any] = decoded

    set_security_context(db, privileged=True)
    claimed: list[tuple[WebhookEvent, bool, bool]] = []
    for item_payload, fallback_key in split_inbox_events(payload):
        message = extract_message(item_payload)
        delivery = extract_delivery_status(item_payload)
        external_id = _event_id(raw, message, delivery, fallback_key=fallback_key)
        event = db.scalar(
            select(WebhookEvent).where(
                WebhookEvent.provider == "whatsapp",
                WebhookEvent.external_event_id == external_id,
            )
        )
        if not event:
            event = WebhookEvent(
                provider="whatsapp",
                external_event_id=external_id,
                signature_valid=True,
                payload=item_payload,
            )
            try:
                with db.begin_nested():
                    db.add(event)
                    db.flush()
            except IntegrityError:
                event = db.scalar(
                    select(WebhookEvent).where(
                        WebhookEvent.provider == "whatsapp",
                        WebhookEvent.external_event_id == external_id,
                    )
                )
                if not event:
                    raise HTTPException(
                        status_code=409, detail="Webhook event could not be claimed"
                    ) from None

        terminal_duplicate = event.processing_status in {"processed", "ignored"}
        created = False
        if not terminal_duplicate:
            _job, created = enqueue_job(
                db,
                kind="whatsapp.process_webhook",
                payload={"event_id": event.id},
                idempotency_key=f"meta:{external_id}",
                max_attempts=5,
            )
        claimed.append((event, created, terminal_duplicate))
    db.commit()

    if settings.is_local:
        results: list[dict[str, Any]] = []
        for event, _created, terminal_duplicate in claimed:
            if terminal_duplicate:
                results.append({"status": "duplicate"})
                continue
            inbox_event_id = event.id
            try:
                results.append(process_inbox_event(db, inbox_event_id, settings))
            except Exception:
                logger.exception(
                    "webhook_inline_processing_failed", extra={"event_id": inbox_event_id}
                )
                db.rollback()
                set_security_context(db, privileged=True)
                failed_event = db.get(WebhookEvent, inbox_event_id)
                if failed_event:
                    failed_event.processing_status = "failed"
                    db.commit()
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
        _event, created, terminal_duplicate = claimed[0]
        if terminal_duplicate:
            return {"status": "duplicate"}
        return {"status": "accepted", "queued": True, "duplicate": not created}
    return {
        "status": "accepted",
        "queued": True,
        "events": len(claimed),
        "duplicates": sum(not created for _event, created, _terminal in claimed),
    }


def _valid_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _event_id(
    raw: bytes,
    message: dict[str, Any] | None,
    delivery: dict[str, Any] | None,
    *,
    fallback_key: str = "root",
) -> str:
    if message and message.get("id"):
        return str(message["id"])
    if delivery and delivery.get("id"):
        return ":".join(
            [
                str(delivery["id"]),
                str(delivery.get("status", "unknown")),
                str(delivery.get("timestamp", "unknown")),
            ]
        )
    if fallback_key == "root":
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(raw + b"\0" + fallback_key.encode()).hexdigest()
