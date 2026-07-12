from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import WebhookEvent
from app.db.session import SessionLocal, set_security_context
from app.jobs.runtime import enqueue_job
from app.services.whatsapp import (
    extract_delivery_status,
    extract_message,
    process_inbox_event,
    split_inbox_events,
)


class WebhookClaimConflict(RuntimeError):
    """Raised when a concurrent claim cannot be read after its unique-key race."""


class InlineWebhookProcessingError(RuntimeError):
    def __init__(self, event_id: str) -> None:
        super().__init__("Webhook inline processing failed")
        self.event_id = event_id


@dataclass(frozen=True, slots=True)
class ClaimedWebhookEvent:
    event_id: str
    job_created: bool
    terminal_duplicate: bool


def claim_webhook_events(payload: dict[str, Any], raw: bytes) -> tuple[ClaimedWebhookEvent, ...]:
    """Atomically persist inbox events and their durable outbox jobs.

    This function owns its SQLAlchemy session and must be invoked from a worker
    thread. The route never passes a request-scoped session across thread
    boundaries. Committing the outer transaction is the acknowledgement gate:
    an event and its job/outbox handoff either become durable together or are
    rolled back together.
    """

    with SessionLocal.begin() as session:
        set_security_context(session, privileged=True)
        _ensure_physical_outer_transaction(session)
        return tuple(
            _claim_event(session, item_payload, raw, fallback_key)
            for item_payload, fallback_key in split_inbox_events(payload)
        )


def process_claimed_events_inline(
    claimed: tuple[ClaimedWebhookEvent, ...], settings: Settings
) -> list[dict[str, Any]]:
    """Process local-mode events in a thread-owned privileged session."""

    results: list[dict[str, Any]] = []
    with SessionLocal() as session:
        set_security_context(session, privileged=True)
        for claim in claimed:
            if claim.terminal_duplicate:
                results.append({"status": "duplicate"})
                continue
            try:
                results.append(process_inbox_event(session, claim.event_id, settings))
            except Exception as exc:
                session.rollback()
                set_security_context(session, privileged=True)
                failed_event = session.get(WebhookEvent, claim.event_id)
                if failed_event:
                    failed_event.processing_status = "failed"
                    session.commit()
                raise InlineWebhookProcessingError(claim.event_id) from exc
    return results


def _claim_event(
    session: Session,
    item_payload: dict[str, Any],
    raw: bytes,
    fallback_key: str,
) -> ClaimedWebhookEvent:
    message = extract_message(item_payload)
    delivery = extract_delivery_status(item_payload)
    external_id = _event_id(raw, message, delivery, fallback_key=fallback_key)
    event = session.scalar(
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
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError:
            event = session.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "whatsapp",
                    WebhookEvent.external_event_id == external_id,
                )
            )
            if not event:
                raise WebhookClaimConflict from None

    terminal_duplicate = event.processing_status in {"processed", "ignored"}
    job_created = False
    if not terminal_duplicate:
        _job, job_created = enqueue_job(
            session,
            kind="whatsapp.process_webhook",
            payload={"event_id": event.id},
            idempotency_key=f"meta:{external_id}",
            max_attempts=5,
        )
    return ClaimedWebhookEvent(
        event_id=event.id,
        job_created=job_created,
        terminal_duplicate=terminal_duplicate,
    )


def _ensure_physical_outer_transaction(session: Session) -> None:
    """Make SQLite's outer transaction real before any nested savepoint.

    Python's sqlite3 legacy transaction mode does not emit ``BEGIN`` for a
    SELECT. Without this explicit boundary, releasing the first nested
    savepoint can commit it independently and defeat all-or-nothing batch
    ingress. PostgreSQL starts the physical transaction when the privileged
    RLS context is applied, so it needs no special handling.
    """

    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN"))


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
