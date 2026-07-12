from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import (
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaOrder,
    BumpaRawResponse,
    BumpaSyncRun,
)
from app.providers.local import LocalCommerceProvider
from app.providers.redaction import redact_order_payload


def run_sync(
    db: Session,
    *,
    tenant_id: str,
    connection: BumpaConnection,
    date_from: date,
    date_to: date,
) -> BumpaSyncRun:
    run = BumpaSyncRun(
        tenant_id=tenant_id,
        bumpa_connection_id=connection.id,
        status="running",
        requested_from=date_from,
        requested_to=date_to,
        started_at=utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        if connection.provider != "local":
            raise HTTPException(
                status_code=503,
                detail="The production Bumpa adapter is not configured; use local provider mode",
            )
        snapshot = LocalCommerceProvider(tenant_id).sync(date_from, date_to)
        results: dict[str, Any] = {}
        for dataset in snapshot.datasets:
            key = f"{dataset.resource}.{dataset.dataset}"
            results[key] = dataset.availability
            db.add(
                BumpaRawResponse(
                    tenant_id=tenant_id,
                    sync_run_id=run.id,
                    resource=dataset.resource,
                    dataset=dataset.dataset,
                    http_status=200,
                    availability=dataset.availability,
                    error_message=dataset.error,
                    payload=dataset.payload,
                )
            )
            db.add(
                BumpaMetricSnapshot(
                    tenant_id=tenant_id,
                    sync_run_id=run.id,
                    metric_key=key,
                    metric_title=dataset.title,
                    value_decimal=dataset.value,
                    currency_code="NGN",
                    requested_from=date_from,
                    requested_to=date_to,
                    availability=dataset.availability,
                )
            )
        for order in snapshot.orders:
            existing = db.scalar(
                select(BumpaOrder).where(
                    BumpaOrder.tenant_id == tenant_id,
                    BumpaOrder.bumpa_order_id == order.order_id,
                )
            )
            values = {
                "order_number": order.order_number,
                "status": order.status,
                "payment_status": order.payment_status,
                "currency_code": order.currency_code,
                "total_amount": order.total_amount,
                "order_date": order.order_date,
                "raw_payload": order.payload,
            }
            if existing:
                for name, value in values.items():
                    setattr(existing, name, value)
            else:
                db.add(
                    BumpaOrder(
                        tenant_id=tenant_id,
                        bumpa_order_id=order.order_id,
                        **values,
                    )
                )
            db.add(
                BumpaRawResponse(
                    tenant_id=tenant_id,
                    sync_run_id=run.id,
                    resource="orders",
                    dataset=None,
                    http_status=200,
                    availability="available",
                    payload=redact_order_payload(order.payload),
                )
            )
        run.status = "success"
        run.dataset_results = results
        run.finished_at = utcnow()
        connection.last_successful_sync_at = utcnow()
        connection.last_error = None
        db.commit()
        db.refresh(run)
        return run
    except HTTPException:
        run.status = "failed"
        run.error = "Provider is not configured"
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        db.commit()
        raise
    except Exception as exc:
        run.status = "failed"
        run.error = "Commerce sync failed"
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        db.commit()
        raise HTTPException(status_code=502, detail="Commerce sync failed") from exc


def clear_tenant_commerce(db: Session, tenant_id: str) -> None:
    db.execute(delete(BumpaOrder).where(BumpaOrder.tenant_id == tenant_id))
    db.execute(delete(BumpaMetricSnapshot).where(BumpaMetricSnapshot.tenant_id == tenant_id))
    db.commit()
