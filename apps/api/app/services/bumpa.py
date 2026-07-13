from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

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
from app.providers.contracts import BumpaSnapshot, ProviderDataset
from app.providers.local import LocalCommerceProvider
from app.providers.redaction import redact_order_payload

EXPECTED_SYNC_DATASETS = frozenset(
    {
        "sales.overview",
        "sales.total_sales",
        "sales.gross_profit",
        "sales.net_profit",
        "products.overview",
        "products.products_sold",
        "products.top_selling_products",
        "products.least_selling_products",
        "customers.overview",
        "customers.top_customers_order",
    }
)
ACCEPTED_UNAVAILABLE_PROFIT_ERRORS = {
    "sales.gross_profit": "Gross profit cannot be calculated for this store",
    "sales.net_profit": "Net profit cannot be calculated for this store",
}

SyncCompletionQuality = Literal["complete", "accepted_partial", "degraded"]
SyncPartialReason = Literal[
    "profit_not_calculable",
    "dataset_unavailable",
    "dataset_error",
    "orders_unavailable",
    "incomplete_dataset_set",
]


@dataclass(frozen=True)
class SyncCompletion:
    status: Literal["success", "partial"]
    quality: SyncCompletionQuality
    partial_reason: SyncPartialReason | None
    advances_freshness: bool


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
        results: dict[str, str] = {}
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
        orders_error = live_result.orders_error if live_result is not None else None
        completion = classify_sync_completion(
            snapshot.datasets,
            orders_availability=orders_availability,
            orders_error=orders_error,
        )
        run.status = completion.status
        run.completion_quality = completion.quality
        run.partial_reason = completion.partial_reason
        run.orders_availability = orders_availability
        run.orders_count = (
            len(snapshot.orders)
            if orders_availability == "available" and orders_error is None
            else None
        )
        run.dataset_results = results
        if live_result is not None:
            run.rate_limit_limit = live_result.rate_limit_limit
            run.rate_limit_remaining = live_result.rate_limit_remaining
        completed_at = utcnow()
        run.finished_at = completed_at
        if completion.advances_freshness:
            connection.last_successful_sync_at = completed_at
            connection.last_error = None
        else:
            connection.last_failed_sync_at = completed_at
            connection.last_error = "Some Bumpa datasets or orders were unavailable"
        db.commit()
        db.refresh(run)
        return run
    except HTTPException:
        run.status = "failed"
        run.completion_quality = "failed"
        run.partial_reason = None
        run.error = "Provider is not configured"
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        db.commit()
        raise
    except BumpaProviderError as exc:
        db.rollback()
        run.status = "failed"
        run.completion_quality = "failed"
        run.partial_reason = None
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
        run.completion_quality = "failed"
        run.partial_reason = None
        run.error = "Commerce sync failed"
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        db.commit()
        raise HTTPException(status_code=502, detail="Commerce sync failed") from exc


def classify_sync_completion(
    datasets: Sequence[ProviderDataset],
    *,
    orders_availability: str,
    orders_error: str | None = None,
) -> SyncCompletion:
    """Classify a completed pull without overstating incomplete business data.

    A store may legitimately be unable to calculate profit while every other
    required dataset and its orders are current. That remains a visible partial
    run, but it is safe to advance the connection's freshness timestamp. Any
    missing required dataset, provider error, or unavailable required data does
    not advance freshness.
    """

    keyed_datasets: dict[str, ProviderDataset] = {}
    duplicate_dataset = False
    for dataset in datasets:
        key = f"{dataset.resource}.{dataset.dataset}"
        duplicate_dataset = duplicate_dataset or key in keyed_datasets
        keyed_datasets[key] = dataset

    exact_dataset_set = (
        not duplicate_dataset and frozenset(keyed_datasets) == EXPECTED_SYNC_DATASETS
    )
    if (
        exact_dataset_set
        and orders_availability == "available"
        and orders_error is None
        and all(
            dataset.availability == "available" and dataset.error is None
            for dataset in keyed_datasets.values()
        )
    ):
        return SyncCompletion("success", "complete", None, True)

    optional_unavailable = {
        key
        for key, dataset in keyed_datasets.items()
        if dataset.availability == "unavailable" and key in ACCEPTED_UNAVAILABLE_PROFIT_ERRORS
    }
    accepted_profit_partial = (
        exact_dataset_set
        and orders_availability == "available"
        and orders_error is None
        and bool(optional_unavailable)
        and all(
            (dataset.availability == "available" and dataset.error is None)
            or (
                key in optional_unavailable
                and dataset.error == ACCEPTED_UNAVAILABLE_PROFIT_ERRORS[key]
            )
            for key, dataset in keyed_datasets.items()
        )
    )
    if accepted_profit_partial:
        return SyncCompletion("partial", "accepted_partial", "profit_not_calculable", True)

    if orders_availability != "available" or orders_error is not None:
        partial_reason: SyncPartialReason = "orders_unavailable"
    elif not exact_dataset_set:
        partial_reason = "incomplete_dataset_set"
    elif any(dataset.availability == "error" for dataset in keyed_datasets.values()):
        partial_reason = "dataset_error"
    else:
        partial_reason = "dataset_unavailable"
    return SyncCompletion("partial", "degraded", partial_reason, False)


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
