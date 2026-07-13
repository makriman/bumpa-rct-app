"""Canonical database policy for evidenced Bumpa freshness."""

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import BumpaSyncRun


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
                BumpaSyncRun.partial_reason == "profit_not_calculable",
                BumpaSyncRun.orders_availability == "available",
                BumpaSyncRun.orders_count.is_not(None),
            ),
        ),
    )
