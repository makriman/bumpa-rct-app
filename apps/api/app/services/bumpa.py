from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
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
from app.providers.contracts import BumpaSnapshot, ProviderDataset, ProviderOrder
from app.providers.diagnostics import (
    ProviderFailureCategory,
    provider_failure_log_extra,
)
from app.providers.local import LocalCommerceProvider
from app.providers.redaction import redact_order_payload
from app.services.research_events import record_research_event

logger = logging.getLogger("bumpabestie.providers")

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


@dataclass(frozen=True)
class StagedSync:
    live_result: BumpaSyncResult | None
    completion: SyncCompletion
    dataset_results: dict[str, str]
    orders_availability: str
    orders_error: str | None
    orders_count: int | None


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
    selected_connection_boundary = (
        connection.tenant_id,
        connection.status,
        connection.provider,
        connection.encrypted_api_key,
        connection.scope_type,
        connection.scope_id,
    )
    # Serialize a connection before creating observable sync state. The run and
    # every canonical side effect live in the same transaction, so a killed
    # worker rolls them back instead of leaving a permanent `running` row.
    db.refresh(connection, with_for_update=True)
    refreshed_connection_boundary = (
        connection.tenant_id,
        connection.status,
        connection.provider,
        connection.encrypted_api_key,
        connection.scope_type,
        connection.scope_id,
    )
    if (
        refreshed_connection_boundary != selected_connection_boundary
        or connection.tenant_id != tenant_id
        or connection.status != "active"
    ):
        raise HTTPException(status_code=409, detail="Bumpa connection changed before sync started")
    failure_publication_boundary = _connection_failure_publication_boundary(connection)
    run_started_at = utcnow()
    run = BumpaSyncRun(
        tenant_id=tenant_id,
        bumpa_connection_id=connection.id,
        status="running",
        requested_from=date_from,
        requested_to=date_to,
        started_at=run_started_at,
    )
    db.add(run)
    db.flush()
    run_id = run.id
    publication_completed = False
    try:
        # The run and connection lock belong to the outer transaction. Provider,
        # evidence, and canonical publication happen inside a savepoint so an
        # ordinary data failure can roll back without releasing serialization to
        # a queued sync before this run's terminal audit and health commit.
        with db.begin_nested():
            staged = _stage_sync_publication(
                db,
                tenant_id=tenant_id,
                connection=connection,
                run_id=run.id,
                date_from=date_from,
                date_to=date_to,
                field_encryption_key=field_encryption_key,
                runtime_backend=runtime_backend,
            )
            db.flush()
        publication_completed = True
        run.status = staged.completion.status
        run.completion_quality = staged.completion.quality
        run.partial_reason = staged.completion.partial_reason
        run.orders_availability = staged.orders_availability
        run.orders_count = staged.orders_count
        run.dataset_results = staged.dataset_results
        if staged.live_result is not None:
            run.rate_limit_limit = staged.live_result.rate_limit_limit
            run.rate_limit_remaining = staged.live_result.rate_limit_remaining
        completed_at = utcnow()
        run.finished_at = completed_at
        if staged.completion.advances_freshness:
            connection.last_successful_sync_at = completed_at
            connection.last_error = None
        else:
            connection.last_failed_sync_at = completed_at
            connection.last_error = "Some Bumpa datasets or orders were unavailable"
        _record_bumpa_sync_completion(db, run)
        db.commit()
        if staged.completion.quality == "degraded" and staged.live_result is not None:
            _log_degraded_bumpa_sync(run, staged.live_result)
        return run
    except HTTPException:
        run.status = "failed"
        run.completion_quality = "failed"
        run.partial_reason = None
        run.error = "Provider is not configured"
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        _record_bumpa_sync_failure(
            db,
            run,
            failure_kind="provider_not_configured",
            retryable=False,
        )
        db.commit()
        raise
    except BumpaProviderError as exc:
        run.status = "failed"
        run.completion_quality = "failed"
        run.partial_reason = None
        run.error = str(exc)
        run.finished_at = utcnow()
        connection.last_failed_sync_at = utcnow()
        connection.last_error = run.error
        _record_bumpa_sync_failure(
            db,
            run,
            failure_kind=exc.failure_kind,
            retryable=exc.retryable,
        )
        db.commit()
        logger.warning(
            "bumpa_sync_provider_failed",
            extra=provider_failure_log_extra(
                provider="bumpa",
                operation="sync",
                category=exc.failure_kind,
                retryable=exc.retryable,
                http_status=exc.status_code,
                request_id_hash=exc.request_id_hash,
                retry_after_seconds=exc.retry_after_seconds,
                sync_run_id=run.id,
            ),
        )
        status_code = 503 if exc.retryable else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        failed_at = utcnow()
        if not publication_completed:
            _terminalize_generic_failure(run, connection, failed_at)
            _record_bumpa_sync_failure(
                db,
                run,
                failure_kind="internal_failure",
                retryable=False,
            )
            try:
                db.commit()
            except Exception:
                db.rollback()
                recovered = _recover_generic_failure_after_outer_rollback(
                    db,
                    tenant_id=tenant_id,
                    connection=connection,
                    run_id=run_id,
                    run_started_at=run_started_at,
                    failed_at=failed_at,
                    date_from=date_from,
                    date_to=date_to,
                    failure_publication_boundary=failure_publication_boundary,
                )
                if recovered.status != "failed":
                    return recovered
        else:
            # A failure while publishing the outer transaction has an ambiguous
            # commit outcome. Roll back locally, then inspect by run identity: a
            # terminal row proves the commit landed; otherwise persist only the
            # sanitized failure audit in a fresh fenced transaction.
            db.rollback()
            recovered = _recover_generic_failure_after_outer_rollback(
                db,
                tenant_id=tenant_id,
                connection=connection,
                run_id=run_id,
                run_started_at=run_started_at,
                failed_at=failed_at,
                date_from=date_from,
                date_to=date_to,
                failure_publication_boundary=failure_publication_boundary,
            )
            if recovered.status != "failed":
                return recovered
        raise HTTPException(status_code=502, detail="Commerce sync failed") from exc


