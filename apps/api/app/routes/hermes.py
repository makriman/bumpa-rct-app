from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant
from app.db.models import HermesProfile
from app.db.session import get_db
from app.providers.hermes import HermesClient, HermesError, endpoint_for

router = APIRouter(prefix="/hermes", tags=["agent-runtime"])


@router.get("/profile")
def profile(principal: Principal = Depends(require_tenant), db: Session = Depends(get_db)) -> dict:
    assert principal.tenant is not None
    row = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == principal.tenant.id))
    if not row:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    return {
        "id": row.id,
        "profile_name": row.profile_name,
        "provider": row.provider,
        "status": row.status,
    }


@router.get("/profile/readiness")
def profile_readiness(
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    assert principal.tenant is not None
    row = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == principal.tenant.id))
    if not row:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    if row.provider == "local" and settings.agent_backend == "mock":
        return {"status": "ready", "provider": "local", "latency_ms": 0}
    if settings.agent_backend != "hermes" or row.provider != "hermes":
        raise HTTPException(status_code=503, detail="Agent service is not configured")
    try:
        readiness = HermesClient(settings).readiness(endpoint_for(row, settings))
    except (HermesError, ValueError) as exc:
        row.status = "degraded"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Agent profile is temporarily unavailable",
        ) from exc
    row.status = "active" if readiness.ready else "degraded"
    db.commit()
    if not readiness.ready:
        raise HTTPException(status_code=503, detail="Agent profile is degraded")
    return {
        "status": "ready",
        "provider": "hermes",
        "latency_ms": readiness.latency_ms,
    }
