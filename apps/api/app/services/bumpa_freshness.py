"""Canonical database policy for evidenced Bumpa domain freshness."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import BumpaMetricSnapshot, BumpaSyncRun


@dataclass(frozen=True)
class FreshMetric:
    snapshot: BumpaMetricSnapshot
    refreshed_at: datetime


def usable_bumpa_sync_run_predicate() -> ColumnElement[bool]:
    """Select only terminal runs carrying current, typed usability evidence."""

    return and_(
        BumpaSyncRun.error.is_(None),
        BumpaSyncRun.finished_at.is_not(None),
        or_(
            and_(
                BumpaSyncRun.status == "success",
                BumpaSyncRun.completion_quality == "complete",
                BumpaSyncRun.partial_reason.is_(None),
                BumpaSyncRun.orders_availability == "available",
            ),
            and_(
                BumpaSyncRun.status == "partial",
                BumpaSyncRun.completion_quality == "accepted_partial",
                BumpaSyncRun.partial_reason.in_(
                    ("profit_not_calculable", "optional_dataset_unavailable")
                ),
                BumpaSyncRun.orders_availability == "available",
                BumpaSyncRun.orders_count.is_not(None),
            ),
        ),
    )


def latest_available_metrics(
    db: Session,
    tenant_id: str,
    *,
    metric_keys: Iterable[str] | None = None,
    as_of: datetime | None = None,
) -> dict[str, FreshMetric]:
    """Return each metric's newest independently validated provider snapshot."""

    filters = [
        BumpaMetricSnapshot.tenant_id == tenant_id,
        BumpaMetricSnapshot.availability == "available",
        BumpaMetricSnapshot.response_from.is_not(None),
        BumpaMetricSnapshot.response_to.is_not(None),
        or_(
            BumpaMetricSnapshot.value_decimal.is_not(None),
            and_(
                BumpaMetricSnapshot.canonical_payload["schema_version"].as_integer() == 1,
                BumpaMetricSnapshot.canonical_payload["kind"].as_string() == "ranking",
            ),
        ),
        BumpaSyncRun.tenant_id == tenant_id,
        BumpaSyncRun.error.is_(None),
        BumpaSyncRun.finished_at.is_not(None),
        BumpaSyncRun.status.in_(("success", "partial")),
        BumpaSyncRun.completion_quality.in_(("complete", "accepted_partial", "degraded")),
    ]
    requested_keys = tuple(metric_keys or ())
    if requested_keys:
        filters.append(BumpaMetricSnapshot.metric_key.in_(requested_keys))
    if as_of is not None:
        filters.append(BumpaSyncRun.finished_at <= as_of)
    ranked = (
        select(
            BumpaMetricSnapshot.id.label("snapshot_id"),
            BumpaSyncRun.finished_at.label("refreshed_at"),
            func.row_number()
            .over(
                partition_by=BumpaMetricSnapshot.metric_key,
                order_by=(BumpaSyncRun.finished_at.desc(), BumpaSyncRun.id.desc()),
            )
            .label("freshness_rank"),
        )
        .join(BumpaSyncRun, BumpaSyncRun.id == BumpaMetricSnapshot.sync_run_id)
        .where(*filters)
        .subquery()
    )
    rows = db.execute(
        select(BumpaMetricSnapshot, ranked.c.refreshed_at)
        .join(ranked, ranked.c.snapshot_id == BumpaMetricSnapshot.id)
        .where(ranked.c.freshness_rank == 1)
        .order_by(BumpaMetricSnapshot.metric_key)
    ).all()
    return {
        snapshot.metric_key: FreshMetric(snapshot=snapshot, refreshed_at=refreshed_at)
        for snapshot, refreshed_at in rows
        if refreshed_at is not None
    }


def latest_complete_orders_run(
    db: Session,
    tenant_id: str,
    *,
    as_of: datetime | None = None,
) -> BumpaSyncRun | None:
    filters = [
        BumpaSyncRun.tenant_id == tenant_id,
        BumpaSyncRun.error.is_(None),
        BumpaSyncRun.finished_at.is_not(None),
        BumpaSyncRun.status.in_(("success", "partial")),
        BumpaSyncRun.orders_availability == "available",
        BumpaSyncRun.orders_count.is_not(None),
    ]
    if as_of is not None:
        filters.append(BumpaSyncRun.finished_at <= as_of)
    return db.scalar(
        select(BumpaSyncRun)
        .where(*filters)
        .order_by(BumpaSyncRun.finished_at.desc(), BumpaSyncRun.id.desc())
        .limit(1)
    )
