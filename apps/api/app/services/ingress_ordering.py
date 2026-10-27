"""Database receipt order for each verified WhatsApp participant and receiver."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import IngressStream, WebhookEvent
from app.jobs.runtime import JobDeferredError, PermanentJobError

_TERMINAL_RECEIPTS = ("processed", "ignored")


def allocate_arrival(
    db: Session, event: WebhookEvent, *, lane: str, payload: dict[str, Any]
) -> None:
    """Allocate once in the same transaction as receipt and durable job creation.

    The stream row lock serializes receipt allocation against head claims. Its
    sequence denotes database receipt admission, not provider timestamps or
    Redis wake-up order. Deduplication retains the original receipt position.
    """
    if event.ordering_scope is not None or event.arrival_sequence is not None:
        if not event.ordering_scope or not event.arrival_sequence or event.arrival_sequence < 1:
            raise PermanentJobError("Ingress ordering receipt is incomplete")
        return
    scope = _ordering_scope(lane, payload)
    if scope is None:
        return
    dialect = db.get_bind().dialect.name
    statement: PostgresInsert | SQLiteInsert
    if dialect == "postgresql":
        statement = postgres_insert(IngressStream).values(scope_key=scope, next_sequence=1)
    elif dialect == "sqlite":
        statement = sqlite_insert(IngressStream).values(scope_key=scope, next_sequence=1)
    else:
        raise RuntimeError("Ingress ordering requires PostgreSQL or SQLite")
    db.execute(statement.on_conflict_do_nothing(index_elements=[IngressStream.scope_key]))
    stream = db.scalar(
        select(IngressStream)
        .where(IngressStream.scope_key == scope)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stream is None or stream.next_sequence < 1:
        raise RuntimeError("Ingress ordering stream is unavailable")
    event.ordering_scope = scope
    event.arrival_sequence = stream.next_sequence
    stream.next_sequence += 1
    # SessionLocal disables autoflush; persist the increment before another
    # allocation in this same transaction reloads the stream row.
    db.flush()


def require_head(db: Session, event: WebhookEvent) -> None:
    """Defer a later receipt while any earlier receipt remains unresolved.

    The caller must retain the stream lock through admission of its application
    run. Failed/dead-letter processing remains an ordering barrier until an
    explicit reconciliation marks its receipt processed or ignored.
    """
    scope, sequence = event.ordering_scope, event.arrival_sequence
    if not scope or sequence is None or sequence < 1:
        raise PermanentJobError("Inbound receipt has no durable arrival order")
    stream = db.scalar(
        select(IngressStream)
        .where(IngressStream.scope_key == scope)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stream is None or sequence >= stream.next_sequence:
        raise PermanentJobError("Inbound receipt has an invalid arrival order")
    earlier = db.scalar(
        select(WebhookEvent.id)
        .where(
            WebhookEvent.ordering_scope == scope,
            WebhookEvent.arrival_sequence < sequence,
            WebhookEvent.processing_status.not_in(_TERMINAL_RECEIPTS),
        )
        .order_by(WebhookEvent.arrival_sequence)
        .limit(1)
    )
    if earlier is not None:
        raise JobDeferredError("An earlier ingress receipt remains unresolved", delay_seconds=1)


def _ordering_scope(lane: str, payload: dict[str, Any]) -> str | None:
    # Keep extraction independent of services.whatsapp: that service calls this
    # module and importing it here would form a circular dependency.
    try:
        entry = payload["entry"][0]
        value = entry["changes"][0]["value"]
        message = value.get("messages", [])[0]
        actor = message.get("from")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    if not isinstance(actor, str) or not actor.lstrip("+").isdigit():
        return None
    actor = actor.lstrip("+")
    waba = entry.get("id")
    metadata = value.get("metadata")
    phone = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
    if (
        not isinstance(waba, str)
        or not waba.isdigit()
        or not isinstance(phone, str)
        or not phone.isdigit()
    ):
        if lane not in {"local", "test", "development"}:
            return None
        waba, phone = "mock", "mock"
    return hashlib.sha256(f"wa-arrival-v1\0{lane}\0{waba}\0{phone}\0{actor}".encode()).hexdigest()
