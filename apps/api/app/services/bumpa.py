from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import delete, select, update
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
from app.providers.redaction import redact_bumpa_payload, redact_order_payload
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
    "optional_dataset_unavailable",
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


@dataclass(frozen=True)
class ConnectionBoundary:
    id: str
    tenant_id: str
    status: str
    provider: str
    encrypted_api_key: str
    scope_type: str
    scope_id: str
    store_timezone: str
    store_currency: str
    boundary_revision: int


@dataclass(frozen=True)
class ExtractedSync:
    snapshot: BumpaSnapshot | BumpaSyncResult
    live_result: BumpaSyncResult | None


def run_sync(
    db: Session,
    *,
    tenant_id: str,
    connection: BumpaConnection,
    date_from: date,
    date_to: date,
    field_encryption_key: str | None = None,
    field_cipher: FieldCipher | None = None,
    runtime_backend: str | None = None,
) -> BumpaSyncRun:
    boundary = _capture_connection_boundary(connection)
    if boundary.tenant_id != tenant_id or boundary.status != "active":
        raise HTTPException(status_code=409, detail="Bumpa connection changed before sync started")
    # Claim a monotonically increasing generation in one short locked transaction.
    # Provider extraction can then make hundreds of requests without holding a DB
    # transaction, while publication remains deterministically newest-start-wins.
    sync_generation = _claim_sync_generation(db, boundary)
    run_started_at = utcnow()
    run = BumpaSyncRun(
        tenant_id=tenant_id,
        bumpa_connection_id=boundary.id,
        status="running",
        boundary_revision=boundary.boundary_revision,
        sync_generation=sync_generation,
        requested_from=date_from,
        requested_to=date_to,
        started_at=run_started_at,
    )
    try:
        extracted = _extract_sync(
            tenant_id=tenant_id,
            boundary=boundary,
            date_from=date_from,
            date_to=date_to,
            field_encryption_key=field_encryption_key,
            field_cipher=field_cipher,
            runtime_backend=runtime_backend,
        )
    except HTTPException:
        _persist_extraction_failure(
            db,
            boundary=boundary,
            run=run,
            error="Provider is not configured",
            failure_kind="provider_not_configured",
            retryable=False,
        )
        raise
    except BumpaProviderError as exc:
        _persist_extraction_failure(
            db,
            boundary=boundary,
            run=run,
            error=str(exc),
            failure_kind=exc.failure_kind,
            retryable=exc.retryable,
        )
        failure_category: ProviderFailureCategory = (
            "invalid_response" if exc.failure_kind == "scope_ambiguous" else exc.failure_kind
        )
        logger.warning(
            "bumpa_sync_provider_failed",
            extra=provider_failure_log_extra(
                provider="bumpa",
                operation="sync",
                category=failure_category,
                retryable=exc.retryable,
                http_status=exc.status_code,
                request_id_hash=exc.request_id_hash,
                retry_after_seconds=exc.retry_after_seconds,
                sync_run_id=run.id,
            ),
        )
        raise HTTPException(status_code=503 if exc.retryable else 502, detail=str(exc)) from exc
    except Exception as exc:
        _persist_extraction_failure(
            db,
            boundary=boundary,
            run=run,
            error="Commerce sync failed",
            failure_kind="internal_failure",
            retryable=False,
        )
        raise HTTPException(status_code=502, detail="Commerce sync failed") from exc

    try:
        locked_connection = _claim_publication_generation(db, boundary, sync_generation)
        if locked_connection is None:
            _persist_superseded_run(db, run)
            raise HTTPException(
                status_code=409,
                detail="Bumpa sync was superseded by a newer request",
            )
        db.add(run)
        db.flush()
        with db.begin_nested():
            staged = _stage_sync_publication(
                db,
                tenant_id=tenant_id,
                run_id=run.id,
                date_from=date_from,
                date_to=date_to,
                extracted=extracted,
            )
            db.flush()
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
            locked_connection.last_successful_sync_at = completed_at
            locked_connection.last_error = None
        else:
            locked_connection.last_failed_sync_at = completed_at
            locked_connection.last_error = "Some Bumpa datasets or orders were unavailable"
        _record_bumpa_sync_completion(db, run)
        db.commit()
        if staged.live_result is not None and any(
            response.failure_kind is not None for response in staged.live_result.responses
        ):
            _log_degraded_bumpa_sync(run, staged.live_result)
        return run
    except HTTPException:
        # The connection fence is an intentional conflict, not a provider or
        # publication failure. Never write extracted data against a rotated or
        # disabled credential boundary.
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        existing = db.get(BumpaSyncRun, run.id)
        if existing is not None and existing.status in {"success", "partial"}:
            db.rollback()
            return existing
        _persist_extraction_failure(
            db,
            boundary=boundary,
            run=run,
            error="Commerce sync failed",
            failure_kind="internal_failure",
            retryable=False,
        )
        raise HTTPException(status_code=502, detail="Commerce sync failed") from exc


