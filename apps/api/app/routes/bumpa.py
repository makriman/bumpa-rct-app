from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import Principal, require_tenant, require_tenant_admin
from app.db.models import BumpaConnection, BumpaSyncRun
from app.db.session import get_db
from app.schemas import SyncRequest
from app.services.bumpa import run_sync

router = APIRouter(prefix="/bumpa", tags=["bumpa"])


@router.post("/sync")
def sync(
    payload: SyncRequest,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    assert principal.tenant is not None
    connection = db.scalar(
        select(BumpaConnection).where(BumpaConnection.tenant_id == principal.tenant.id)
    )
    if not connection:
        raise HTTPException(status_code=409, detail="Bumpa is not connected")
    result = run_sync(
        db,
        tenant_id=principal.tenant.id,
        connection=connection,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    return _run_view(result)


@router.post("/sync/latest")
def sync_latest(
    principal: Principal = Depends(require_tenant_admin), db: Session = Depends(get_db)
) -> dict:
    today = date.today()
    return sync(SyncRequest(date_from=today - timedelta(days=29), date_to=today), principal, db)


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
