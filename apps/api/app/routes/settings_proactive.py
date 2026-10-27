"""Self-service opt-in settings for optional business summaries."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant_admin
from app.db.session import get_db
from app.services.audit import audit
from app.services.proactive_insights import (
    SubscriptionConflict,
    SubscriptionDenied,
    subscription_settings,
    update_subscription,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class ProactiveSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence: Literal["daily", "weekly"]
    status: Literal["active", "paused"]
    expected_revision: int = Field(ge=0, strict=True)
    phone_identity_id: str | None = Field(default=None, max_length=36)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    local_hour: int = Field(default=8, ge=0, le=23, strict=True)
    weekday: int = Field(default=0, ge=0, le=6, strict=True)


@router.get("/proactive-insights")
def get_proactive_settings(
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    assert principal.tenant is not None
    try:
        return subscription_settings(
            db, tenant_id=principal.tenant.id, user_id=principal.user.id, settings=settings
        )
    except SubscriptionDenied as exc:
        raise HTTPException(403, str(exc)) from exc


@router.put("/proactive-insights")
def put_proactive_settings(
    payload: ProactiveSettingsUpdate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    assert principal.tenant is not None
    try:
        result = update_subscription(
            db,
            tenant_id=principal.tenant.id,
            user_id=principal.user.id,
            settings=settings,
            **payload.model_dump(),
        )
    except SubscriptionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except SubscriptionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="proactive_subscription.updated",
        resource_type="proactive_subscription",
        resource_id=principal.user.id,
        after={"cadence": payload.cadence, "status": payload.status},
    )
    db.commit()
    return result
