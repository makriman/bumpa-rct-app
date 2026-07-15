"""Transactional policy for replacing a tenant's Bumpa connection boundary."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import BumpaConnection, BumpaOrder, BumpaOrderItem

MAX_BOUNDARY_REVISION = 9_223_372_036_854_775_807


@dataclass(frozen=True)
class BumpaBoundaryInput:
    scope_type: str
    scope_id: str
    store_timezone: str
    store_currency: str
    provider: str


def material_boundary_changed(
    connection: BumpaConnection,
    boundary: BumpaBoundaryInput,
) -> bool:
    """Return whether persisted commerce evidence belongs to another boundary."""

    return (
        connection.scope_type != boundary.scope_type
        or connection.scope_id != boundary.scope_id
        or connection.store_timezone != boundary.store_timezone
        or connection.store_currency != boundary.store_currency
        or connection.provider != boundary.provider
    )


def replace_bumpa_connection(
    db: Session,
    connection: BumpaConnection,
    *,
    encrypted_api_key: str,
    boundary: BumpaBoundaryInput,
) -> bool:
    """Replace credentials/context and atomically invalidate material old state.

    Historical sync runs, raw responses, and metric snapshots remain available
    for audit. Their older boundary revision makes them ineligible for product
    freshness reads. Canonical orders are mutable tenant projections without a
    run/revision key, so they are removed in the same transaction.

    Callers updating an existing connection must acquire its row with
    ``SELECT ... FOR UPDATE`` and commit this mutation with their audit record.
    """

    changed = material_boundary_changed(connection, boundary)
    if changed:
        if connection.boundary_revision >= MAX_BOUNDARY_REVISION:
            raise HTTPException(
                status_code=503,
                detail="Bumpa connection boundary revision is exhausted",
            )
        connection.boundary_revision += 1
        connection.last_successful_sync_at = None
        connection.last_failed_sync_at = None
        connection.last_error = None
        db.execute(delete(BumpaOrderItem).where(BumpaOrderItem.tenant_id == connection.tenant_id))
        db.execute(delete(BumpaOrder).where(BumpaOrder.tenant_id == connection.tenant_id))

    connection.encrypted_api_key = encrypted_api_key
    connection.scope_type = boundary.scope_type
    connection.scope_id = boundary.scope_id
    connection.store_timezone = boundary.store_timezone
    connection.store_currency = boundary.store_currency
    connection.provider = boundary.provider
    connection.status = "active"
    connection.last_error = None
    return changed
