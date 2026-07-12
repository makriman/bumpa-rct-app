from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.models import (
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaOrder,
    BumpaOrderItem,
    BumpaRawResponse,
    BumpaSyncRun,
)
from app.providers.bumpa import BumpaClient, BumpaProviderError, BumpaSyncResult
from app.providers.contracts import BumpaSnapshot
from app.providers.local import LocalCommerceProvider
from app.providers.redaction import redact_order_payload


def run_sync(
    db: Session,
    *,
    tenant_id: str,
    connection: BumpaConnection,
    date_from: date,
    date_to: date,
    field_encryption_key: str | None = None,
    runtime_backend: str | None = None,
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
        live_result: BumpaSyncResult | None = None
        snapshot: BumpaSnapshot | BumpaSyncResult
        if connection.provider == "local":
            snapshot = LocalCommerceProvider(tenant_id).sync(date_from, date_to)
        elif connection.provider == "bumpa":
            if runtime_backend != "bumpa":
                raise HTTPException(status_code=503, detail="Bumpa integration is not enabled")
            if not field_encryption_key:
                raise HTTPException(
                    status_code=503, detail="Bumpa credential decryption is unavailable"
                )
            api_key = FieldCipher(field_encryption_key).decrypt(connection.encrypted_api_key)
            with BumpaClient(api_key, connection.scope_type, connection.scope_id) as provider:
                live_result = provider.sync(date_from, date_to)
            snapshot = live_result
        else:
            raise HTTPException(
                status_code=503,
                detail="Bumpa provider is not configured",
            )
        results: dict[str, Any] = {}
        for dataset in snapshot.datasets:
            key = f"{dataset.resource}.{dataset.dataset}"
            results[key] = dataset.availability
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
            if live_result is None:
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
        if live_result is not None:
            for response in live_result.responses:
                payload = (
                    redact_order_payload(response.payload)
                    if response.resource == "orders"
                    else response.payload
                )
                db.add(
                    BumpaRawResponse(
                        tenant_id=tenant_id,
                        sync_run_id=run.id,
                        resource=response.resource,
                        dataset=response.dataset,
                        http_status=response.status_code,
                        availability=response.availability,
                        error_message=response.error,
                        payload=payload,
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
                "shipping_status": _text(order.payload, "shipping_status"),
                "channel": _text(order.payload, "channel", "sales_channel"),
                "origin": _text(order.payload, "origin"),
                "subtotal_amount": _money(order.payload, "subtotal_amount", "subtotal"),
                "tax_amount": _money(order.payload, "tax_amount", "tax"),
                "shipping_amount": _money(order.payload, "shipping_amount", "shipping_fee"),
                "amount_paid": _money(order.payload, "amount_paid", "paid_amount"),
                "amount_due": _money(order.payload, "amount_due", "due_amount"),
                "created_at_source": _source_datetime(order.payload, "created_at"),
                "updated_at_source": _source_datetime(order.payload, "updated_at"),
            }
            if existing:
                for name, value in values.items():
                    setattr(existing, name, value)
            else:
                existing = BumpaOrder(
                    tenant_id=tenant_id,
                    bumpa_order_id=order.order_id,
                    **values,
                )
                db.add(existing)
            db.flush()
            _replace_order_items(db, tenant_id, existing, order.payload)
            if live_result is None:
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
        orders_availability = (
            live_result.orders_availability if live_result is not None else "available"
        )
        run.status = (
            "partial"
            if any(value != "available" for value in results.values())
            or orders_availability != "available"
            else "success"
        )
        run.dataset_results = results
        if live_result is not None:
            run.rate_limit_limit = live_result.rate_limit_limit
            run.rate_limit_remaining = live_result.rate_limit_remaining
        run.finished_at = utcnow()
        if run.status == "success":
            connection.last_successful_sync_at = utcnow()
            connection.last_error = None
        else:
            connection.last_failed_sync_at = utcnow()
            connection.last_error = "Some Bumpa datasets or orders were unavailable"
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
    except BumpaProviderError as exc:
        db.rollback()
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        db.commit()
        status_code = 503 if exc.retryable else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
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


def _replace_order_items(
    db: Session, tenant_id: str, order: BumpaOrder, payload: dict[str, Any]
) -> None:
    db.execute(delete(BumpaOrderItem).where(BumpaOrderItem.order_id == order.id))
    raw_items = payload.get("items") or payload.get("order_items") or payload.get("products") or []
    if not isinstance(raw_items, list):
        return
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        db.add(
            BumpaOrderItem(
                tenant_id=tenant_id,
                order_id=order.id,
                bumpa_item_id=_text(raw, "id", "order_item_id"),
                product_id=_text(raw, "product_id", "productId"),
                name=_text(raw, "name", "product_name"),
                unit=_text(raw, "unit"),
                quantity=_money(raw, "quantity", "qty"),
                unit_price=_money(raw, "unit_price", "price"),
                total_amount=_money(raw, "total_amount", "total"),
                raw_payload=redact_order_payload(raw),
            )
        )


def _text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)[:500]
    return None


def _money(payload: dict[str, Any], *keys: str) -> Any:
    from app.providers.redaction import parse_money

    for key in keys:
        if key in payload:
            return parse_money(payload[key])
    return None


def _source_datetime(payload: dict[str, Any], key: str) -> Any:
    from datetime import UTC, datetime

    value = payload.get(key)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)