def _stage_sync_publication(
    db: Session,
    *,
    tenant_id: str,
    connection: BumpaConnection,
    run_id: str,
    date_from: date,
    date_to: date,
    field_encryption_key: str | None,
    runtime_backend: str | None,
) -> StagedSync:
    """Pull and stage one publication inside the caller's nested transaction."""

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

    orders_availability = (
        live_result.orders_availability if live_result is not None else "available"
    )
    orders_error = live_result.orders_error if live_result is not None else None
    completion = classify_sync_completion(
        snapshot.datasets,
        orders_availability=orders_availability,
        orders_error=orders_error,
    )
    results: dict[str, str] = {}
    for dataset in snapshot.datasets:
        key = f"{dataset.resource}.{dataset.dataset}"
        results[key] = dataset.availability
        db.add(
            BumpaMetricSnapshot(
                tenant_id=tenant_id,
                sync_run_id=run_id,
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
                    sync_run_id=run_id,
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
                    sync_run_id=run_id,
                    resource=response.resource,
                    dataset=response.dataset,
                    http_status=response.status_code,
                    availability=response.availability,
                    failure_kind=response.failure_kind,
                    error_message=response.error,
                    payload=payload,
                )
            )

    # Raw responses and run-scoped metric evidence are retained for a degraded
    # pull, but canonical commerce rows represent the latest usable boundary and
    # must not move ahead of the evidenced freshness timestamp.
    if completion.advances_freshness:
        _promote_orders(
            db,
            tenant_id,
            run_id,
            snapshot.orders,
            record_raw_responses=live_result is None,
        )
    return StagedSync(
        live_result=live_result,
        completion=completion,
        dataset_results=results,
        orders_availability=orders_availability,
        orders_error=orders_error,
        orders_count=(
            len(snapshot.orders)
            if orders_availability == "available" and orders_error is None
            else None
        ),
    )


def _terminalize_generic_failure(
    run: BumpaSyncRun,
    connection: BumpaConnection,
    failed_at: datetime,
) -> None:
    run.status = "failed"
    run.completion_quality = "failed"
    run.partial_reason = None
    run.error = "Commerce sync failed"
    run.finished_at = failed_at
    connection.last_failed_sync_at = failed_at
    connection.last_error = run.error