def _capture_connection_boundary(connection: BumpaConnection) -> ConnectionBoundary:
    return ConnectionBoundary(
        id=connection.id,
        tenant_id=connection.tenant_id,
        status=connection.status,
        provider=connection.provider,
        encrypted_api_key=connection.encrypted_api_key,
        scope_type=connection.scope_type,
        scope_id=connection.scope_id,
        store_timezone=connection.store_timezone,
        store_currency=connection.store_currency,
        boundary_revision=connection.boundary_revision,
    )


def _lock_matching_connection(db: Session, boundary: ConnectionBoundary) -> BumpaConnection:
    locked = db.scalar(
        select(BumpaConnection)
        .where(BumpaConnection.id == boundary.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or _capture_connection_boundary(locked) != boundary:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bumpa connection changed during sync")
    return locked


def _claim_sync_generation(db: Session, boundary: ConnectionBoundary) -> int:
    """Atomically claim a durable start order without holding an I/O lock."""

    db.rollback()
    generation = db.scalar(
        update(BumpaConnection)
        .where(
            BumpaConnection.id == boundary.id,
            BumpaConnection.tenant_id == boundary.tenant_id,
            BumpaConnection.status == boundary.status,
            BumpaConnection.provider == boundary.provider,
            BumpaConnection.encrypted_api_key == boundary.encrypted_api_key,
            BumpaConnection.scope_type == boundary.scope_type,
            BumpaConnection.scope_id == boundary.scope_id,
            BumpaConnection.store_timezone == boundary.store_timezone,
            BumpaConnection.store_currency == boundary.store_currency,
            BumpaConnection.boundary_revision == boundary.boundary_revision,
            BumpaConnection.sync_generation < 9_223_372_036_854_775_807,
        )
        .values(sync_generation=BumpaConnection.sync_generation + 1)
        .returning(BumpaConnection.sync_generation)
        .execution_options(synchronize_session=False)
    )
    if generation is None:
        db.rollback()
        locked = _lock_matching_connection(db, boundary)
        db.rollback()
        if locked.sync_generation >= 9_223_372_036_854_775_807:
            raise HTTPException(status_code=503, detail="Bumpa sync generation is exhausted")
        raise HTTPException(status_code=409, detail="Bumpa connection changed during sync")
    db.commit()
    return int(generation)


def _claim_publication_generation(
    db: Session,
    boundary: ConnectionBoundary,
    generation: int,
) -> BumpaConnection | None:
    """Atomically serialize publication while allowing failed newer claims."""

    db.rollback()
    claimed_id = db.scalar(
        update(BumpaConnection)
        .where(
            BumpaConnection.id == boundary.id,
            BumpaConnection.tenant_id == boundary.tenant_id,
            BumpaConnection.status == boundary.status,
            BumpaConnection.provider == boundary.provider,
            BumpaConnection.encrypted_api_key == boundary.encrypted_api_key,
            BumpaConnection.scope_type == boundary.scope_type,
            BumpaConnection.scope_id == boundary.scope_id,
            BumpaConnection.store_timezone == boundary.store_timezone,
            BumpaConnection.store_currency == boundary.store_currency,
            BumpaConnection.boundary_revision == boundary.boundary_revision,
            BumpaConnection.sync_generation >= generation,
            BumpaConnection.published_sync_generation < generation,
        )
        .values(published_sync_generation=generation)
        .returning(BumpaConnection.id)
        .execution_options(synchronize_session=False)
    )
    if claimed_id is None:
        db.rollback()
        locked = _lock_matching_connection(db, boundary)
        if locked.published_sync_generation >= generation:
            return None
        db.rollback()
        raise HTTPException(status_code=409, detail="Bumpa connection changed during sync")
    return db.scalar(
        select(BumpaConnection)
        .where(BumpaConnection.id == claimed_id)
        .execution_options(populate_existing=True)
    )


def _extract_sync(
    *,
    tenant_id: str,
    boundary: ConnectionBoundary,
    date_from: date,
    date_to: date,
    field_encryption_key: str | None,
    field_cipher: FieldCipher | None,
    runtime_backend: str | None,
) -> ExtractedSync:
    if boundary.provider == "local":
        snapshot = LocalCommerceProvider(
            tenant_id,
            store_timezone=boundary.store_timezone,
            store_currency=boundary.store_currency,
        ).sync(date_from, date_to)
        return ExtractedSync(snapshot=snapshot, live_result=None)
    if boundary.provider != "bumpa":
        raise HTTPException(status_code=503, detail="Bumpa provider is not configured")
    if runtime_backend != "bumpa":
        raise HTTPException(status_code=503, detail="Bumpa integration is not enabled")
    if field_cipher is None and not field_encryption_key:
        raise HTTPException(status_code=503, detail="Bumpa credential decryption is unavailable")
    cipher = field_cipher or FieldCipher(field_encryption_key or "")
    api_key = cipher.decrypt(boundary.encrypted_api_key)
    with BumpaClient(
        api_key,
        boundary.scope_type,
        boundary.scope_id,
        store_timezone=boundary.store_timezone,
        store_currency=boundary.store_currency,
    ) as provider:
        live_result = provider.sync(date_from, date_to)
    return ExtractedSync(snapshot=live_result, live_result=live_result)


def _persist_extraction_failure(
    db: Session,
    *,
    boundary: ConnectionBoundary,
    run: BumpaSyncRun,
    error: str,
    failure_kind: str,
    retryable: bool,
) -> None:
    db.rollback()
    locked = _lock_matching_connection(db, boundary)
    failed_at = utcnow()
    run.status = "failed"
    run.completion_quality = "failed"
    run.partial_reason = None
    run.error = error
    run.finished_at = failed_at
    db.add(run)
    db.flush()
    # A failure from an older extraction is still useful audit evidence, but it
    # must not overwrite connection health belonging to a newer claimed sync.
    if locked.sync_generation == run.sync_generation:
        locked.last_failed_sync_at = failed_at
        locked.last_error = error
    _record_bumpa_sync_failure(
        db,
        run,
        failure_kind=failure_kind,
        retryable=retryable,
    )
    db.commit()


def _persist_superseded_run(db: Session, run: BumpaSyncRun) -> None:
    run.status = "failed"
    run.completion_quality = "failed"
    run.partial_reason = None
    run.error = "Superseded by a newer Bumpa sync"
    run.finished_at = utcnow()
    db.add(run)
    db.flush()
    _record_bumpa_sync_failure(
        db,
        run,
        failure_kind="superseded",
        retryable=False,
    )
    db.commit()


def _stage_sync_publication(
    db: Session,
    *,
    tenant_id: str,
    run_id: str,
    date_from: date,
    date_to: date,
    extracted: ExtractedSync,
) -> StagedSync:
    """Stage a previously decoded extraction inside a short publication lock."""

    live_result = extracted.live_result
    snapshot = extracted.snapshot

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
                canonical_payload=dataset.canonical_payload,
                currency_code=dataset.currency_code,
                requested_from=date_from,
                requested_to=date_to,
                response_from=dataset.response_from,
                response_to=dataset.response_to,
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
            payload = redact_bumpa_payload(
                response.payload,
                resource=response.resource,
                dataset=response.dataset,
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

    # Orders and analytics have independent provider contracts. A complete,
    # schema-valid order pull is canonical even when one analytics endpoint is
    # degraded; a later-page order failure still leaves the previous order
    # boundary untouched while every valid metric remains queryable run-scoped.
    if orders_availability == "available" and orders_error is None:
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
        and all(_dataset_has_typed_content(dataset) for dataset in keyed_datasets.values())
    ):
        return SyncCompletion("success", "complete", None, True)

    optional_unavailable = {
        key
        for key, dataset in keyed_datasets.items()
        if dataset.availability == "unavailable" and key in ACCEPTED_UNAVAILABLE_PROFIT_ERRORS
    }
    accepted_optional_partial = (
        exact_dataset_set
        and orders_availability == "available"
        and orders_error is None
        and bool(optional_unavailable)
        and all(
            _dataset_has_typed_content(dataset)
            or (
                key in optional_unavailable
                and dataset.error == ACCEPTED_UNAVAILABLE_PROFIT_ERRORS[key]
            )
            for key, dataset in keyed_datasets.items()
        )
    )
    if accepted_optional_partial:
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


def _dataset_has_typed_content(dataset: ProviderDataset) -> bool:
    if dataset.availability != "available" or dataset.error is not None:
        return False
    if dataset.value is not None:
        return True
    # Rankings have no honest scalar projection. A schema-valid empty ranking is
    # still a meaningful zero-result fact and is retained canonically.
    return (
        dataset.canonical_payload.get("schema_version") == 1
        and dataset.canonical_payload.get("kind") == "ranking"
        and isinstance(dataset.canonical_payload.get("groups"), list)
    )


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
            "order_number": order.order_number[:120],
            "status": order.status[:80],
            "payment_status": order.payment_status[:80],
            "currency_code": order.currency_code,
            "total_amount": order.total_amount,
            "order_date": order.order_date,
            "raw_payload": redact_order_payload(order.payload),
            "shipping_status": _bounded_text(_text(order.payload, "shipping_status"), 80),
            "channel": _bounded_text(_text(order.payload, "channel", "sales_channel"), 80),
            "origin": _bounded_text(_text(order.payload, "origin"), 120),
            "subtotal_amount": _money(
                order.payload,
                "subtotal_amount",
                "subtotal",
                "sub_total",
                currency_code=order.currency_code,
            ),
            "tax_amount": _money(
                order.payload, "tax_amount", "tax", currency_code=order.currency_code
            ),
            "shipping_amount": _money(
                order.payload,
                "shipping_amount",
                "shipping_fee",
                "shipping_price",
                currency_code=order.currency_code,
            ),
            "amount_paid": _money(
                order.payload,
                "amount_paid",
                "paid_amount",
                currency_code=order.currency_code,
            ),
            "amount_due": _money(
                order.payload,
                "amount_due",
                "due_amount",
                currency_code=order.currency_code,
            ),
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
        _replace_order_items(
            db,
            tenant_id,
            existing,
            order.payload,
            currency_code=order.currency_code,
        )
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
    db: Session,
    tenant_id: str,
    order: BumpaOrder,
    payload: dict[str, Any],
    *,
    currency_code: str | None,
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
                bumpa_item_id=_bounded_text(_text(raw, "id", "order_item_id"), 120),
                product_id=_item_product_id(raw),
                name=_bounded_text(_text(raw, "name", "product_name"), 300),
                unit=_bounded_text(_text(raw, "unit"), 80),
                quantity=_money(raw, "quantity", "qty", currency_code=None),
                unit_price=_money(raw, "unit_price", "price", currency_code=currency_code),
                total_amount=_money(raw, "total_amount", "total", currency_code=currency_code),
                raw_payload=redact_order_payload(raw),
            )
        )


def _text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)[:500]
    return None


def _money(payload: dict[str, Any], *keys: str, currency_code: str | None) -> Any:
    from app.providers.redaction import parse_money

    for key in keys:
        if key in payload:
            # ``parse_money`` keeps a legacy NGN fallback for callers that have
            # no currency context. The persistence boundary always has the
            # normalized ProviderOrder currency, so pass it explicitly. An
            # unknown currency uses an empty token set rather than silently
            # interpreting a naira-prefixed value.
            return parse_money(
                payload[key],
                currency_code=(currency_code if currency_code is not None else "UNSPECIFIED"),
            )
    return None


def _item_product_id(payload: dict[str, Any]) -> str | None:
    direct = _text(payload, "product_id", "productId")
    if direct is not None:
        return _bounded_text(direct, 120)
    product = payload.get("product")
    return (
        _bounded_text(_text(product, "id", "product_id"), 120)
        if isinstance(product, dict)
        else None
    )


def _bounded_text(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


def _source_datetime(payload: dict[str, Any], key: str) -> Any:
    from datetime import UTC

    value = payload.get(key)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)
