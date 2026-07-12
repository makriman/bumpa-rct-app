import secrets
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant, require_tenant_admin
from app.core.rate_limit import enforce_operation_rate_limit
from app.db.models import BumpaConnection, BumpaSyncRun
from app.db.session import get_db
from app.jobs.runtime import enqueue_job
from app.schemas import SyncRequest
from app.services.bumpa import run_sync

router = APIRouter(prefix="/bumpa", tags=["bumpa"])


@router.post("/sync")
def sync(
    payload: SyncRequest,
    response: Response,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if settings.bumpa_backend == "disabled":
        raise HTTPException(
            status_code=503,
            detail="Bumpa integration is disabled",
        )
    assert principal.tenant is not None
    connection = db.scalar(
        select(BumpaConnection).where(BumpaConnection.tenant_id == principal.tenant.id)
    )
    if not connection:
        raise HTTPException(status_code=409, detail="Bumpa is not connected")
    enforce_operation_rate_limit(
        settings,
        operation="bumpa-sync",
        scopes={"tenant": principal.tenant.id},
        limit=settings.bumpa_sync_rate_limit,
        window_seconds=settings.bumpa_sync_rate_limit_window_seconds,
    )
    if not settings.is_local:
        request_key = _request_idempotency_key(idempotency_key)
        job_payload = {
            "tenant_id": principal.tenant.id,
            "connection_id": connection.id,
            "date_from": payload.date_from.isoformat(),
            "date_to": payload.date_to.isoformat(),
        }
        job, created = enqueue_job(
            db,
            kind="bumpa.sync",
            tenant_id=principal.tenant.id,
            payload=job_payload,
            idempotency_key=f"bumpa:{principal.tenant.id}:{request_key}",
            max_attempts=5,
        )
        if not created and job.payload != job_payload:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different sync request",
            )
        db.commit()
        response.status_code = status.HTTP_202_ACCEPTED
        return {
            "status": "queued",
            "job_id": job.id,
            "duplicate": not created,
            "requested_from": payload.date_from,
            "requested_to": payload.date_to,
        }
    result = run_sync(
        db,
        tenant_id=principal.tenant.id,
        connection=connection,
        date_from=payload.date_from,
        date_to=payload.date_to,
        field_encryption_key=settings.field_encryption_key,
        runtime_backend=settings.bumpa_backend,
    )
    return _run_view(result)


@router.post("/sync/latest")
def sync_latest(
    response: Response,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    today = date.today()
    return sync(
        SyncRequest(date_from=today - timedelta(days=29), date_to=today),
        response,
        principal,
        db,
        settings,
        idempotency_key,
    )


@router.get("/sync-runs")
def sync_runs(
    principal: Principal = Depends(require_tenant), db: Session = Depends(get_db)
) -> list[dict]:
    assert principal.tenant is not None
    rows = db.scalars(
        select(BumpaSyncRun)
        .where(BumpaSyncRun.tenant_id == principal.tenant.id)
        .order_by(BumpaSyncRun.started_at.desc())
        .limit(50)
    ).all()
    return [_run_view(row) for row in rows]


def _run_view(row: BumpaSyncRun) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "requested_from": row.requested_from,
        "requested_to": row.requested_to,
        "dataset_results": row.dataset_results,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error": row.error,
    }


def _request_idempotency_key(value: str | None) -> str:
    if value is None:
        return secrets.token_urlsafe(24)
    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must contain 1 to 120 characters",
        )
    if any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must contain visible ASCII characters only",
        )
    return normalized
