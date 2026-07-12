from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.dependencies import Principal, require_operator
from app.core.security import normalize_phone
from app.db.models import (
    AuditLog,
    BumpaConnection,
    BumpaSyncRun,
    HermesProfile,
    PhoneIdentity,
    SystemError,
    Tenant,
    TenantMembership,
    UsageEvent,
    User,
)
from app.db.session import get_db
from app.providers.local import local_profile_key
from app.schemas import BumpaConnectionCreate, PhoneCreate, TenantCreate, TenantUpdate, UserCreate
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tenants")
def list_tenants(
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict]:
    rows = db.scalars(select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)).all()
    return [_tenant_view(row) for row in rows]


@router.post("/tenants", status_code=201)
def create_tenant(
    payload: TenantCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = Tenant(**payload.model_dump())
    db.add(tenant)
    audit(
        db,
        actor_user_id=principal.user.id,
        action="tenant.created",
        resource_type="tenant",
        resource_id=tenant.id,
        after={"slug": tenant.slug, "name": tenant.name},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tenant slug already exists") from exc
    return _tenant_view(tenant)


@router.get("/tenants/{tenant_id}")
def get_tenant(
    tenant_id: str,
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    return _tenant_view(tenant)


@router.patch("/tenants/{tenant_id}")
def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    before = {"name": tenant.name, "status": tenant.status}
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(tenant, key, value)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.updated",
        resource_type="tenant",
        resource_id=tenant.id,
        before=before,
        after=payload.model_dump(exclude_none=True),
    )
    db.commit()
    return _tenant_view(tenant)


@router.post("/tenants/{tenant_id}/users", status_code=201)
def create_user(
    tenant_id: str,
    payload: UserCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    phone = normalize_phone(payload.phone_e164)
    user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
    if not user:
        user = User(
            name=payload.name,
            primary_phone_e164=phone,
            email=str(payload.email) if payload.email else None,
        )
        db.add(user)
        db.flush()
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id
        )
    )
    if membership:
        raise HTTPException(status_code=409, detail="User already belongs to this tenant")
    membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role=payload.role)
    db.add(membership)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.user.added",
        resource_type="membership",
        resource_id=membership.id,
        after={"user_id": user.id, "role": payload.role},
    )
    db.commit()
    return {"user_id": user.id, "membership_id": membership.id, "role": membership.role}


@router.post("/tenants/{tenant_id}/phones", status_code=201)
def create_phone(
    tenant_id: str,
    payload: PhoneCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    member = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == payload.user_id
        )
    )
    if not member:
        raise HTTPException(status_code=422, detail="User is not a tenant member")
    identity = PhoneIdentity(
        tenant_id=tenant.id,
        user_id=payload.user_id,
        phone_e164=normalize_phone(payload.phone_e164),
        label=payload.label,
    )
    db.add(identity)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="phone.approved",
        resource_type="phone_identity",
        resource_id=identity.id,
    )
    db.commit()
    return {"id": identity.id, "status": identity.status}


@router.post("/tenants/{tenant_id}/bumpa")
def connect_bumpa(
    tenant_id: str,
    payload: BumpaConnectionCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    tenant = _tenant(db, tenant_id)
    if not settings.is_local and payload.provider == "local":
        raise HTTPException(
            status_code=422,
            detail="Local Bumpa provider is forbidden in production",
        )
    connection = db.scalar(select(BumpaConnection).where(BumpaConnection.tenant_id == tenant.id))
    encrypted = FieldCipher(settings.field_encryption_key).encrypt(payload.api_key)
    if connection:
        connection.encrypted_api_key = encrypted
        connection.scope_type = payload.scope_type
        connection.scope_id = payload.scope_id
        connection.provider = payload.provider
        connection.status = "active"
    else:
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=encrypted,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            provider=payload.provider,
        )
        db.add(connection)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.bumpa_connection.saved",
        resource_type="bumpa_connection",
        resource_id=connection.id,
        after={
            "scope_type": payload.scope_type,
            "scope_id_last4": payload.scope_id[-4:],
            "provider": payload.provider,
        },
    )
    db.commit()
    return {"id": connection.id, "status": connection.status, "provider": connection.provider}


@router.post("/tenants/{tenant_id}/hermes-profile")
def create_profile(
    tenant_id: str,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    tenant = _tenant(db, tenant_id)
    if settings.agent_backend != "mock":
        raise HTTPException(
            status_code=503,
            detail="Hermes profile provisioning is not configured yet",
        )
    existing = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == tenant.id))
    if existing:
        return {"id": existing.id, "profile_name": existing.profile_name, "status": existing.status}
    profile = HermesProfile(
        tenant_id=tenant.id,
        profile_name=f"tenant_{tenant.slug.replace('-', '_')}_{tenant.id[:8]}",
        provider="local",
        api_internal_url="local://agent",
        encrypted_api_key=FieldCipher(settings.field_encryption_key).encrypt(local_profile_key()),
    )
    db.add(profile)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="hermes.profile.created",
        resource_type="hermes_profile",
        resource_id=profile.id,
        after={"profile_name": profile.profile_name, "provider": "local"},
    )
    db.commit()
    return {"id": profile.id, "profile_name": profile.profile_name, "status": profile.status}


@router.get("/system/errors")
def system_errors(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(select(SystemError).order_by(SystemError.created_at.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "service": row.service,
            "severity": row.severity,
            "message": row.message,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/system/sync-runs")
def all_sync_runs(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(
        select(BumpaSyncRun).order_by(BumpaSyncRun.started_at.desc()).limit(100)
    ).all()
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "status": row.status,
            "started_at": row.started_at,
            "error": row.error,
        }
        for row in rows
    ]


@router.get("/usage")
def usage(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(select(UsageEvent).order_by(UsageEvent.created_at.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "event_name": row.event_name,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/audit")
def audits(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def _tenant(db: Session, tenant_id: str) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def _tenant_view(tenant: Tenant) -> dict:
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
        "created_at": tenant.created_at,
    }