def _recover_generic_failure_after_outer_rollback(
    db: Session,
    *,
    tenant_id: str,
    connection: BumpaConnection,
    run_id: str,
    run_started_at: datetime,
    failed_at: datetime,
    date_from: date,
    date_to: date,
    failure_publication_boundary: tuple[object, ...],
) -> BumpaSyncRun:
    """Resolve an ambiguous outer commit without duplicating a landed run."""

    db.refresh(connection, with_for_update=True)
    existing = db.scalar(
        select(BumpaSyncRun)
        .where(BumpaSyncRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        db.commit()
        return existing

    failed_run = BumpaSyncRun(
        id=run_id,
        tenant_id=tenant_id,
        bumpa_connection_id=connection.id,
        status="failed",
        completion_quality="failed",
        requested_from=date_from,
        requested_to=date_to,
        started_at=run_started_at,
        finished_at=failed_at,
        error="Commerce sync failed",
    )
    db.add(failed_run)
    if _connection_failure_publication_boundary(connection) == failure_publication_boundary:
        connection.last_failed_sync_at = failed_at
        connection.last_error = failed_run.error
    _record_bumpa_sync_failure(
        db,
        failed_run,
        failure_kind="internal_failure",
        retryable=False,
    )
    db.commit()
    return failed_run


def _log_degraded_bumpa_sync(run: BumpaSyncRun, result: BumpaSyncResult) -> None:
    """Emit one aggregate, sanitized warning after degraded evidence commits."""

    failure = next(
        (response for response in result.responses if response.failure_kind is not None),
        None,
    )
    category: ProviderFailureCategory = "provider"
    if failure is not None and failure.failure_kind == "timeout":
        category = "timeout"
    elif failure is not None and failure.failure_kind == "transport":
        category = "transport"
    logger.warning(
        "bumpa_sync_degraded",
        extra=provider_failure_log_extra(
            provider="bumpa",
            operation="sync",
            category=category,
            retryable=failure.retryable if failure is not None else False,
            http_status=failure.status_code if failure is not None else None,
            request_id_hash=(failure.request_id_hash if failure is not None else None),
            retry_after_seconds=(failure.retry_after_seconds if failure is not None else None),
            sync_run_id=run.id,
        ),
    )


def _record_bumpa_sync_completion(db: Session, run: BumpaSyncRun) -> None:
    outcome = {
        "status": run.status,
        "completion_quality": run.completion_quality,
        "partial_reason": run.partial_reason,
        "orders_availability": run.orders_availability,
        "orders_count": run.orders_count,
        "datasets_available": sum(
            availability == "available" for availability in run.dataset_results.values()
        ),
        "datasets_unavailable": sum(
            availability == "unavailable" for availability in run.dataset_results.values()
        ),
        "datasets_error": sum(
            availability == "error" for availability in run.dataset_results.values()
        ),
    }
    quality_flags = (run.partial_reason,) if run.partial_reason else ()
    record_research_event(
        db,
        tenant_id=run.tenant_id,
        event_type="bumpa_sync_completed",
        source_parts=(run.id,),
        channel="worker",
        business_outcome=outcome,
        quality_flags=quality_flags,
    )
    if run.completion_quality == "degraded":
        record_research_event(
            db,
            tenant_id=run.tenant_id,
            event_type="bumpa_sync_degraded",
            source_parts=(run.id,),
            channel="worker",
            business_outcome=outcome,
            quality_flags=quality_flags,
        )


def _record_bumpa_sync_failure(
    db: Session,
    run: BumpaSyncRun,
    *,
    failure_kind: str,
    retryable: bool,
) -> None:
    record_research_event(
        db,
        tenant_id=run.tenant_id,
        event_type="bumpa_sync_failed",
        source_parts=(run.id,),
        channel="worker",
        business_outcome={
            "status": "failed",
            "completion_quality": "failed",
            "failure_kind": failure_kind,
            "retryable": retryable,
        },
        quality_flags=(failure_kind,),
    )


def _connection_failure_publication_boundary(connection: BumpaConnection) -> tuple[object, ...]:
    """Return committed connection state that fences stale failure publication."""

    return (
        connection.updated_at,
        connection.status,
        connection.provider,
        connection.encrypted_api_key,
        connection.scope_type,
        connection.scope_id,
        connection.last_successful_sync_at,
        connection.last_failed_sync_at,
        connection.last_error,
    )


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


def _promote_orders(
    db: Session,
    tenant_id: str,
    sync_run_id: str,
    orders: Sequence[ProviderOrder],
    *,
    record_raw_responses: bool,
) -> None:
    for order in orders:
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
        if record_raw_responses:
            db.add(
                BumpaRawResponse(
                    tenant_id=tenant_id,
                    sync_run_id=sync_run_id,
                    resource="orders",
                    dataset=None,
                    http_status=200,
                    availability="available",
                    payload=redact_order_payload(order.payload),
                )
            )


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
