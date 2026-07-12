from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import Principal, require_tenant
from app.db.models import HermesProfile
from app.db.session import get_db

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
