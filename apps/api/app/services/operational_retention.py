from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import AuditLog, SystemError


class OperationalRetentionResult(TypedDict):
    audit_logs_deleted: int
    system_errors_deleted: int


def cleanup_operational_history(
    db: Session,
    *,
    audit_log_retention_days: int,
    system_error_retention_days: int,
    limit: int,
    now: datetime | None = None,
) -> OperationalRetentionResult:
    """Delete bounded oldest-first batches beyond configured retention windows.

    The limit applies independently to each operational table so a sustained
    audit backlog cannot starve system-error cleanup. The worker transaction is
    the commit boundary: either both bounded batches and the durable job result
    commit, or neither does.
    """

    if not 30 <= audit_log_retention_days <= 3650:
        raise ValueError("Audit-log retention days must be between 30 and 3650")
    if not 7 <= system_error_retention_days <= 3650:
        raise ValueError("System-error retention days must be between 7 and 3650")
    if not 1 <= limit <= 1000:
        raise ValueError("Operational retention limit must be between 1 and 1000")

    current = _as_utc(now or utcnow())
    audit_ids = _expired_ids(
        db,
        AuditLog,
        cutoff=current - timedelta(days=audit_log_retention_days),
        limit=limit,
    )
    error_ids = _expired_ids(
        db,
        SystemError,
        cutoff=current - timedelta(days=system_error_retention_days),
        limit=limit,
    )
    if audit_ids:
        db.execute(
            delete(AuditLog)
            .where(AuditLog.id.in_(audit_ids))
            .execution_options(synchronize_session=False)
        )
    if error_ids:
        db.execute(
            delete(SystemError)
            .where(SystemError.id.in_(error_ids))
            .execution_options(synchronize_session=False)
        )
    return {
        "audit_logs_deleted": len(audit_ids),
        "system_errors_deleted": len(error_ids),
    }


def _expired_ids(
    db: Session,
    model: type[AuditLog] | type[SystemError],
    *,
    cutoff: datetime,
    limit: int,
) -> list[str]:
    return list(db.scalars(_expired_id_statement(model, cutoff=cutoff, limit=limit)).all())


def _expired_id_statement(
    model: type[AuditLog] | type[SystemError],
    *,
    cutoff: datetime,
    limit: int,
) -> Select[tuple[str]]:
    # Continuations and the next daily root may overlap in a horizontally
    # scaled worker pool. Lock only the bounded candidate rows and skip rows
    # another cleanup transaction already owns, so counts and drain chains stay
    # accurate without serializing unrelated inserts or reads.
    return (
        select(model.id)
        .where(model.created_at < cutoff)
        .order_by(model.created_at.asc(), model.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
