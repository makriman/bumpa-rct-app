from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import Principal, require_tenant, require_tenant_admin
from app.db.models import ResearchConsent
from app.db.session import get_db
from app.schemas import ConsentUpdate, TenantUpdate
from app.services.audit import audit

router = APIRouter(prefix="/tenants", tags=["tenants"])


def tenant_view(principal: Principal) -> dict:
    tenant = principal.tenant
    assert tenant is not None
    return {
        "id": tenant.id,
        "slug": tenant.slug,
        "name": tenant.name,
        "status": tenant.status,
        "business_category": tenant.business_category,
        "country": tenant.country,
        "city": tenant.city,
        "timezone": tenant.timezone,
        "currency_code": tenant.currency_code,
        "research_consent_status": tenant.research_consent_status,
        "role": principal.membership.role if principal.membership else None,
    }


@router.get("/current")
def current_tenant(principal: Principal = Depends(require_tenant)) -> dict:
    return tenant_view(principal)


@router.patch("/current")
def update_tenant(
    payload: TenantUpdate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    tenant = principal.tenant
    assert tenant is not None
    before = {"name": tenant.name, "city": tenant.city, "timezone": tenant.timezone}
    for key, value in payload.model_dump(exclude_none=True, exclude={"status"}).items():
        setattr(tenant, key, value)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.updated",
        resource_type="tenant",
        resource_id=tenant.id,
        before=before,
        after=payload.model_dump(exclude_none=True, exclude={"status"}),
    )
    db.commit()
    return tenant_view(principal)


@router.post("/current/research-consent")
def update_consent(
    payload: ConsentUpdate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    tenant = principal.tenant
    assert tenant is not None
    before = tenant.research_consent_status
    tenant.research_consent_status = payload.status
    db.add(
        ResearchConsent(
            tenant_id=tenant.id,
            status=payload.status,
            policy_version=payload.policy_version,
            actor_user_id=principal.user.id,
        )
    )
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="research.consent.changed",
        resource_type="tenant",
        resource_id=tenant.id,
        before={"status": before},
        after={"status": payload.status, "policy_version": payload.policy_version},
    )
    db.commit()
    return {"status": tenant.research_consent_status}
